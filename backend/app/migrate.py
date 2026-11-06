"""Offline SQLite -> PostgreSQL import. Never changes the source directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from app.auth import Auth
from app.config import settings
from app.persistence.repositories import Store


def migrate(source, target, owner_username):
    source = Path(source).resolve()
    if not target.postgres or source == target.directory.resolve():
        raise ValueError('Import requires PostgreSQL and separate source/destination directories')
    connection = sqlite3.connect((source/'application.db').as_uri()+'?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    tables = ('projects', 'runs', 'sessions', 'assets', 'versions', 'tool_runs', 'session_events')
    allowed = {
        'projects': {'id', 'data', 'owner_id', 'created_at'},
        'runs': {'id', 'project_id', 'status', 'data', 'user_id', 'created_at', 'lease_token', 'lease_until', 'worker_id'},
        'sessions': {'id', 'project_id', 'data'},
        'assets': {'id', 'project_id', 'data'},
        'versions': {'id', 'project_id', 'data'},
        'tool_runs': {'id', 'run_id', 'data'},
        'session_events': {'id', 'session_id', 'seq', 'run_id', 'step_id', 'call_id', 'kind', 'source', 'data', 'model_data', 'event_key', 'created'},
    }
    try:
        if connection.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','waiting_user') LIMIT 1").fetchone():
            raise ValueError('Finish or cancel all queued, running and waiting tasks before importing')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone():
            raise ValueError('Upgrade the local session schema before importing')
        snapshot = {table: [dict(r) for r in connection.execute(f'SELECT * FROM {table} ORDER BY '+('id' if table == 'session_events' else 'rowid'))] for table in tables}
    finally:
        connection.close()
    with target.connect() as db:
        owner = db.execute('SELECT id FROM users WHERE username=? AND disabled=0', (owner_username.lower(),)).fetchone()
        if not owner:
            raise ValueError('Create the destination owner account before importing')
        if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in tables):
            raise ValueError('Destination business tables must be empty')
    if any((target.directory/name).exists() for name in ('assets', 'previews')):
        raise ValueError('Destination assets/previews directories must not exist')
    references = {}
    for row in snapshot['assets']:
        asset = json.loads(row['data'])
        references[asset['path']] = asset.get('sha256')
    def collect(value):
        if isinstance(value, dict):
            if value.get('type') == 'image_ref':
                references[value['path']] = value.get('sha256')
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    for event in snapshot['session_events']:
        if event['model_data']:
            collect(json.loads(event['model_data']))
    for relative, digest in references.items():
        path = (source/relative).resolve()
        if not any(path.is_relative_to(source/name) for name in ('assets', 'previews')) or not path.is_file():
            raise ValueError('Missing or invalid source asset')
        if digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Source asset checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='migration-', dir=target.directory) as temporary:
        staged = Path(temporary)
        for relative in references:
            destination = staged/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source/relative, destination)
        moved = []
        try:
            with target.connect() as db:
                db.execute('SELECT pg_advisory_xact_lock(91020601)')
                # Recheck under exclusive business-table locks before the first write.
                db.execute('LOCK TABLE projects,runs,sessions,assets,versions,tool_runs,session_events IN ACCESS EXCLUSIVE MODE')
                if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in tables):
                    raise ValueError('Destination changed during import')
                for table in tables:
                    for original in snapshot[table]:
                        row = dict(original)
                        if not set(row).issubset(allowed[table]):
                            raise ValueError('Unsupported source table columns')
                        if table in {'projects', 'runs'}:
                            data = json.loads(row['data'])
                            if table == 'projects':
                                data['owner_id'] = owner['id']
                                row['owner_id'] = owner['id']
                            else:
                                # Historical actor identity cannot be inferred.
                                data['user_id'] = None
                                row.update(user_id=None, lease_token=None, lease_until=None, worker_id=None)
                            row['created_at'] = data.get('created_at', row.get('created_at', 0))
                            row['data'] = json.dumps(data, ensure_ascii=False)
                        columns = list(row)
                        db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", list(row.values()))
                db.execute("SELECT setval(pg_get_serial_sequence('session_events','id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM session_events")
                for name in ('assets', 'previews'):
                    if (staged/name).exists():
                        (staged/name).replace(target.directory/name)
                        moved.append(name)
                Auth(target).audit(None, 'sqlite_import', owner['id'], {table: len(rows) for table, rows in snapshot.items()}, db)
        except BaseException:
            for name in moved:
                (target.directory/name).replace(staged/name)
            raise
    return {table: len(rows) for table, rows in snapshot.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    target = Store(settings.data_dir, settings.database_url, settings.db_pool_size)
    try:
        print(json.dumps(migrate(args.source, target, args.owner), ensure_ascii=False))
    finally:
        target.close()


if __name__ == '__main__':
    main()
