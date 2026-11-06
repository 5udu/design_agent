"""Sign legacy API scenarios in through the real authentication boundary."""
import json
from contextlib import contextmanager
from fastapi.testclient import TestClient
from app.auth import Auth


@contextmanager
def signed_in_client(server):
    auth = Auth(server.store)
    with server.store.connect() as db:
        row = db.execute("SELECT id FROM users WHERE username='test-admin'").fetchone()
    if row:
        user = auth.user(row['id'])
    else:
        user = auth.create_user('test-admin', 'test-password-12345', is_admin=True)
        with server.store.connect() as db:
            db.execute('UPDATE users SET must_change_password=0 WHERE id=?', (user['id'],))
    with server.store.connect() as db:
        for row in db.execute('SELECT data FROM projects WHERE owner_id IS NULL').fetchall():
            project = json.loads(row['data'])
            project['owner_id'] = user['id']
            server.store._put(db, 'projects', project)
    with TestClient(server.app) as client:
        response = client.post('/api/auth/login', json={'username': user['username'], 'password': 'test-password-12345'})
        assert response.status_code == 200, response.text
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        yield client
