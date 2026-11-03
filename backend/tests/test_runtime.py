import io
import json
import pytest
from PIL import Image
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.config import Settings
from app.persistence.repositories import Store
from app.runtime.gateway import Gateway
from app.agent.graph import build_graph
from app.agent.observations import image_message
from app.runtime.worker import process_run


class Images:
    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, reference=None, size=None):
        self.calls += 1
        output = io.BytesIO()
        Image.new('RGB', (40, 60), '#d5d9b4').save(output, format='PNG')
        return output.getvalue(), {'model':'test-image', 'usage':{}}


def setup(tmp_path, **kwargs):
    config = Settings(_env_file=None, data_dir=tmp_path, **kwargs)
    store = Store(tmp_path)
    project = store.put('projects', {'id':'project', 'name':'test', 'brand':'demo', 'approved_copy':['100 g']})
    run = store.put('runs', {'id':'run', 'project_id':'project', 'status':'running', 'message':'做一张概念图'})
    images = Images()
    return config, store, project, run, images, Gateway(store, images, config)


def call(id='g1', name='generate_image', args=None):
    return {'id':id, 'type':'function', 'function':{'name':name, 'arguments':json.dumps(args or {'prompt':'tea', 'direction':'green'})}, 'arguments':args or {'prompt':'tea','direction':'green'}}


async def test_tool_result_replay_never_rebills(tmp_path):
    config, store, project, run, images, gateway = setup(tmp_path)
    first = await gateway.execute(run, call())
    second = await gateway.execute(run, call())
    assert first == second and images.calls == 1
    assert first['quality_status'] == 'visual_review_required'
    observation, digest = image_message(first['artifact'], gateway.assets)
    assert observation['content'][1]['type'] == 'image_ref'
    assert (tmp_path/observation['content'][1]['path']).is_file()


async def test_image_budget_counts_submissions(tmp_path):
    config, store, project, run, images, gateway = setup(tmp_path, max_image_calls=1)
    await gateway.execute(run, call())
    second = await gateway.execute(run, call('g2'))
    assert second['status'] == 'failed' and images.calls == 1


async def test_unknown_submission_not_retried(tmp_path):
    config, store, project, run, images, gateway = setup(tmp_path)
    store.put('tool_runs', {'id':'run:g1','run_id':'run','name':'generate_image','status':'submitted','submitted_at':1})
    result = await gateway.execute(run, call())
    assert result['error_code'] == 'unknown_submission' and images.calls == 0


async def test_inspection_rejects_other_project(tmp_path):
    config, store, project, run, images, gateway = setup(tmp_path)
    store.put('assets', {'id':'foreign','project_id':'other'})
    result = await gateway.execute(run, call('i1','inspect_image',{'asset_id':'foreign'}))
    assert result['status'] == 'failed'


class Model:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.received = []
        self.efforts = []

    async def complete(self, messages, tools, *, reasoning_effort=None):
        self.received.append(list(messages))
        self.efforts.append(reasoning_effort)
        return next(self.replies), {'model':'test-agent', 'usage':{}}


def response(item=None):
    return {'role':'assistant', 'content':'' if item else '概念已生成，质量待校验', **({'tool_calls':[{k:v for k,v in item.items() if k != 'arguments'}]} if item else {})}


async def test_interrupt_resume_same_task_and_actual_visual_input(tmp_path):
    config, store, project, run, images, gateway = setup(tmp_path)
    replies = [response(call('ask','ask_user',{'question':'选什么颜色？','options':['绿色','米色']})), response(call()), response()]
    for i, reply in enumerate(replies):
        reply['reasoning_content'] = f'private-reasoning-{i}'
    model = Model(replies)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.db')) as cp:
        graph = build_graph(store, model, gateway, config, cp)
        await process_run(graph, store, run, config)
        assert store.get('runs','run')['status'] == 'waiting_user'
    # Rebuild both the checkpoint connection and graph to simulate worker restart.
    resumed = store.resume('run','绿色')
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.db')) as cp:
        graph = build_graph(store, model, gateway, config, cp)
        await process_run(graph, store, resumed, config)
        assert store.get('runs','run')['status'] == 'succeeded'
        assert images.calls == 1
        assert any(isinstance(m.get('content'),list) and any(b.get('type')=='image_url' for b in m['content']) for m in model.received[-1])
        assert len([c for c in store.tool_calls('run') if c['name']=='ask_user']) == 1
        ask_events=[event for event in store.events('run') if event['kind'] in {'tool_started','tool_finished'} and event['data']['name']=='ask_user']
        assert [event['kind'] for event in ask_events]==['tool_started','tool_finished']
        assert [m['reasoning_content'] for m in model.received[-1] if m['role']=='assistant'] == ['private-reasoning-0','private-reasoning-1']
        saved = await graph.aget_state({'configurable': {'thread_id': run['id']}})
        assert 'messages' not in saved.values
        nodes = graph.context_builder.nodes(run['session_id'])
        assert nodes[-1].messages[-1]['reasoning_content'] == 'private-reasoning-2'
        assert 'private-reasoning-' not in json.dumps(store.events(run['id']))


