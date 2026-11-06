"""Managed employee accounts, opaque cookie sessions and project membership."""
import hashlib
import hmac
import json
import re
import secrets
import time
from app.persistence.repositories import uid

COOKIE = 'design_session'


def hash_password(password):
    if not 12 <= len(password) <= 256:
        raise ValueError('密码长度需要在 12 到 256 个字符之间')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64*1024*1024)
    return f'scrypt${salt}${digest.hex()}'


def verify_password(password, encoded):
    try:
        kind, salt, expected = encoded.split('$')
        if kind != 'scrypt' or len(password) > 256:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64*1024*1024)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def public_user(row):
    return {key: row[key] for key in ('id', 'username', 'display_name', 'is_admin', 'disabled', 'must_change_password')}


class Auth:
    def __init__(self, store):
        self.store = store

    def initialize(self):
        with self.store.connect() as db:
            if self.store.postgres:
                db.execute('SELECT pg_advisory_xact_lock(91020601)')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                disabled INTEGER NOT NULL DEFAULT 0, must_change_password INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS auth_sessions(
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL,
                expires_at DOUBLE PRECISION NOT NULL);
            CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
            CREATE TABLE IF NOT EXISTS login_limits(
                key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires_at DOUBLE PRECISION NOT NULL);
            CREATE TABLE IF NOT EXISTS project_members(
                project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
                PRIMARY KEY(project_id,user_id));
            CREATE INDEX IF NOT EXISTS memberships_user ON project_members(user_id,project_id);
            CREATE TABLE IF NOT EXISTS audit_log(
                id TEXT PRIMARY KEY, actor_id TEXT, action TEXT NOT NULL, target_id TEXT,
                created_at DOUBLE PRECISION NOT NULL, data TEXT NOT NULL);
            INSERT INTO schema_migrations(version) VALUES(2) ON CONFLICT(version) DO NOTHING;
            ''')

    def audit(self, actor, action, target, data=None, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self.audit(actor, action, target, data, connection)
        db.execute('INSERT INTO audit_log VALUES(?,?,?,?,?,?)',
                   (uid(), actor, action, target, time.time(), json.dumps(data or {}, ensure_ascii=False)))

    def create_user(self, username, password, display_name='', is_admin=False, actor=None):
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.@-]{2,99}', username):
            raise ValueError('账号需为 3 到 100 位字母、数字或 _.@-')
        encoded = hash_password(password)
        user_id = uid()
        with self.store.connect() as db:
            db.execute('INSERT INTO users(id,username,display_name,password_hash,is_admin) VALUES(?,?,?,?,?)',
                       (user_id, username, display_name.strip() or username, encoded, int(is_admin)))
            self.audit(actor, 'user_created', user_id, db=db)
        return self.user(user_id)

    def user(self, user_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return public_user(row) if row else None

    def login(self, username, password, address, ttl):
        now = time.time()
        # DB-backed windows coordinate throttling across API processes. Reserve
        # an attempt before the expensive hash, including nonexistent accounts.
        keys = ['account:'+username.strip().lower(), 'ip:'+address]
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for key, maximum in zip(keys, (10, 300)):
                key = hashlib.sha256(key.encode()).hexdigest()
                db.execute('INSERT INTO login_limits(key,attempts,expires_at) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN login_limits.expires_at<? THEN 1 ELSE login_limits.attempts+1 END, expires_at=CASE WHEN login_limits.expires_at<? THEN excluded.expires_at ELSE login_limits.expires_at END', (key, now+900, now, now))
                row = db.execute('SELECT attempts FROM login_limits WHERE key=?', (key,)).fetchone()
                if row['attempts'] > maximum:
                    return None, 'limited'
            row = db.execute('SELECT * FROM users WHERE username=?', (username.strip().lower(),)).fetchone()
        dummy = 'scrypt$'+'00'*16+'$'+'00'*64
        valid = verify_password(password, row['password_hash'] if row else dummy)
        if not valid or not row or row['disabled']:
            return None, 'invalid'
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.connect() as db:
            suffix = ' FOR UPDATE' if self.store.postgres else ''
            fresh = db.execute('SELECT password_hash,disabled FROM users WHERE id=?'+suffix, (row['id'],)).fetchone()
            if not fresh or fresh['disabled'] or fresh['password_hash'] != row['password_hash']:
                return None, 'invalid'
            db.execute('DELETE FROM auth_sessions WHERE expires_at<?', (now,))
            db.execute('DELETE FROM login_limits WHERE expires_at<?', (now,))
            db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], csrf, now+ttl))
            self.audit(row['id'], 'login', row['id'], db=db)
        return {'token': token, 'csrf_token': csrf, 'user': public_user(row)}, None

    def session(self, token):
        if not token:
            return None
        with self.store.connect() as db:
            row = db.execute('SELECT u.*,s.csrf,s.expires_at FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return {'user': public_user(row), 'csrf_token': row['csrf']} if row else None

    def logout(self, token):
        with self.store.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def change_password(self, user_id, old_password, new_password):
        with self.store.connect() as db:
            row = db.execute('SELECT password_hash FROM users WHERE id=?', (user_id,)).fetchone()
        if not row or not verify_password(old_password, row['password_hash']):
            raise ValueError('当前密码不正确')
        self.set_password(user_id, new_password, user_id, force_change=False)

    def set_password(self, user_id, password, actor, force_change=True):
        encoded = hash_password(password)
        with self.store.connect() as db:
            db.execute('UPDATE users SET password_hash=?,must_change_password=? WHERE id=?', (encoded, int(force_change), user_id))
            db.execute('DELETE FROM auth_sessions WHERE user_id=?', (user_id,))
            self.audit(actor, 'password_changed', user_id, db=db)

    def role(self, user, project):
        if project.get('owner_id') == user['id']:
            return 'owner'
        with self.store.connect() as db:
            row = db.execute('SELECT role FROM project_members WHERE project_id=? AND user_id=?', (project['id'], user['id'])).fetchone()
        return row['role'] if row else None

    def visible_projects(self, user, limit=100, offset=0):
        with self.store.connect() as db:
            rows = db.execute('SELECT p.data FROM projects p WHERE p.owner_id=? OR EXISTS(SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?) ORDER BY p.created_at DESC,p.id LIMIT ? OFFSET ?',
                              (user['id'], user['id'], limit, offset)).fetchall()
        return [{**json.loads(row['data']), 'role': self.role(user, json.loads(row['data']))} for row in rows]


def require_run_access(store, run):
    if not run.get('user_id'):
        return  # Historical/offline runs have no employee identity.
    auth = Auth(store)
    user = auth.user(run['user_id'])
    project = store.get('projects', run['project_id'])
    if not user or user['disabled'] or not project or auth.role(user, project) not in {'owner', 'editor'}:
        raise PermissionError('提交任务的员工已停用或失去项目编辑权限')
