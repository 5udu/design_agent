import json
import sqlite3
import pytest
from auth_helpers import signed_in_client
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.agent.graph import build_graph
from app.agent.instructions import SYSTEM
from app.context.builder import ContextBuilder
from app.context.images import hydrate_images
from app.context.meter import estimate_tokens
from app.providers.deepseek import ContextWindowExceeded
from app.session.projection import derive_nodes, balanced_units
from app.tools.registry import TOOL_SCHEMAS
from app.runtime.worker import process_run
from test_runtime import setup, Model, response, call


def new_run(store, index, message='继续创作'):
    return store.put('runs', {'id': f'run-{index}', 'project_id': 'project',
                            'status': 'running', 'message': message, 'reasoning_effort': 'off'})


async def execute(store, gateway, config, model, run, path):
    async with AsyncSqliteSaver.from_conn_string(str(path)) as cp:
        graph = build_graph(store, model, gateway, config, cp)
        await process_run(graph, store, run, config)
        return graph


async def test_session_preserves_roles_and_requirements_beyond_six_runs(tmp_path):
    config, store, _, first, _, gateway = setup(tmp_path)
    model = Model([{'content': '收到，将保留标志。', 'reasoning_content': 'private-first'}]+[response() for _ in range(8)])
    store.update_run(first['id'], message='始终保留 boss 标志')
    await execute(store, gateway, config, model, first, tmp_path/'cp.db')
    for index in range(8):
        run = new_run(store, index)
        await execute(store, gateway, config, model, run, tmp_path/'cp.db')
    history = model.received[-1]
    assert any(m['role']=='user' and m['content']=='始终保留 boss 标志' for m in history)
    assert any(m['role']=='assistant' and m['content']=='收到，将保留标志。' for m in history)
    assert len({r['session_id'] for r in store.list('runs')})==1
    assert sum(m['role']=='system' for m in history)==1
    assert 'private-first' not in json.dumps(store.session_events(first['session_id']))


def add_history(store, builder, count=8, text='保留 boss 标志。'):
    for index in range(count):
        run = new_run(store, index, text+('较早的创作记录 abcde '*120))
        builder.ensure_run(run)
        store.append(run['id'], 'assistant_message', {'text':'已记录', 'phase':'final'},
                     model_data={'role':'assistant','content':'已记录'})
        store.update_run(run['id'], status='succeeded')


class Summarizer:
    def __init__(self, summary='用户要求保留 boss 标志；已记录较早的创作过程。', error=None):
        self.inputs=[]
        self.summary, self.error=summary, error

    async def summarize(self, messages, *, max_tokens):
        self.inputs.append(messages)
        if self.error:
            raise self.error
        return self.summary, {'model':'summary-test','usage':{'prompt_tokens':100}}


def compact_setup(tmp_path):
    config,store,_,first,_,gateway=setup(tmp_path)
    # Use a small pressure threshold, but a real summary request still fits.
    config.context_window_tokens=100000
    config.context_output_reserve=1000
    config.context_safety_tokens=500
    config.context_compact_ratio=0.05
    config.context_retain_tokens=300
    config.context_summary_max_tokens=256
    model=Summarizer()
    builder=ContextBuilder(store,model,gateway.assets,config,TOOL_SCHEMAS)
    store.update_run(first['id'],status='succeeded')
    add_history(store,builder)
    current=new_run(store,'current','把饭盒改成深蓝色，并保留标志')
    builder.ensure_run(current)
    return config,store,gateway,model,builder,current


async def test_automatic_compaction_preserves_original_trace_and_restarts(tmp_path):
    config,store,gateway,model,builder,run=compact_setup(tmp_path)
    original=store.session_events(run['session_id'])
    prepared=await builder.prepare(run,'current:1')
    assert model.inputs
    compacted=[e for e in store.session_events(run['session_id']) if e['kind']=='context_compacted']
    assert compacted and compacted[-1]['data']['estimated_tokens_after']<compacted[-1]['data']['estimated_tokens_before']
    assert store.session_events(run['session_id'],through=original[-1]['seq'])==original
    assert any('历史对话压缩摘要' in str(m.get('content')) for m in prepared.messages)
    assert any(m['content']==run['message'] for m in prepared.messages)
    from app.persistence.repositories import Store
    restored=Store(tmp_path)
    assert builder.nodes(run['session_id'])==ContextBuilder(restored,model,gateway.assets,config,TOOL_SCHEMAS).nodes(run['session_id'])
    request=next(e for e in restored.session_events(run['session_id']) if e['seq']==prepared.request_seq)
    reconstructed=[hydrate_images(m,tmp_path) for node in builder.nodes(run['session_id'],request['data']['through_seq']) for m in node.messages]
    assert reconstructed==prepared.messages


