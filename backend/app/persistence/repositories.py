"""Small transactional store. Never keeps a connection across provider requests."""
import json
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from app.session.journal import SessionJournal, encode
from app.runtime.queue import TaskQueue
from app.runtime.leases import check_lease


def uid() -> str:
    return uuid.uuid4().hex


class Store(SessionJournal, TaskQueue):
    def __init__(self, directory: Path, database_url: str = '', pool_size: int = 20):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'application.db'
        self.database_url = database_url
        self.postgres = bool(database_url)
        self.pool = None
        if self.postgres:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            self.pool = ConnectionPool(database_url, min_size=1, max_size=pool_size,
                                       kwargs={'row_factory': dict_row}, timeout=15, open=True)
            self.pool.wait()
        with self.connect() as db:
            if self.postgres:
                db.execute('SELECT pg_advisory_xact_lock(91020601)')
            schema = '''
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tool_runs(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS file_cleanup(path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS session_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
                run_id TEXT NOT NULL, step_id TEXT, call_id TEXT, kind TEXT NOT NULL, source TEXT NOT NULL,
                data TEXT NOT NULL, model_data TEXT, event_key TEXT, created REAL NOT NULL,
                UNIQUE(session_id,seq), UNIQUE(session_id,event_key));
            CREATE INDEX IF NOT EXISTS session_event_run ON session_events(run_id,seq);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_project ON runs(project_id) WHERE status IN ('queued','running','waiting_user');
            '''
            if self.postgres:
                schema = schema.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY').replace(' REAL ', ' DOUBLE PRECISION ')
                for table in ('projects', 'runs', 'assets', 'versions', 'tool_runs'):
                    schema = schema.replace(f'{table}(id TEXT', f'{table}(ordinal BIGSERIAL UNIQUE, id TEXT')
            db.executescript(schema)
            # Forward-only, additive schema migration, safe for existing local data.
            for table in ('projects', 'runs'):
                columns = ('owner_id TEXT', 'created_at DOUBLE PRECISION NOT NULL DEFAULT 0') if table == 'projects' else (
                    'user_id TEXT', 'created_at DOUBLE PRECISION NOT NULL DEFAULT 0',
                    'lease_token TEXT', 'lease_until DOUBLE PRECISION', 'worker_id TEXT')
                existing = {r['column_name'] for r in db.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?', (table,))} if self.postgres else {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                for column in columns:
                    if column.split()[0] not in existing:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {column}')
            db.executescript('''
            CREATE INDEX IF NOT EXISTS runs_queue ON runs(status,created_at);
            CREATE INDEX IF NOT EXISTS runs_user_status ON runs(user_id,status);
            CREATE INDEX IF NOT EXISTS assets_project ON assets(project_id);
            CREATE INDEX IF NOT EXISTS versions_project ON versions(project_id);
            CREATE INDEX IF NOT EXISTS tool_runs_run ON tool_runs(run_id);
            CREATE TABLE IF NOT EXISTS checkpoint_cleanup(run_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS worker_dispatch(user_id TEXT PRIMARY KEY,last_started DOUBLE PRECISION NOT NULL);
            CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY,updated_at DOUBLE PRECISION NOT NULL);
            CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
            INSERT INTO schema_migrations(version) VALUES(1) ON CONFLICT(version) DO NOTHING;
            ''')
            from app.auth import Auth
            # Reuse the initialization transaction (including its PostgreSQL lock).
            # Auth schema is initialized below after this connection commits.
        Auth(self).initialize()
        from app.session.migration import migrate
        if not self.postgres:
            migrate(self)
        from app.persistence.projects import collect_garbage
        collect_garbage(self)

    @contextmanager
    def connect(self):
        if self.postgres:
            from app.persistence.database import PostgresConnection
            with self.pool.connection() as connection:
                db = PostgresConnection(connection)
                check_lease(self, db)
                yield db
            return
        from app.persistence.database import SQLiteConnection
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        db = SQLiteConnection(connection)
        db.execute('PRAGMA journal_mode=WAL')
        try:
            check_lease(self, db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def close(self):
        if self.pool:
            self.pool.close()

    def project_lock(self, project_id):
        if self.postgres:
            from app.persistence.database import postgres_project_lock
            return postgres_project_lock(self.pool, project_id)
        from app.persistence.locking import project_lock
        return project_lock(self.directory, project_id)

    def lock_row(self, db, table, key):
        assert table in {'projects', 'runs', 'sessions'}
        if self.postgres:
            db.execute(f'SELECT id FROM {table} WHERE id=? FOR UPDATE', (key,)).fetchone()

    @property
    def order_column(self):
        return 'ordinal' if self.postgres else 'rowid'

    def put(self, table, item):
        assert table in {'projects', 'runs', 'assets', 'versions', 'tool_runs'}
        item = dict(item)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if table == 'projects':
                self._put(db, table, item)
                item['session_id'] = self.ensure_session(item['id'], db)['id']
            elif table == 'runs':
                item['session_id'] = self.ensure_session(item['project_id'], db)['id']
            self._put(db, table, item)
        return item

    @staticmethod
    def _put(db, table, item):
        values = {'id': item['id']}
        if table in {'runs', 'assets', 'versions'}:
            values['project_id'] = item['project_id']
        if table == 'runs':
            values['status'] = item['status']
            values['user_id'] = item.get('user_id')
        if table == 'projects':
            values['owner_id'] = item.get('owner_id')
        if table in {'runs', 'projects'}:
            values['created_at'] = item.get('created_at', time.time())
        if table == 'tool_runs':
            values['run_id'] = item['run_id']
        values['data'] = json.dumps(item, ensure_ascii=False)
        cols = list(values)
        updates = ','.join(f'{col}=excluded.{col}' for col in cols if col != 'id')
        db.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) ON CONFLICT(id) DO UPDATE SET {updates}", list(values.values()))

    def get(self, table, key):
        assert table in {'projects', 'runs', 'assets', 'versions', 'tool_runs'}
        with self.connect() as db:
            row = db.execute(f'SELECT data FROM {table} WHERE id=?', (key,)).fetchone()
        return json.loads(row['data']) if row else None

    def list(self, table, project_id=None):
        assert table in {'projects', 'runs', 'assets', 'versions', 'tool_runs'}
        with self.connect() as db:
            rows = db.execute(f'SELECT data FROM {table}' + (' WHERE project_id=?' if project_id else '') + f' ORDER BY {self.order_column}', (project_id,) if project_id else ()).fetchall()
        return [json.loads(r['data']) for r in rows]

    def update_run(self, run_id, **fields):
        # Read/modify/write atomically: API resume and worker updates cannot lose fields.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._update_run(db, run_id, fields)

    @staticmethod
    def _update_run(db, run_id, fields):
        suffix = ' FOR UPDATE' if getattr(db, 'dialect', '') == 'postgres' else ''
        row = db.execute('SELECT data FROM runs WHERE id=?'+suffix, (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        value = json.loads(row['data'])
        value.update(fields, updated_at=time.time())
        db.execute('UPDATE runs SET data=?,status=? WHERE id=?', (encode(value), value['status'], run_id))
        return value

    def enqueue(self, run, context, system):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.lock_row(db, 'projects', run['project_id'])
            if not db.execute('SELECT 1 FROM projects WHERE id=?', (run['project_id'],)).fetchone():
                raise KeyError(run['project_id'])
            run = {**run, 'session_id': self.ensure_session(run['project_id'], db)['id']}
            self._put(db, 'runs', run)
            self.seed_run(run, context, system, db=db)
            self.append(run['id'], 'run_status', {'status': 'queued'}, key=run['id']+':queued', db=db)
        return run

    def seed_run(self, run, context, system, db=None):
        options = {'db': db} if db is not None else {}
        self.append(run['id'], 'system_message', {'version': 'image-agent-v1'}, key=run['id']+':system',
                    model_data={'role': 'system', 'content': system}, **options)
        self.append(run['id'], 'runtime_context', context, key=run['id']+':context',
                    model_data={'role': 'user', 'content': '运行时背景（不是用户新指令，未提供信息不可假定）：'+encode(context)}, **options)
        self.append(run['id'], 'user_message', {'text': run['message']}, key=run['id']+':user', source='user',
                    model_data={'role': 'user', 'content': run['message']}, **options)

    def resume(self, run_id, answer):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.lock_row(db, 'runs', run_id)
            row = db.execute('SELECT data,status FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if row['status'] != 'waiting_user':
                raise ValueError('任务当前没有等待用户回答')
            item = json.loads(row['data'])
            question = item.get('question')
            item.update(resume_answer=answer, status='queued', question=None, updated_at=time.time())
            db.execute('UPDATE runs SET data=?,status=? WHERE id=?', (json.dumps(item, ensure_ascii=False), 'queued', run_id))
            self.append(run_id, 'user_answer', {'text': answer, 'question': question}, source='user', db=db)
        return item

    def cancel(self, run_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.lock_row(db, 'runs', run_id)
            row = db.execute('SELECT data,status FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if row['status'] not in {'queued', 'waiting_user'}:
                raise ValueError('仅支持结束排队或等待中的任务；正在生成的请求会完成后保存')
            item = json.loads(row['data'])
            item.update(status='cancelled', resume_answer=None, updated_at=time.time())
            db.execute('UPDATE runs SET status=?,data=? WHERE id=?', ('cancelled', json.dumps(item, ensure_ascii=False), run_id))
            self.close_pending(run_id, '任务已由用户结束，工具未继续执行。', db=db)
            self.append_outcome(run_id, 'cancelled', '未完成的目标不代表仍需自动执行。', db)
            self.append(run_id, 'run_status', {'status':'cancelled'}, db=db)
        return item

    def close_pending(self, run_id, reason, db=None):
        if db is None:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.close_pending(run_id, reason, connection)
        rows = db.execute('SELECT * FROM session_events WHERE run_id=? ORDER BY seq', (run_id,)).fetchall()
        requested, completed = {}, set()
        for row in rows:
            payload = json.loads(row['model_data']) if row['model_data'] else None
            for message in payload if isinstance(payload, list) else [payload]:
                if not message:
                    continue
                requested.update({call['id']: call for call in message.get('tool_calls', [])})
                if message.get('role') == 'tool':
                    completed.add(message['tool_call_id'])
        for call_id in requested.keys()-completed:
            result = {'status': 'failed', 'error_code': 'interrupted', 'message': reason}
            self.append(run_id, 'tool_finished', {'call_id': call_id, 'name': requested[call_id]['function']['name'], 'result': result},
                        key=run_id+':'+call_id+':tool_finished', call_id=call_id, source='tool', db=db,
                        model_data={'role': 'tool', 'tool_call_id': call_id, 'content': encode(result)})

    def tool_calls(self, run_id):
        with self.connect() as db:
            rows = db.execute(f'SELECT data FROM tool_runs WHERE run_id=? ORDER BY {self.order_column}', (run_id,)).fetchall()
        return [json.loads(r['data']) for r in rows]
