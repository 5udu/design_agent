"""Run these against a disposable PostgreSQL DB with TEST_DATABASE_URL set."""
import concurrent.futures
import os
from urllib.parse import urlparse
import pytest
from app.persistence.repositories import Store, uid
from app.persistence.projects import delete_project


@pytest.fixture(params=['sqlite', 'postgres'])
def storage(request, tmp_path):
    url = os.getenv('TEST_DATABASE_URL', '') if request.param == 'postgres' else ''
    if request.param == 'postgres' and not url:
        pytest.skip('TEST_DATABASE_URL is not set')
    if url and not urlparse(url).path.rstrip('/').endswith('_test'):
        raise pytest.UsageError('Destructive integration tests require a database name ending in _test')
    store = Store(tmp_path, url)
    yield store
    if store.postgres:
        with store.connect() as db:
            for table in ('session_events', 'tool_runs', 'sessions', 'assets', 'versions', 'runs', 'projects', 'checkpoint_cleanup', 'worker_dispatch', 'workers', 'project_members', 'auth_sessions', 'users', 'login_limits', 'audit_log'):
                db.execute(f'DELETE FROM {table}')
    store.close()


def test_transactions_and_concurrent_journal(storage):
    store = storage
    project = store.put('projects', {'id': uid(), 'name': '事务验证'})
    run = store.put('runs', {'id': uid(), 'project_id': project['id'], 'status': 'succeeded', 'message': 'hello'})
    with pytest.raises(RuntimeError):
        with store.connect() as db:
            store.append(run['id'], 'test', {'text': 'rollback'}, db=db)
            raise RuntimeError('rollback')
    assert store.events(run['id']) == []
    def append(index):
        return store.append(run['id'], 'test', {'text': f'消息 {index} ? %'}, key=f'key-{index}')
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(append, range(20)))
    events = store.events(run['id'])
    assert [e['seq'] for e in events] == list(range(1, 21))
    assert store.append(run['id'], 'test', {}, key='key-0')['id'] in {e['id'] for e in events}
    assert len(store.events(run['id'])) == 20
    assert delete_project(store, project['id'])['deleted']
    assert store.events(run['id']) == []


def test_storage_restart_and_claim(storage):
    store = storage
    project = store.put('projects', {'id': uid(), 'name': 'restart'})
    run = store.put('runs', {'id': uid(), 'project_id': project['id'], 'status': 'queued', 'message': 'hello'})
    reopened = Store(store.directory, store.database_url)
    try:
        assert reopened.claim()['id'] == run['id']
        assert reopened.claim() is None
        assert reopened.get('runs', run['id'])['status'] == 'running'
    finally:
        reopened.close()