@pytest.mark.parametrize('summary,error', [('',None),('x'*100000,None),('unused',RuntimeError('summary offline'))], ids=['empty','expanding','offline'])
async def test_failed_or_expanding_compaction_keeps_active_history(tmp_path,summary,error):
    _,store,_,_,builder,run=compact_setup(tmp_path)
    builder.compactor.model=Summarizer(summary,error)
    before=builder.nodes(run['session_id'])
    await builder.prepare(run,'current:1')
    assert builder.nodes(run['session_id'])==before
    assert not any(e['kind']=='context_compacted' for e in store.session_events(run['session_id']))
    assert any(e['kind']=='context_compaction_finished' and e['data']['status']=='failed' for e in store.events(run['id']))


async def test_compaction_keeps_tool_batches_paired_and_hides_reasoning_from_summary(tmp_path):
    _,store,_,model,builder,run=compact_setup(tmp_path)
    store.update_run(run['id'],status='succeeded')
    old=new_run(store,'batch')
    builder.ensure_run(old)
    request=call('read','read_project',{})
    store.append(old['id'],'assistant_message',{'text':'读取项目','phase':'progress'},model_data={
        'role':'assistant','content':'读取项目','reasoning_content':'do-not-leak',
        'tool_calls':[{k:v for k,v in request.items() if k!='arguments'}]})
    store.append(old['id'],'tool_finished',{'name':'read_project','call_id':'read','result':{'status':'succeeded'}},
                 model_data={'role':'tool','tool_call_id':'read','content':'项目事实 '*300})
    store.update_run(old['id'],status='succeeded')
    store.update_run(run['id'],status='running')
    # Current context is fixed even though older fixture data was appended later.
    await builder.prepare(run,'current:1')
    units,pending=balanced_units(builder.nodes(run['session_id']))
    assert not pending
    for unit in units:
        calls={c['id'] for node in unit for m in node.messages for c in m.get('tool_calls',[])}
        results={m['tool_call_id'] for node in unit for m in node.messages if m['role']=='tool'}
        assert calls==results
    assert 'do-not-leak' not in json.dumps(model.inputs)


