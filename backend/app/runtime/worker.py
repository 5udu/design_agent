import asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from app.config import settings
from app.persistence.repositories import Store
from app.persistence.locking import FileLock as WorkerLock, LockBusy, project_lock
from app.providers.deepseek import DeepSeek
from app.providers.seedream import Seedream
from app.runtime.gateway import Gateway
from app.agent.graph import build_graph, initial_state


async def process_run(graph, store, run, config=settings):
    lock = project_lock(store.directory, run['project_id'])
    while True:
        try:
            lock.__enter__()
            break
        except LockBusy:
            await asyncio.sleep(0.05)
    try:
        await _process_run(graph, store, run, config)
    finally:
        lock.__exit__(None, None, None)


async def _process_run(graph, store, run, config):
    run = store.get('runs', run['id'])
    if not run.get('reasoning_effort'):
        run = store.update_run(run['id'], reasoning_effort=config.deepseek_reasoning_effort)
    graph_config = {'configurable': {'thread_id': run['id']}, 'recursion_limit': 60}
    snapshot = await graph.aget_state(graph_config)
    graph.context_builder.ensure_run(run)
    if run.get('resume_answer') is not None and snapshot.values:
        state = Command(resume=run['resume_answer'])
    elif run.get('resume_answer') is not None:
        # A missing checkpoint cannot resume an interrupt. Close its open batch
        # explicitly, then continue from the journal at a new decision number.
        answers = [event for event in store.events(run['id']) if event['kind'] == 'user_answer']
        answer = answers[-1] if answers else None
        store.close_pending(run['id'], '执行检查点缺失，先前等待的工具已中断；用户回答将作为补充记录继续。')
        store.append(run['id'], 'recovery_context', {'reason': 'missing_checkpoint'},
            key=run['id']+':recovery:'+str(answer['seq'] if answer else 0), source='user',
            model_data={'role': 'user', 'content': '此前任务在询问后等待；执行检查点缺失。以下是已保存的用户回答，请结合会话中的已知结果继续，无法确认的执行不可假定成功：'+
                        str(answer['data'] if answer else run['resume_answer'])})
        state = initial_state(run)
        prior_steps = [int(event['step_id'].rsplit(':', 1)[-1]) for event in store.events(run['id'])
                       if event['kind'] == 'assistant_message' and event['step_id'] and event['step_id'].rsplit(':', 1)[-1].isdigit()]
        state['steps'] = max([run.get('steps', 0), *prior_steps])
    elif snapshot.values:
        if not snapshot.next:
            # Graph already ended; business final state may have committed before crash.
            store.transition(run['id'], 'run_status', {'status': 'partial'}, status='partial',
                             summary='执行已结束，已恢复保存结果，请查看历史。')
            return
        state = None
    else:
        state = initial_state(run)
    store.append(run['id'], 'run_status', {'status': 'running'})
    try:
        result = await graph.ainvoke(state, graph_config)
        if result.get('__interrupt__'):
            question = result['__interrupt__'][0].value
            store.update_run(run['id'], status='waiting_user', question=question, resume_answer=None)
            store.append(run['id'], 'question', question)
            store.append(run['id'], 'run_status', {'status': 'waiting_user'})
        else:
            store.update_run(run['id'], resume_answer=None)
    except Exception as exc:
        reason = config.safe_error(exc)
        store.close_pending(run['id'], '本次执行中断，工具结果无法确认，请核实已有产物。')
        store.transition(run['id'], 'error', {'message': reason}, status='failed', error=reason, resume_answer=None)
        store.append(run['id'], 'run_status', {'status': 'failed'})


async def main():
    store = Store(settings.data_dir)
    with WorkerLock(settings.data_dir / 'worker.lock'):
        # Recover only after obtaining the OS worker lock.
        for run in store.list('runs'):
            if run['status'] == 'running':
                store.update_run(run['id'], status='queued')
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.db')) as cp:
            graph = build_graph(store, DeepSeek(settings), Gateway(store, Seedream(settings), settings), settings, cp)
            print('Image agent worker ready', flush=True)
            while True:
                run = store.claim()
                if run:
                    await process_run(graph, store, run)
                else:
                    await asyncio.sleep(0.5)


if __name__ == '__main__':
    asyncio.run(main())
