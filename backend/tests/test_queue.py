import asyncio
import concurrent.futures
import threading
from contextlib import contextmanager
import pytest
from app.config import Settings
from app.persistence.repositories import Store, uid
from app.runtime.leases import execution_lease, LeaseLost
from app.runtime.checkpoints import checkpoint_saver
from app.runtime.worker import execute_claim
from app.runtime.gateway import Gateway
from app.agent.graph import build_graph
from app.agent.instructions import SYSTEM
from test_storage import storage
from test_runtime import Images
from test_runtime import Model, response, call


def enqueue(store, user_id=None):
    project = store.put('projects', {'id': uid(), 'name': 'queue test'})
    return store.enqueue({'id': uid(), 'project_id': project['id'], 'user_id': user_id, 'status': 'queued', 'message': 'hello'}, {}, SYSTEM)


def test_multi_process_limits_and_fairness(storage):
    store = storage
    for user in ('alice', 'bob', 'carol'):
        for _ in range(3):
            enqueue(store, user)
    # Independent Store instances simulate API/worker processes sharing a DB.
    other = Store(store.directory, store.database_url)
    def claim(index):
        return (store if index % 2 else other).claim(f'worker-{index}', global_limit=3, user_limit=1)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            claimed = [r for r in pool.map(claim, range(6)) if r]
        assert len(claimed) == 3
        assert len({r['id'] for r in claimed}) == 3
        assert {r['user_id'] for r in claimed} == {'alice', 'bob', 'carol'}
        assert store.claim(global_limit=3) is None
        first = claimed[0]
        store.update_run(first['id'], status='succeeded')
        store.release_lease(first['id'], first['_lease_token'])
        assert store.claim(global_limit=3, user_limit=1)['user_id'] == first['user_id']
    finally:
        other.close()


def test_only_expired_unlocked_tasks_recover_and_old_lease_is_fenced(storage):
    store = storage
    run = enqueue(store)
    claimed = store.claim()
    restarted = Store(store.directory, store.database_url)
    try:
        assert restarted.recover_expired() == 0
        with store.connect() as db:
            db.execute('UPDATE runs SET lease_until=0 WHERE id=?', (run['id'],))
        with store.project_lock(run['project_id']):
            assert restarted.recover_expired() == 0
        assert restarted.recover_expired() == 1
        replacement = restarted.claim()
        assert replacement['_lease_token'] != claimed['_lease_token']
        token = execution_lease.set((id(store), run['id'], claimed['_lease_token']))
        try:
            with pytest.raises(LeaseLost):
                store.update_run(run['id'], summary='stale writer')
        finally:
            execution_lease.reset(token)
        assert not store.renew_lease(run['id'], claimed['_lease_token'])
        assert store.renew_lease(run['id'], replacement['_lease_token'])
    finally:
        restarted.close()


async def test_actual_graph_tasks_overlap_and_checkpoint_resume(storage):
    store = storage
    config = Settings(_env_file=None, data_dir=store.directory)
    runs = [enqueue(store) for _ in range(3)]
    class Model:
        active = 0
        maximum = 0
        async def complete(self, messages, tools, **kwargs):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.1)
            self.active -= 1
            return {'content': 'done'}, {}
    model = Model()
    async with checkpoint_saver(store) as cp:
        graph = build_graph(store, model, Gateway(store, Images(), config), config, cp)
        claimed = [store.claim(user_limit=5) for _ in runs]
        await asyncio.wait_for(asyncio.gather(*(execute_claim(graph, store, r, config) for r in claimed)), timeout=30)
        assert model.maximum >= 2
        assert all(store.get('runs', r['id'])['status'] == 'succeeded' for r in runs)
        for run in runs:
            snapshot = await graph.aget_state({'configurable': {'thread_id': run['id']}})
            assert snapshot.values['run_id'] == run['id']


async def test_checkpoint_rejects_a_stale_executor(storage):
    store = storage
    run = enqueue(store)
    claimed = store.claim()
    with store.connect() as db:
        db.execute('UPDATE runs SET lease_until=0 WHERE id=?', (run['id'],))
    token = execution_lease.set((id(store), run['id'], claimed['_lease_token']))
    try:
        async with checkpoint_saver(store) as cp:
            with pytest.raises(LeaseLost):
                await cp.aput_writes({'configurable': {'thread_id': run['id'], 'checkpoint_ns': '', 'checkpoint_id': 'old'}}, [('x', 1)], 'task')
    finally:
        execution_lease.reset(token)


async def test_waiting_run_resumes_after_checkpoint_connection_restart(storage):
    store = storage
    config = Settings(_env_file=None, data_dir=store.directory)
    run = enqueue(store)
    model = Model([response(call('ask', 'ask_user', {'question': '颜色？', 'options': ['蓝色']})), response()])
    async with checkpoint_saver(store) as cp:
        graph = build_graph(store, model, Gateway(store, Images(), config), config, cp)
        await execute_claim(graph, store, store.claim(), config)
    assert store.get('runs', run['id'])['status'] == 'waiting_user'
    store.resume(run['id'], '蓝色')
    async with checkpoint_saver(store) as cp:
        graph = build_graph(store, model, Gateway(store, Images(), config), config, cp)
        await execute_claim(graph, store, store.claim(), config)
    assert store.get('runs', run['id'])['status'] == 'succeeded'


@pytest.mark.parametrize('action', ['resume', 'cancel'])
def test_lease_release_preserves_concurrent_user_action(storage, monkeypatch, action):
    store = storage
    run = enqueue(store)
    claimed = store.claim()
    store.update_run(run['id'], status='waiting_user', question={'question': '颜色？'}, resume_answer=None)
    api_store = Store(store.directory, store.database_url)
    read = threading.Event()
    finish_release = threading.Event()
    connect = store.connect

    class PausedCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            read.set()
            assert finish_release.wait(5)
            return row

    class Connection:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, sql, args=()):
            cursor = self.db.execute(sql, args)
            if sql.startswith('SELECT data,status FROM runs WHERE id=? AND lease_token=?'):
                return PausedCursor(cursor)
            return cursor

    @contextmanager
    def paused_connection():
        with connect() as db:
            yield Connection(db)

    monkeypatch.setattr(store, 'connect', paused_connection)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            release = pool.submit(store.release_lease, run['id'], claimed['_lease_token'])
            assert read.wait(5)
            mutate = pool.submit(api_store.resume, run['id'], '蓝色') if action == 'resume' else pool.submit(api_store.cancel, run['id'])
            try:
                # Without the row lock this commits before release writes its
                # stale snapshot. With it, the user action waits until commit.
                mutate.result(timeout=0.5)
            except concurrent.futures.TimeoutError:
                pass
            finally:
                finish_release.set()
            release.result(timeout=5)
            mutate.result(timeout=5)
        actual = api_store.get('runs', run['id'])
        assert actual['status'] == ('queued' if action == 'resume' else 'cancelled')
        if action == 'resume':
            assert actual['resume_answer'] == '蓝色'
    finally:
        finish_release.set()
        api_store.close()


async def test_worker_retries_a_busy_project_lock(storage):
    store = storage
    config = Settings(_env_file=None, data_dir=store.directory)
    run = enqueue(store)
    claimed = store.claim()
    async with checkpoint_saver(store) as cp:
        graph = build_graph(store, Model([response()]), Gateway(store, Images(), config), config, cp)
        with store.project_lock(run['project_id']):
            task = asyncio.create_task(execute_claim(graph, store, claimed, config))
            await asyncio.sleep(0.15)
        await asyncio.wait_for(task, timeout=10)
    assert store.get('runs', run['id'])['status'] == 'succeeded'
