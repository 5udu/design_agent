import sqlite3
from contextlib import closing
from pathlib import Path
import pytest
from auth_helpers import signed_in_client
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.agent.graph import build_graph
from app.agent.instructions import SYSTEM
from app.context.builder import ContextBuilder
from app.persistence.locking import LockBusy, project_lock, preview_lock
from app.persistence.projects import delete_project, collect_garbage
from app.persistence.repositories import Store
from app.runtime.worker import process_run
from app.tools.registry import TOOL_SCHEMAS
from test_runtime import setup, call, Model, response


async def test_delete_cascades_records_checkpoints_and_unshared_images(tmp_path):
    config,store,_,run,_,gateway=setup(tmp_path)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.db')) as cp:
        graph=build_graph(store,Model([response(call()),response()]),gateway,config,cp)
        await process_run(graph,store,run,config)
    asset=store.list('assets')[0]
    previews=list((tmp_path/'previews').glob('*.image'))
    assert previews and (tmp_path/asset['path']).is_file()
    store.put('projects',{'id':'other','name':'另一个项目'})
    other=store.put('runs',{'id':'other-run','project_id':'other','status':'running','message':'读项目'})
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.db')) as cp:
        graph=build_graph(store,Model([response()]),gateway,config,cp)
        await process_run(graph,store,other,config)
    result=delete_project(store,'project')
    assert result['deleted'] and not result['cleanup_pending']
    assert not (tmp_path/asset['path']).exists() and all(not path.exists() for path in previews)
    assert store.get('projects','project') is None and store.get('projects','other')
    assert store.list('assets')==[] and store.list('versions')==[] and store.tool_calls(run['id'])==[]
    assert store.session_events(run['session_id'])==[] and store.get('runs',run['id']) is None
    with closing(sqlite3.connect(tmp_path/'checkpoints.db')) as db:
        for table in ('checkpoints','writes'):
            assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE thread_id=?',(run['id'],)).fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM checkpoints WHERE thread_id=?',(other['id'],)).fetchone()[0]>0


@pytest.mark.parametrize('status',['queued','running','waiting_user'])
def test_active_project_cannot_be_deleted(tmp_path,status):
    _,store,_,run,_,_=setup(tmp_path)
    store.update_run(run['id'],status=status)
    before=store.get('runs',run['id'])
    with pytest.raises(ValueError,match='请先完成或结束任务'):
        delete_project(store,'project')
    assert store.get('runs',run['id'])==before and store.get('projects','project')


def test_delete_waits_until_worker_has_finished_writing_checkpoint(tmp_path):
    _,store,_,run,_,_=setup(tmp_path)
    store.update_run(run['id'],status='succeeded')
    with project_lock(tmp_path,'project'):
        with pytest.raises(LockBusy):
            delete_project(store,'project')
    assert store.get('projects','project')


async def test_shared_original_and_preview_remain_until_last_project_is_removed(tmp_path):
    config,store,_,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call())
    asset=created['artifact']
    builder=ContextBuilder(store,Model([]),gateway.assets,config,TOOL_SCHEMAS)
    builder.observe(run,asset['id'],key='first-image')
    store.update_run(run['id'],status='succeeded')
    store.put('projects',{'id':'other','name':'共享图片的项目'})
    other=store.put('runs',{'id':'other-run','project_id':'other','status':'succeeded','message':'继续'})
    copied=store.put('assets',{**asset,'id':'shared','project_id':'other'})
    builder.observe(other,copied['id'],key='second-image')
    preview=next((tmp_path/'previews').glob('*.image'))
    delete_project(store,'project')
    assert (tmp_path/asset['path']).is_file() and preview.is_file()
    delete_project(store,'other')
    assert not (tmp_path/asset['path']).exists() and not preview.exists()


@pytest.mark.parametrize('path',['application.db','../outside.png'],ids=['database','outside'])
def test_unsafe_asset_path_rolls_back_deletion(tmp_path,path):
    _,store,_,run,_,_=setup(tmp_path)
    store.update_run(run['id'],status='succeeded')
    store.put('assets',{'id':'bad','project_id':'project','path':path})
    with pytest.raises(ValueError,match='图片路径异常'):
        delete_project(store,'project')
    assert store.get('projects','project') and store.get('assets','bad')


async def test_busy_file_is_cleaned_on_restart_without_restoring_deleted_project(tmp_path,monkeypatch):
    _,store,_,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call())
    path=(tmp_path/created['artifact']['path']).resolve()
    store.update_run(run['id'],status='succeeded')
    unlink=Path.unlink
    def busy_file(self,*args,**kwargs):
        if self==path:
            raise PermissionError('file busy')
        return unlink(self,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',busy_file)
        assert delete_project(store,'project')['cleanup_pending']
        assert path.is_file() and store.get('projects','project') is None
    restored=Store(tmp_path)
    assert not path.exists() and restored.list('projects')==[]


def test_deleted_project_cannot_be_recreated_by_a_delayed_enqueue(tmp_path):
    _,store,_,run,_,_=setup(tmp_path)
    store.update_run(run['id'],status='succeeded')
    delete_project(store,'project')
    with pytest.raises(KeyError):
        store.enqueue({**run,'id':'late','status':'queued'},{},SYSTEM)
    with pytest.raises(KeyError):
        store.ensure_session('project')
    assert store.list('runs')==[]
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0


async def test_other_project_preview_write_defers_files_without_blocking_record_deletion(tmp_path):
    _,store,_,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call())
    path=tmp_path/created['artifact']['path']
    store.update_run(run['id'],status='succeeded')
    with preview_lock(tmp_path):
        assert delete_project(store,'project')['cleanup_pending']
        assert store.get('projects','project') is None and path.is_file()
    assert collect_garbage(store)==0 and not path.exists()


def test_delete_api_and_empty_project_list_survive_restart(tmp_path,monkeypatch):
    import app.main as server
    config,store,_,run,_,gateway=setup(tmp_path)
    store.update_run(run['id'],status='succeeded')
    monkeypatch.setattr(server,'store',store)
    monkeypatch.setattr(server,'assets',gateway.assets)
    monkeypatch.setattr(server,'settings',config)
    with signed_in_client(server) as client:
        assert client.delete('/api/projects/project').status_code==200
        assert client.delete('/api/projects/project').status_code==404
        assert client.get('/api/projects/project').status_code==404
        assert client.get('/api/runs/run').status_code==404
        assert client.get('/api/sessions/'+run['session_id']).status_code==404
    with signed_in_client(server) as client:
        assert client.get('/api/projects').json()==[]
