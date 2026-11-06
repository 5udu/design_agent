"""Transactional global limits, per-employee fairness and recoverable leases."""
import json
import time
import secrets
from app.persistence.locking import LockBusy


class TaskQueue:
    def queue_lock(self, db):
        if self.postgres:
            db.execute('SELECT pg_advisory_xact_lock(91020602)')
        else:
            db.execute('BEGIN IMMEDIATE')

    def claim(self, worker_id='local', *, global_limit=5, user_limit=2, lease_seconds=60):
        now = time.time()
        with self.connect() as db:
            self.queue_lock(db)
            if db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0] >= global_limit:
                return None
            suffix = ' FOR UPDATE OF r SKIP LOCKED' if self.postgres else ''
            row = db.execute('''SELECT r.id,r.data FROM runs r WHERE r.status='queued'
                AND (SELECT COUNT(*) FROM runs active WHERE active.status='running'
                     AND COALESCE(active.user_id,'')=COALESCE(r.user_id,''))<?
                ORDER BY COALESCE((SELECT last_started FROM worker_dispatch d WHERE d.user_id=COALESCE(r.user_id,'')),0),r.created_at,r.id
                LIMIT 1'''+suffix, (user_limit,)).fetchone()
            if not row:
                return None
            item = json.loads(row['data'])
            token = secrets.token_hex(24)
            item.update(status='running', updated_at=now)
            db.execute('UPDATE runs SET status=?,data=?,lease_token=?,lease_until=?,worker_id=? WHERE id=?',
                       ('running', json.dumps(item, ensure_ascii=False), token, now+lease_seconds, worker_id, item['id']))
            db.execute('INSERT INTO worker_dispatch(user_id,last_started) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_started=excluded.last_started', (item.get('user_id') or '', now))
        return {**item, '_lease_token': token}

    def renew_lease(self, run_id, token, lease_seconds=60):
        with self.connect() as db:
            cursor = db.execute('UPDATE runs SET lease_until=? WHERE id=? AND lease_token=? AND lease_until>?',
                                (time.time()+lease_seconds, run_id, token, time.time()))
            return cursor.rowcount == 1

    def release_lease(self, run_id, token):
        with self.connect() as db:
            self.queue_lock(db)
            row = db.execute('SELECT data,status FROM runs WHERE id=? AND lease_token=?', (run_id, token)).fetchone()
            if not row:
                return
            item = json.loads(row['data'])
            if row['status'] == 'running':
                item.update(status='queued', updated_at=time.time())
            db.execute('UPDATE runs SET status=?,data=?,lease_token=NULL,lease_until=NULL,worker_id=NULL WHERE id=? AND lease_token=?',
                       (item['status'], json.dumps(item, ensure_ascii=False), run_id, token))

    def recover_expired(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,project_id FROM runs WHERE status='running' AND (lease_until IS NULL OR lease_until<?) LIMIT 100", (time.time(),)).fetchall()
        recovered = 0
        for row in rows:
            try:
                with self.project_lock(row['project_id']), self.connect() as db:
                    self.queue_lock(db)
                    self.lock_row(db, 'runs', row['id'])
                    current = db.execute("SELECT data FROM runs WHERE id=? AND status='running' AND (lease_until IS NULL OR lease_until<?)", (row['id'], time.time())).fetchone()
                    if not current:
                        continue
                    item = json.loads(current['data'])
                    item.update(status='queued', updated_at=time.time())
                    db.execute('UPDATE runs SET status=?,data=?,lease_token=NULL,lease_until=NULL,worker_id=NULL WHERE id=?', ('queued', json.dumps(item, ensure_ascii=False), row['id']))
                    self.append(row['id'], 'run_status', {'status': 'queued', 'reason': 'worker_recovered'}, db=db)
                    recovered += 1
            except LockBusy:
                continue  # A live executor still holds the project lock.
        return recovered

    def worker_heartbeat(self, worker_id):
        with self.connect() as db:
            db.execute('INSERT INTO workers(id,updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at', (worker_id, time.time()))
            db.execute('DELETE FROM workers WHERE updated_at<?', (time.time()-86400,))