async def test_selected_reference_is_actual_input_and_old_image_stays_selected(tmp_path):
    config,store,_,run,_,gateway=setup(tmp_path)
    first=await gateway.execute(run,call('g1'))
    second=await gateway.execute(run,call('g2'))
    store.update_run(run['id'],status='succeeded')
    selected=new_run(store,'selected')
    selected=store.update_run(selected['id'],target_asset_id=first['artifact']['id'])
    builder=ContextBuilder(store,Summarizer(),gateway.assets,config,TOOL_SCHEMAS)
    builder.ensure_run(selected)
    observation=store.event_by_key(selected['session_id'],selected['id']+':reference',private=True)
    assert observation['data']['asset_id']==first['artifact']['id']
    assert observation['data']['asset_id']!=second['artifact']['id']
    assert observation['model_data']['content'][1]['type']=='image_ref'
    assert 'base64' not in json.dumps(observation)
    message=hydrate_images(observation['model_data'],tmp_path)
    assert message['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    tokens=estimate_tokens([observation['model_data']],[],tmp_path)
    assert tokens>256


async def test_accepted_model_result_replays_without_repeating_decision(tmp_path):
    config,store,_,run,images,gateway=setup(tmp_path)
    model=Model([response()])
    context=ContextBuilder(store,model,gateway.assets,config,TOOL_SCHEMAS)
    context.ensure_run(run)
    tool=call()
    store.append(run['id'],'assistant_message',{'text':'','phase':'progress'},key=run['id']+':1:assistant',
                 model_data=response(tool))
    await execute(store,gateway,config,model,run,tmp_path/'cp.db')
    assert store.get('runs',run['id'])['status']=='succeeded'
    assert len(model.received)==1 and images.calls==1


async def test_cancel_waiting_run_closes_tool_protocol_before_next_run(tmp_path):
    config,store,_,run,_,gateway=setup(tmp_path)
    model=Model([response(call('ask','ask_user',{'question':'选择颜色','options':['蓝色']})),response()])
    await execute(store,gateway,config,model,run,tmp_path/'cp.db')
    store.cancel(run['id'])
    next_run=new_run(store,'after-cancel')
    await execute(store,gateway,config,model,next_run,tmp_path/'cp.db')
    assert store.get('runs',next_run['id'])['status']=='succeeded'
    history=model.received[-1]
    assert any(m['role']=='tool' and m['tool_call_id']=='ask' for m in history)


def test_session_cursor_snapshot_and_private_payload_api(tmp_path,monkeypatch):
    import app.main as server
    config,store,_,run,_,gateway=setup(tmp_path)
    store.append(run['id'],'assistant_message',{'text':'公开说明','phase':'progress'},
                 model_data={'role':'assistant','content':'公开说明','reasoning_content':'private-secret'})
    monkeypatch.setattr(server,'store',store)
    monkeypatch.setattr(server,'assets',gateway.assets)
    monkeypatch.setattr(server,'settings',config)
    with signed_in_client(server) as client:
        snapshot=client.get('/api/sessions/'+run['session_id']).json()
        store.append(run['id'],'test',{'value':1})
        delta=client.get('/api/sessions/'+run['session_id'],params={'after':snapshot['cursor']}).json()
        assert len(delta['events'])==1 and delta['events'][0]['seq']>snapshot['cursor']
        assert 'private-secret' not in json.dumps(snapshot)
        assert 'model_data' not in json.dumps(client.get('/api/runs/'+run['id']).json())
        assert '/api/runs/{run_id}/events' not in client.get('/openapi.json').json()['paths']


def test_duplicate_event_is_idempotent_and_session_sequence_is_contiguous(tmp_path):
    _,store,_,run,_,_=setup(tmp_path)
    first=store.append(run['id'],'test',{'x':1},key='same')
    repeated=store.append(run['id'],'test',{'x':1},key='same')
    store.update_run(run['id'],status='succeeded')
    other=new_run(store,'second')
    store.append(other['id'],'test',{'x':2})
    assert first==repeated
    assert [e['seq'] for e in store.session_events(run['session_id'])]==[1,2]


async def test_tool_state_and_result_event_rollback_together(tmp_path,monkeypatch):
    _,store,_,run,_,gateway=setup(tmp_path)
    record={'id':run['id']+':x','run_id':run['id'],'name':'read_project','status':'succeeded','result':{}}
    def fail(*args,**kwargs):
        raise RuntimeError('journal failure')
    monkeypatch.setattr(store,'append',fail)
    with pytest.raises(RuntimeError):
        store.save_tool(record,'tool_finished',{'call_id':'x'})
    assert store.get('tool_runs',record['id']) is None


def test_legacy_migration_imports_summary_without_inventing_tool_history(tmp_path):
    from app.persistence.repositories import Store
    with sqlite3.connect(tmp_path/'application.db') as db:
        db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE events(id INTEGER PRIMARY KEY,run_id TEXT,kind TEXT,data TEXT,created REAL);''')
        db.execute('INSERT INTO projects VALUES(?,?)',('p',json.dumps({'id':'p','name':'旧项目'})))
        run={'id':'old','project_id':'p','status':'succeeded','message':'旧要求','summary':'旧摘要','conversation_context':[]}
        db.execute('INSERT INTO runs VALUES(?,?,?,?)',('old','p','succeeded',json.dumps(run)))
        db.execute('INSERT INTO events VALUES(1,?,?,?,1)',('old','assistant_message',json.dumps({'text':'旧摘要'})))
    store=Store(tmp_path)
    run=store.get('runs','old')
    assert 'conversation_context' not in run
    events=store.session_events(run['session_id'],private=True)
    legacy=next(e for e in events if e['kind']=='legacy_history')
    assert not legacy['data']['restored_from_checkpoint']
    assert legacy['model_data']==[{'role':'assistant','content':'旧摘要'}]
    assert next(e for e in events if e['kind']=='assistant_message')['created_at']==1
    assert Store(tmp_path).session_events(run['session_id'])==store.session_events(run['session_id'])
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone()


async def test_overflow_recovery_compacts_once_then_retries(tmp_path):
    config,store,gateway,summary,builder,run=compact_setup(tmp_path)
    config.context_compact_ratio=0.99
    class OverflowModel(Summarizer):
        def __init__(self):
            super().__init__()
            self.calls=0
        async def complete(self,messages,tools,*,reasoning_effort=None):
            self.calls+=1
            if self.calls==1:
                raise ContextWindowExceeded('overflow')
            return {'content':'已继续完成。'},{}
    model=OverflowModel()
    await execute(store,gateway,config,model,run,tmp_path/'cp.db')
    assert model.calls==2 and model.inputs
    assert store.get('runs',run['id'])['status']=='succeeded'


async def test_a_single_oversized_input_fails_without_silent_truncation(tmp_path):
    config,store,_,run,_,gateway=setup(tmp_path)
    config.context_window_tokens=5000
    config.context_output_reserve=500
    config.context_safety_tokens=100
    store.update_run(run['id'],message='保留的原始需求 '*3000)
    model=Model([])
    await execute(store,gateway,config,model,run,tmp_path/'cp.db')
    assert not model.received
    assert store.get('runs',run['id'])['status']=='failed'
    assert any(e['kind']=='user_message' and len(e['data']['text'])>10000 for e in store.events(run['id']))


async def test_legacy_checkpoint_with_pending_question_resumes_new_graph(tmp_path):
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END
    config,store,_,run,images,gateway=setup(tmp_path)
    question=call('legacy-ask','ask_user',{'question':'选颜色','options':['蓝色']})
    class OldState(TypedDict):
        run_id: str
        messages: list
        turns: int
        calls: list
        finished: bool
    async def old_decide(state):
        return {'messages':state['messages']+[response(question)],'calls':response(question)['tool_calls'],'turns':1}
    async def old_act(state):
        await gateway.execute(run,question)
        return {'calls':[]}
    old=StateGraph(OldState)
    old.add_node('decide',old_decide)
    old.add_node('act',old_act)
    old.add_edge(START,'decide')
    old.add_edge('decide','act')
    old.add_edge('act',END)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.db')) as cp:
        interrupted=await old.compile(checkpointer=cp).ainvoke({'run_id':run['id'], 'messages':[
            {'role':'system','content':SYSTEM},{'role':'user','content':run['message']}],
            'turns':0,'calls':[],'finished':False},{'configurable':{'thread_id':run['id']}})
        assert interrupted.get('__interrupt__')
    store.update_run(run['id'],status='waiting_user',question={'question':'选颜色'})
    with store.connect() as db:
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,run_id TEXT,kind TEXT,data TEXT,created REAL)')
        db.execute('INSERT INTO events SELECT id,run_id,kind,data,created FROM session_events')
        db.execute('DELETE FROM session_events')
        db.execute('DELETE FROM sessions')
    from app.persistence.repositories import Store
    restored=Store(tmp_path)
    resumed=restored.resume(run['id'],'蓝色')
    model=Model([response()])
    restored_gateway=type(gateway)(restored,images,config)
    await execute(restored,restored_gateway,config,model,resumed,tmp_path/'checkpoints.db')
    assert restored.get('runs',run['id'])['status']=='succeeded'
    assert images.calls==0
    assert any(m['role']=='tool' and '蓝色' in m['content'] for m in model.received[-1])


async def test_legacy_question_without_checkpoint_continues_from_saved_answer(tmp_path):
    config,store,_,run,images,gateway=setup(tmp_path)
    store.update_run(run['id'],status='waiting_user',question={'question':'选什么风格？'})
    resumed=store.resume(run['id'],'国潮风格')
    model=Model([response()])
    await execute(store,gateway,config,model,resumed,tmp_path/'cp.db')
    assert store.get('runs',run['id'])['status']=='succeeded'
    assert any('国潮风格' in str(m['content']) and '选什么风格' in str(m['content']) for m in model.received[-1])
    assert images.calls==0


async def test_lost_checkpoint_after_image_and_question_recovers_without_rebilling(tmp_path):
    config,store,_,run,images,gateway=setup(tmp_path)
    batch=response(call('image'))
    batch['tool_calls'].extend(response(call('ask','ask_user',{'question':'选什么颜色？'}))['tool_calls'])
    model=Model([batch])
    await execute(store,gateway,config,model,run,tmp_path/'lost-cp.db')
    assert store.get('runs',run['id'])['status']=='waiting_user' and images.calls==1
    resumed=store.resume(run['id'],'深蓝色')
    continuation=Model([response()])
    await execute(store,gateway,config,continuation,resumed,tmp_path/'new-cp.db')
    assert store.get('runs',run['id'])['status']=='succeeded' and images.calls==1
    assert len(continuation.received)==1
    assert not balanced_units(derive_nodes(store.session_events(run['session_id'],private=True)))[1]
    assert any('深蓝色' in str(m['content']) for m in continuation.received[0])
    assert store.event_by_key(run['session_id'],run['id']+':2:assistant')
