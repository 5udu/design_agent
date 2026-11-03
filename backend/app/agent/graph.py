"""Checkpointed control flow; model history is always derived from the session."""
import time
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from app.context.builder import ContextBuilder
from app.providers.deepseek import ContextWindowExceeded
from app.runtime.results import finish_run
from app.tools.registry import TOOL_SCHEMAS


class AgentState(TypedDict, total=False):
    run_id: str
    steps: int
    calls: list
    finished: bool


def build_graph(store, model, gateway, config, checkpointer):
    context = ContextBuilder(store, model, gateway.assets, config, TOOL_SCHEMAS)

    async def decide(state):
        run = store.get('runs', state['run_id'])
        if run['status'] == 'cancelled':
            return {'finished': True, 'calls': []}
        steps = state.get('steps', run.get('steps', 0))
        if steps >= config.max_model_steps:
            store.transition(run['id'], 'run_status', {'status': 'partial'}, status='partial',
                             summary='达到本轮行动上限，已保留产物。', quality_status='unverified')
            return {'finished': True, 'calls': []}
        step_id = f"{run['id']}:{steps+1}"
        accepted = store.event_by_key(run['session_id'], step_id+':assistant', private=True)
        if accepted:
            message = accepted['model_data']
        else:
            store.append(run['id'], 'step_started', {'step': steps+1}, key=step_id+':start', step_id=step_id)
            started = time.time()
            prepared = await context.prepare(run, step_id)
            try:
                message, usage = await model.complete(prepared.messages, TOOL_SCHEMAS,
                    reasoning_effort=run.get('reasoning_effort', config.deepseek_reasoning_effort))
            except ContextWindowExceeded:
                prepared = await context.prepare(run, step_id, force_compact=True, attempt=1)
                message, usage = await model.complete(prepared.messages, TOOL_SCHEMAS,
                    reasoning_effort=run.get('reasoning_effort', config.deepseek_reasoning_effort))
            assistant = {'role': 'assistant', 'content': message.get('content') or ''}
            for key in ('reasoning_content', 'tool_calls'):
                if key in message:
                    assistant[key] = message[key]
            phase = 'progress' if assistant.get('tool_calls') else 'final'
            store.append(run['id'], 'assistant_message', {'text': assistant['content'], 'phase': phase, 'request_seq': prepared.request_seq},
                                    key=step_id+':assistant', step_id=step_id, source='assistant', model_data=assistant)
            message = assistant
            store.append(run['id'], 'model_step', {'step': steps+1, 'elapsed_ms': round((time.time()-started)*1000),
                         'request_seq': prepared.request_seq, 'estimated_input_tokens': prepared.estimated_tokens, **usage}, key=step_id+':usage', step_id=step_id)
        calls = message.get('tool_calls', [])
        if not calls:
            finish_run(store, run, message.get('content') or '', steps+1)
            store.append(run['id'], 'step_finished', {'step': steps+1}, key=step_id+':end', step_id=step_id)
        return {'steps': steps+1, 'calls': calls, 'finished': not bool(calls)}

    async def act(state):
        run = store.get('runs', state['run_id'])
        step_id = f"{run['id']}:{state.get('steps', run.get('steps', 0))}"
        asset_ids = []
        for raw in state['calls']:
            result = await gateway.execute(run, {**raw, 'step_id': step_id})
            asset_ids.extend(result.get('asset_ids', []))
        # Add observations only after the complete tool batch has settled.
        for asset_id in dict.fromkeys(asset_ids):
            context.observe(run, asset_id, key=step_id+':observation:'+asset_id, step_id=step_id)
        store.append(run['id'], 'step_finished', {'step': state.get('steps', 0)}, key=step_id+':end', step_id=step_id)
        return {'calls': []}

    graph = StateGraph(AgentState)
    graph.add_node('decide', decide)
    graph.add_node('act', act)
    graph.add_edge(START, 'decide')
    graph.add_conditional_edges('decide', lambda state: 'end' if state.get('finished') else 'act', {'end': END, 'act': 'act'})
    graph.add_edge('act', 'decide')
    compiled = graph.compile(checkpointer=checkpointer)
    compiled.context_builder = context
    return compiled


def initial_state(run):
    return {'run_id': run['id'], 'steps': 0, 'calls': [], 'finished': False}
