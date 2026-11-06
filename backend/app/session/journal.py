"""SQLite session journal. Public readers never receive private model payloads."""
import json
import time
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SessionJournal:
    def ensure_session(self, project_id, db=None):
        if db is None:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.ensure_session(project_id, connection)
        self.lock_row(db, 'projects', project_id)
        project_row = db.execute('SELECT data FROM projects WHERE id=?', (project_id,)).fetchone()
        if not project_row:
            raise KeyError(project_id)
        row = db.execute('SELECT data FROM sessions WHERE project_id=?', (project_id,)).fetchone()
        if row:
            return json.loads(row['data'])
        session = {'id': uuid.uuid4().hex, 'project_id': project_id, 'created_at': time.time()}
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (session['id'], project_id, encode(session)))
        project = json.loads(project_row['data'])
        project['session_id'] = session['id']
        db.execute('UPDATE projects SET data=? WHERE id=?', (encode(project), project_id))
        return session

    def append(self, run_id, kind, data, *, key=None, step_id=None, call_id=None,
               source='runtime', model_data=None, db=None):
        if db is None:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.append(run_id, kind, data, key=key, step_id=step_id,
                                   call_id=call_id, source=source, model_data=model_data, db=connection)
        run = json.loads(db.execute('SELECT data FROM runs WHERE id=?', (run_id,)).fetchone()['data'])
        session_id = run.get('session_id') or self.ensure_session(run['project_id'], db)['id']
        self.lock_row(db, 'sessions', session_id)
        if key:
            existing = db.execute('SELECT * FROM session_events WHERE session_id=? AND event_key=?',
                                  (session_id, key)).fetchone()
            if existing:
                return self._event_row(existing)
        seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM session_events WHERE session_id=?',
                         (session_id,)).fetchone()[0]
        cursor = db.execute('''INSERT INTO session_events
            (session_id,seq,run_id,step_id,call_id,kind,source,data,model_data,event_key,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id''',
            (session_id, seq, run_id, step_id, call_id or data.get('call_id'), kind, source,
             encode(data), encode(model_data) if model_data is not None else None, key, time.time()))
        event_id = cursor.fetchone()[0]
        return self._event_row(db.execute('SELECT * FROM session_events WHERE id=?', (event_id,)).fetchone())

    @staticmethod
    def _event_row(row, private=False):
        result = {name: row[name] for name in ('id', 'session_id', 'seq', 'run_id', 'step_id', 'call_id', 'kind', 'source')}
        result.update(data=json.loads(row['data']), created_at=row['created'])
        if private and row['model_data'] is not None:
            result['model_data'] = json.loads(row['model_data'])
        return result

    def session_events(self, session_id, after=0, *, through=None, private=False):
        sql = 'SELECT * FROM session_events WHERE session_id=? AND seq>?'
        args = [session_id, after]
        if through is not None:
            sql += ' AND seq<=?'
            args.append(through)
        with self.connect() as db:
            return [self._event_row(row, private) for row in db.execute(sql+' ORDER BY seq', args)]

    def event_by_key(self, session_id, key, *, private=False):
        with self.connect() as db:
            row = db.execute('SELECT * FROM session_events WHERE session_id=? AND event_key=?', (session_id, key)).fetchone()
        return self._event_row(row, private) if row else None

    def cursor(self, session_id):
        with self.connect() as db:
            return db.execute('SELECT COALESCE(MAX(seq),0) FROM session_events WHERE session_id=?', (session_id,)).fetchone()[0]

    def events(self, run_id):
        with self.connect() as db:
            return [self._event_row(row) for row in db.execute(
                'SELECT * FROM session_events WHERE run_id=? ORDER BY seq', (run_id,))]

    def transition(self, run_id, kind, data, *, key=None, **fields):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._update_run(db, run_id, fields)
            self.append(run_id, kind, data, key=key, db=db)
            if fields.get('status') in {'failed', 'partial', 'cancelled'}:
                self.append_outcome(run_id, fields['status'], fields.get('summary') or fields.get('error', ''), db)
        return run

    def append_outcome(self, run_id, status, reason, db):
        self.append(run_id, 'run_outcome', {'status': status}, key=run_id+':outcome', db=db,
                    model_data={'role': 'user', 'content': f'运行状态记录（非用户新指令）：任务 {run_id} 已结束，状态 {status}。{reason}'})

    def save_tool(self, record, kind, data, *, step_id=None, model_data=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._put(db, 'tool_runs', record)
            return self.append(record['run_id'], kind, data, key=record['id']+':'+kind,
                               call_id=data.get('call_id'), step_id=step_id, model_data=model_data,
                               source='tool', db=db)
