"""Small DB-API boundary: SQLite for development, pooled PostgreSQL for deployment."""
import hashlib
from contextlib import contextmanager


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self.cursor.rowcount


class PostgresConnection:
    dialect = 'postgres'

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, args=()):
        # Only application-owned SQL passes through this boundary. Values always
        # remain bound parameters, including user-supplied text containing '?'.
        if sql == 'BEGIN IMMEDIATE':
            sql = 'SELECT 1'  # psycopg already starts a transaction on first use.
        return Cursor(self.connection.execute(sql.replace('?', '%s'), args))

    def executemany(self, sql, args):
        with self.connection.cursor() as cursor:
            cursor.executemany(sql.replace('?', '%s'), args)

    def executescript(self, sql):
        for statement in sql.split(';'):
            if statement.strip():
                self.execute(statement)


def advisory_key(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)


@contextmanager
def postgres_project_lock(pool, project_id):
    from app.persistence.locking import LockBusy
    with pool.connection() as connection:
        key = advisory_key('project:'+project_id)
        acquired = connection.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (key,)).fetchone()['acquired']
        connection.commit()
        if not acquired:
            raise LockBusy('项目正在更新，请稍后重试')
        try:
            yield
        finally:
            try:
                connection.execute('SELECT pg_advisory_unlock(%s)', (key,))
                connection.commit()
            except Exception:
                connection.close()  # Never return a session with an unknown lock to the pool.
                raise