async def test_model_step_budget_stops_loop(tmp_path):
    config, store, project, run, images, gateway = setup(tmp_path,max_model_steps=2)
    model = Model([response(call('r1','read_project',{})),response(call('r2','read_project',{}))])
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.db')) as cp:
        graph = build_graph(store,model,gateway,config,cp)
        await process_run(graph,store,run,config)
    assert store.get('runs','run')['status']=='partial'
    assert len(model.received)==2


def test_resume_requires_waiting_state(tmp_path):
    _,store,_,run,_,_=setup(tmp_path)
    with pytest.raises(ValueError):
        store.resume(run['id'],'answer')


def test_cannot_cancel_a_running_provider_request(tmp_path):
    _,store,_,run,_,_=setup(tmp_path)
    with pytest.raises(ValueError):
        store.cancel(run['id'])
    assert store.get('runs',run['id'])['status']=='running'


async def test_batch_image_then_question_replays_without_new_image_submission(tmp_path):
    config,store,_,run,images,gateway=setup(tmp_path)
    batch=response(call())
    batch['tool_calls'].extend(response(call('ask','ask_user',{'question':'喜欢哪个颜色？','options':['绿色','米色']}))['tool_calls'])
    model=Model([batch,response()])
    checkpoint=str(tmp_path/'batch.db')
    async with AsyncSqliteSaver.from_conn_string(checkpoint) as cp:
        await process_run(build_graph(store,model,gateway,config,cp),store,run,config)
    assert store.get('runs','run')['status']=='waiting_user'
    assert store.get('tool_runs','run:ask')['status']=='waiting_user'
    assert images.calls==1
    assert not any(event['kind']=='error' for event in store.events('run'))
    resumed=store.resume('run','绿色')
    async with AsyncSqliteSaver.from_conn_string(checkpoint) as cp:
        await process_run(build_graph(store,model,gateway,config,cp),store,resumed,config)
    assert store.get('runs','run')['status']=='succeeded' and images.calls==1
    assert len(store.list('assets','project'))==1
    starts=[event for event in store.events('run') if event['kind']=='tool_started']
    assert [event['data']['name'] for event in starts]==['generate_image','ask_user']
    tools=[message for message in model.received[-1] if message['role']=='tool']
    assert [message['tool_call_id'] for message in tools]==['g1','ask']
    assert json.loads(tools[-1]['content'])['answer']=='绿色'


@pytest.mark.parametrize('effort',['off','low','high','max'])
async def test_run_keeps_selected_effort_across_interrupt_and_worker_restart(tmp_path,effort):
    config,store,_,run,_,gateway=setup(tmp_path)
    run=store.update_run(run['id'],reasoning_effort=effort)
    model=Model([response(call('ask','ask_user',{'question':'选择颜色','options':['绿色']})),response(call('read','read_project',{})),response()])
    checkpoint=str(tmp_path/'effort.db')
    async with AsyncSqliteSaver.from_conn_string(checkpoint) as cp:
        await process_run(build_graph(store,model,gateway,config,cp),store,run,config)
    assert store.get('runs',run['id'])['status']=='waiting_user'
    config.deepseek_reasoning_effort='high' if effort=='off' else 'off'
    resumed=store.resume(run['id'],'绿色')
    async with AsyncSqliteSaver.from_conn_string(checkpoint) as cp:
        await process_run(build_graph(store,model,gateway,config,cp),store,resumed,config)
    assert model.efforts==[effort,effort,effort]
    assert store.get('runs',run['id'])['reasoning_effort']==effort
    assert store.get('runs',run['id'])['status']=='succeeded'
