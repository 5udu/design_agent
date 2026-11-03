import asyncio
import json
import sqlite3
import time
from typing import get_args
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, settings
from app.domain.models import ProjectCreate, RunCreate, ResumeInput, ReasoningEffort
from app.persistence.repositories import Store, uid
from app.persistence.assets import Assets
from app.persistence.projects import delete_project as remove_project
from app.persistence.locking import LockBusy
from app.agent.instructions import SYSTEM
from app.domain.projects import project_facts

store = Store(settings.data_dir)
assets = Assets(store, settings)


app = FastAPI(title='图像生成与编辑 Agent', version='0.2.0')


def require(table, key):
    item = store.get(table, key)
    if not item:
        raise HTTPException(404, '记录不存在')
    return item


@app.get('/api/health')
def health():
    return {'status': 'ok', 'models': {'agent': settings.deepseek_model, 'image': settings.seedream_model},
            'agent_settings': {'thinking': 'disabled' if settings.deepseek_reasoning_effort == 'off' else 'enabled',
                               'reasoning_effort': settings.deepseek_reasoning_effort,
                               'supported_reasoning_efforts': list(get_args(ReasoningEffort))},
            'credentials': {'agent_configured': bool(settings.deepseek_api_key), 'image_configured': bool(settings.seedream_api_key)},
            'capabilities': ['product_image_generation','reference_image_editing','session_history','agent_trace','context_compaction','project_deletion'],
            'pending': ['broader_image_quality_evaluation']}


@app.get('/api/projects')
def projects():
    return store.list('projects')


@app.post('/api/projects', status_code=201)
def create_project(body: ProjectCreate):
    return store.put('projects', {'id': uid(), **body.model_dump(), 'created_at': time.time()})


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    try:
        return remove_project(store, project_id)
    except KeyError:
        raise HTTPException(404, '项目不存在或已删除') from None
    except (ValueError, LockBusy) as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/api/projects/{project_id}')
def project_detail(project_id: str,include_events:bool=False):
    project = require('projects', project_id)
    try:
        session = store.ensure_session(project_id)
    except KeyError:
        raise HTTPException(404, '项目不存在或已删除') from None
    cursor = store.cursor(session['id'])
    result={'project': project, 'session': session, 'cursor': cursor, 'assets': store.list('assets', project_id), 'versions': store.list('versions', project_id), 'runs': store.list('runs', project_id)}
    if include_events:
        events = store.session_events(session['id'], through=cursor)
        result['conversations']=[{'run':r,'events':[e for e in events if e['run_id']==r['id']]} for r in result['runs']]
    return result


@app.post('/api/projects/{project_id}/runs', status_code=202)
def create_run(project_id: str, body: RunCreate):
    project=require('projects', project_id)
    reference=body.target_asset_id
    if reference is None:reference=project.get('current_image_id')
    if reference is None:
        images=store.list('assets',project_id)
        if images:reference=images[-1]['id']
    target=None
    if reference:
        try:asset,_=assets.get(project_id,reference);target=asset.get('version_id')
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    active = [r for r in store.list('runs', project_id) if r['status'] in {'queued', 'running', 'waiting_user'}]
    if active:
        raise HTTPException(409, '当前项目已有进行中的任务，请先继续或结束它')
    run = {'id': uid(), 'project_id': project_id, 'message': body.message,'target_asset_id':reference,'working_version_id':target,
           'reasoning_effort': body.reasoning_effort if body.reasoning_effort is not None else settings.deepseek_reasoning_effort,
           'status': 'queued', 'quality_status': 'unverified', 'created_at': time.time()}
    try:
        run = store.enqueue(run, {'project': project_facts(project), 'target_asset_id': reference}, SYSTEM)
    except sqlite3.IntegrityError:
        raise HTTPException(409, '当前项目已有进行中的任务') from None
    except KeyError:
        raise HTTPException(404, '项目不存在或已删除') from None
    return run


@app.get('/api/runs/{run_id}')
def run_detail(run_id: str):
    return {'run': require('runs', run_id), 'events': store.events(run_id), 'tool_runs': store.tool_calls(run_id)}


@app.post('/api/runs/{run_id}/resume', status_code=202)
def resume_run(run_id: str, body: ResumeInput):
    require('runs', run_id)
    try:
        return store.resume(run_id, body.answer)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post('/api/runs/{run_id}/cancel')
def cancel_run(run_id: str):
    require('runs', run_id)
    try:
        return store.cancel(run_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/api/sessions/{session_id}')
def session_detail(session_id: str, after: int = 0):
    with store.connect() as db:
        row = db.execute('SELECT data FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, '对话不存在')
    cursor = store.cursor(session_id)
    return {'session': json.loads(row['data']), 'cursor': cursor,
            'events': store.session_events(session_id, after, through=cursor)}


@app.get('/api/sessions/{session_id}/events')
async def events(session_id: str, request: Request, after: int = 0):
    session = session_detail(session_id, after)['session']
    last = request.headers.get('last-event-id', '')
    cursor = max(after, int(last) if last.isdigit() else 0)

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            if not store.get('projects', session['project_id']):
                yield 'event: project_deleted\ndata: '+json.dumps({'project_id': session['project_id']})+'\n\n'
                return
            for event in store.session_events(session_id, cursor):
                cursor = event['seq']
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.6)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.get('/api/assets/{asset_id}/file')
def asset_file(asset_id: str,download:bool=False):
    asset = require('assets', asset_id)
    path = (settings.data_dir / asset['path']).resolve()
    if not path.is_relative_to(settings.data_dir.resolve()) or not path.is_file():
        raise HTTPException(404, '图片文件不存在')
    return FileResponse(path, media_type='image/png',filename=f'{asset_id}.png' if download else None)


dist = ROOT / 'frontend' / 'dist'
if dist.is_dir():
    app.mount('/', StaticFiles(directory=dist, html=True), name='studio')
