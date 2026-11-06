"""Fence every worker transaction with the current execution lease."""
import time
from contextvars import ContextVar

execution_lease = ContextVar('execution_lease', default=None)


class LeaseLost(RuntimeError):
    pass


def check_lease(store, db):
    lease = execution_lease.get()
    if not lease or lease[0] != id(store):
        return
    if not store.postgres and not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    suffix = ' FOR UPDATE' if store.postgres else ''
    row = db.execute('SELECT lease_token,lease_until FROM runs WHERE id=?'+suffix, (lease[1],)).fetchone()
    if not row or row['lease_token'] != lease[2] or (row['lease_until'] or 0) <= time.time():
        raise LeaseLost('任务执行权已失效，当前执行已停止')
