import asyncio
import logging
import signal
import os
import socket
import uuid
from langgraph.types import Command
from app.config import settings
from app.persistence.repositories import Store
from app.persistence.locking import LockBusy
from app.runtime.leases import execution_lease, LeaseLost
from app.runtime.checkpoints import checkpoint_saver
from app.providers.deepseek import DeepSeek
from app.providers.seedream import Seedream
from app.runtime.gateway import Gateway
from app.agent.graph import build_graph, initial_state


async def process_run(graph, store, run, config=settings):
    while True:
        # PostgreSQL's generator context manager is single-use, including when
        # acquisition raises LockBusy; obtain a fresh one on every retry.
        lock = store.project_lock(run['project_id'])
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
        if isinstance(exc, LeaseLost):
            raise
        reason = config.safe_error(exc)
        store.close_pending(run['id'], '本次执行中断，工具结果无法确认，请核实已有产物。')
        store.transition(run['id'], 'error', {'message': reason}, status='failed', error=reason, resume_answer=None)
        store.append(run['id'], 'run_status', {'status': 'failed'})


async def execute_claim(graph, store, run, config):
    task = asyncio.current_task()
    lost = asyncio.Event()
    async def heartbeat():
        while True:
            await asyncio.sleep(config.worker_lease_seconds/4)
            try:
                renewed = await asyncio.to_thread(store.renew_lease, run['id'], run['_lease_token'], config.worker_lease_seconds)
            except Exception:
                renewed = False
            if not renewed:
                lost.set()
                task.cancel()
                return
    pulse = asyncio.create_task(heartbeat())
    token = execution_lease.set((id(store), run['id'], run['_lease_token']))
    try:
        await process_run(graph, store, run, config)
    except LeaseLost:
        logging.warning('execution_lease_lost run_id=%s', run['id'])
    except asyncio.CancelledError:
        if not lost.is_set():
            raise
        logging.warning('execution_heartbeat_lost run_id=%s', run['id'])
    finally:
        execution_lease.reset(token)
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        await asyncio.to_thread(store.release_lease, run['id'], run['_lease_token'])


async def run_worker(store, graph, cp, config, stopping=None):
    stopping = stopping or asyncio.Event()
    worker_id = f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}'
    async def slot():
        while not stopping.is_set():
            try:
                run = await asyncio.to_thread(store.claim, worker_id, global_limit=config.global_concurrency,
                                            user_limit=config.user_concurrency, lease_seconds=config.worker_lease_seconds)
                if run:
                    await execute_claim(graph, store, run, config)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception('worker_slot_error worker_id=%s', worker_id)
                await asyncio.sleep(2)
    tasks = [asyncio.create_task(slot()) for _ in range(config.worker_concurrency)]
    try:
        while not stopping.is_set():
            await asyncio.to_thread(store.worker_heartbeat, worker_id)
            await asyncio.to_thread(store.recover_expired)
            if store.postgres:
                with store.connect() as db:
                    cleanup = db.execute('SELECT run_id FROM checkpoint_cleanup LIMIT 100').fetchall()
                for row in cleanup:
                    await cp.adelete_thread(row['run_id'])
                    with store.connect() as db:
                        db.execute('DELETE FROM checkpoint_cleanup WHERE run_id=?', (row['run_id'],))
            try:
                await asyncio.wait_for(stopping.wait(), timeout=5)
            except TimeoutError:
                pass
    finally:
        stopping.set()
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), config.worker_shutdown_seconds)
        except (TimeoutError, asyncio.CancelledError):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        with store.connect() as db:
            db.execute('DELETE FROM workers WHERE id=?', (worker_id,))


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = Store(settings.data_dir, settings.database_url, settings.db_pool_size)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ('SIGINT', 'SIGTERM'):
        try:
            loop.add_signal_handler(getattr(signal, name), stopping.set)
        except NotImplementedError:
            pass
    try:
        async with checkpoint_saver(store) as cp:
            graph = build_graph(store, DeepSeek(settings), Gateway(store, Seedream(settings), settings), settings, cp)
            logging.info('worker_ready concurrency=%s global_limit=%s', settings.worker_concurrency, settings.global_concurrency)
            await run_worker(store, graph, cp, settings, stopping)
    finally:
        store.close()


if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
