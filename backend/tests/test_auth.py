import io
import time
import pytest
from fastapi.testclient import TestClient
from app.auth import Auth, COOKIE
from app.config import Settings
from app.persistence.repositories import Store
from app.persistence.assets import Assets
from PIL import Image
from test_storage import storage


@pytest.fixture
def environment(storage, monkeypatch):
    import app.main as server
    store = storage
    config = Settings(_env_file=None, data_dir=store.directory)
    monkeypatch.setattr(server, 'store', store)
    monkeypatch.setattr(server, 'settings', config)
    monkeypatch.setattr(server, 'assets', Assets(store, config))
    auth = Auth(store)
    identities = {}
    for name in ('owner', 'editor', 'viewer', 'stranger', 'admin'):
        user = auth.create_user(name, 'initial-password-123', is_admin=name == 'admin')
        with store.connect() as db:
            db.execute('UPDATE users SET must_change_password=0 WHERE id=?', (user['id'],))
        identities[name] = user
    clients = []
    def login(name, change=True):
        client = TestClient(server.app)
        response = client.post('/api/auth/login', json={'username': name, 'password': 'initial-password-123'})
        assert response.status_code == 200
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        clients.append(client)
        return client
    yield server, store, identities, login
    for client in clients:
        client.close()


def test_authentication_csrf_and_session_revocation(environment):
    server, store, identities, login = environment
    with TestClient(server.app) as anonymous:
        assert anonymous.get('/api/projects').status_code == 401
        assert anonymous.get('/api/health/live').status_code == 200
        assert anonymous.post('/api/auth/login', headers={'Origin': 'https://evil.example'}, json={'username': 'owner', 'password': 'initial-password-123'}).status_code == 403
    client = login('owner')
    assert 'HttpOnly' in client.cookies.jar._cookies['testserver.local']['/'][COOKIE]._rest
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/projects', json={'name': 'blocked'}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    assert client.post('/api/auth/logout', json={}).status_code == 200
    assert client.get('/api/projects').status_code == 401


def test_project_acl_covers_runs_sessions_files_and_mutations(environment):
    server, store, identities, login = environment
    owner, editor, viewer, stranger, admin = [login(n) for n in ('owner', 'editor', 'viewer', 'stranger', 'admin')]
    project = owner.post('/api/projects', json={'name': 'private'}).json()
    path = '/api/projects/'+project['id']
    for name in ('editor', 'viewer'):
        assert owner.put(path+'/members/'+name, json={'role': name}).status_code == 200
    assert stranger.get('/api/projects').json() == []
    assert admin.get(path).status_code == 404  # Admin account management is separate from project access.
    assert viewer.get(path).status_code == 200
    assert viewer.post(path+'/runs', json={'message': 'blocked'}).status_code == 403
    assert editor.delete(path).status_code == 403
    run = editor.post(path+'/runs', json={'message': 'test'}).json()
    assert run['user_id'] == identities['editor']['id']
    output = io.BytesIO()
    Image.new('RGB', (10, 10)).save(output, format='PNG')
    asset = server.assets.save_image(project['id'], run['id'], output.getvalue(), prompt='test', title='test', requested_size='10x10', usage={})
    for resource in (path, '/api/runs/'+run['id'], '/api/sessions/'+run['session_id'], '/api/sessions/'+run['session_id']+'/events', asset['url'], asset['url']+'?download=true'):
        assert stranger.get(resource).status_code == 404, resource
    assert stranger.post('/api/runs/'+run['id']+'/cancel').status_code == 404
    assert stranger.post('/api/runs/'+run['id']+'/resume', json={'answer': 'test'}).status_code == 404
    assert viewer.get(asset['url']).status_code == 200
    assert viewer.post('/api/runs/'+run['id']+'/cancel').status_code == 403
    assert owner.delete(path+'/members/viewer').status_code == 200
    assert viewer.get(asset['url']).status_code == 404
    assert owner.post(path+'/owner/editor', json={}).status_code == 200
    assert owner.get(path).json()['project']['role'] == 'editor'
    assert editor.get(path).json()['project']['role'] == 'owner'


def test_user_management_forced_password_and_disabled_sessions(environment):
    server, store, identities, login = environment
    admin, owner = login('admin'), login('owner')
    assert owner.get('/api/admin/users').status_code == 403
    response = admin.post('/api/admin/users', json={'username': 'new-employee', 'password': 'initial-password-123', 'display_name': '新员工'})
    assert response.status_code == 201 and 'password_hash' not in response.text
    employee = login('new-employee')
    assert employee.get('/api/projects').status_code == 403
    assert employee.post('/api/auth/password', json={'current_password': 'initial-password-123', 'new_password': 'updated-password-123'}).status_code == 200
    assert employee.get('/api/auth/me').status_code == 401
    assert admin.patch('/api/admin/users/'+identities['owner']['id'], json={'disabled': True}).status_code == 200
    assert owner.get('/api/projects').status_code == 401
    assert admin.patch('/api/admin/users/'+identities['admin']['id'], json={'disabled': True}).status_code == 409
    assert admin.patch('/api/admin/users/'+identities['viewer']['id'], json={'password': 'reset-password-123'}).status_code == 200
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0] >= 5


def test_login_limits_and_expiration(environment):
    server, store, identities, login = environment
    with TestClient(server.app) as client:
        for _ in range(10):
            assert client.post('/api/auth/login', json={'username': 'missing', 'password': 'incorrect'}).status_code == 401
        assert client.post('/api/auth/login', json={'username': 'missing', 'password': 'incorrect'}).status_code == 429
    client = login('owner')
    with store.connect() as db:
        db.execute('UPDATE auth_sessions SET expires_at=?', (time.time()-1,))
    assert client.get('/api/auth/me').status_code == 401


async def test_live_stream_stops_when_membership_is_revoked(environment):
    server, store, identities, login = environment
    owner, viewer = login('owner'), login('viewer')
    project = owner.post('/api/projects', json={'name': 'stream'}).json()
    path = '/api/projects/'+project['id']
    owner.put(path+'/members/viewer', json={'role': 'viewer'})
    class Request:
        headers = {}
        cookies = {COOKIE: viewer.cookies[COOKIE]}
        async def is_disconnected(self):
            return False
    server.settings.sse_poll_seconds = 0.1
    stream = await server.events(project['session_id'], Request(), 0, identities['viewer'])
    assert 'heartbeat' in await anext(stream.body_iterator)
    owner.put(path+'/members/viewer', json={'role': 'editor'})
    assert 'permissions_changed' in await anext(stream.body_iterator)
    assert 'heartbeat' in await anext(stream.body_iterator)
    owner.delete(path+'/members/viewer')
    assert 'access_revoked' in await anext(stream.body_iterator)
    with pytest.raises(StopAsyncIteration):
        await anext(stream.body_iterator)
