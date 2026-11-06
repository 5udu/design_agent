"""Delete project records and checkpoints; retry unreferenced file cleanup."""
import json
from app.persistence.locking import LockBusy, preview_lock, project_lock


def image_refs(value):
    if isinstance(value, dict):
        if value.get('type') == 'image_ref' and isinstance(value.get('path'), str):
            yield value['path']
        for child in value.values():
            yield from image_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from image_refs(child)


def live_paths(db):
    for row in db.execute('SELECT data FROM assets'):
        path = json.loads(row['data']).get('path')
        if path:
            yield path
    for row in db.execute('SELECT model_data FROM session_events WHERE model_data IS NOT NULL'):
        yield from image_refs(json.loads(row['model_data']))


def cleanup_path(directory, relative):
    root = directory.resolve()
    path = (root/relative).resolve()
    # Only known asset/cache directories can be removed, never databases/locks.
    if not any(path.is_relative_to(root/name) and path != root/name for name in ('assets', 'previews')):
        raise ValueError('图片路径异常，未删除项目')
    if path.is_dir():
        raise ValueError('图片路径不是文件，未删除项目')
    return path


def _collect_garbage(store):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        queued = db.execute('SELECT path FROM file_cleanup').fetchall()
        if not queued:
            return 0
        protected = {(store.directory/path).resolve() for path in live_paths(db)}
        for row in queued:
            try:
                path = cleanup_path(store.directory, row['path'])
                if path not in protected:
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue
            db.execute('DELETE FROM file_cleanup WHERE path=?', (row['path'],))
        return db.execute('SELECT COUNT(*) FROM file_cleanup').fetchone()[0]


def collect_garbage(store):
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM file_cleanup LIMIT 1').fetchone():
            return 0
    try:
        with preview_lock(store.directory):
            return _collect_garbage(store)
    except LockBusy:
        return None


def delete_project(store, project_id):
    with store.project_lock(project_id):
        with store.connect() as db:
            checkpoint_path = store.directory/'checkpoints.db'
            if not store.postgres and checkpoint_path.exists():
                db.execute('ATTACH DATABASE ? AS execution', (str(checkpoint_path),))
            db.execute('BEGIN IMMEDIATE')
            store.lock_row(db, 'projects', project_id)
            if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
                raise KeyError(project_id)
            if db.execute("SELECT 1 FROM runs WHERE project_id=? AND status IN ('queued','running','waiting_user')", (project_id,)).fetchone():
                raise ValueError('项目有正在执行或等待回答的任务，请先完成或结束任务再删除')
            assets = [json.loads(row['data']) for row in db.execute('SELECT data FROM assets WHERE project_id=?', (project_id,))]
            paths = {asset['path'] for asset in assets if asset.get('path')}
            for row in db.execute('SELECT model_data FROM session_events WHERE session_id IN (SELECT id FROM sessions WHERE project_id=?) AND model_data IS NOT NULL', (project_id,)):
                paths.update(image_refs(json.loads(row['model_data'])))
            # Validate every candidate before any records are changed.
            paths = {str(cleanup_path(store.directory, path).relative_to(store.directory.resolve())) for path in paths}
            runs = db.execute('SELECT COUNT(*) FROM runs WHERE project_id=?', (project_id,)).fetchone()[0]
            if store.postgres:
                db.execute('INSERT INTO checkpoint_cleanup(run_id) SELECT id FROM runs WHERE project_id=? ON CONFLICT(run_id) DO NOTHING', (project_id,))
            elif checkpoint_path.exists():
                for table in ('checkpoints', 'writes'):
                    if db.execute("SELECT 1 FROM execution.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        db.execute(f'DELETE FROM execution.{table} WHERE thread_id IN (SELECT id FROM main.runs WHERE project_id=?)', (project_id,))
            db.execute('DELETE FROM tool_runs WHERE run_id IN (SELECT id FROM runs WHERE project_id=?)', (project_id,))
            db.execute('DELETE FROM session_events WHERE session_id IN (SELECT id FROM sessions WHERE project_id=?)', (project_id,))
            for table in ('sessions', 'assets', 'versions', 'runs'):
                db.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM projects WHERE id=?', (project_id,))
            db.executemany('INSERT INTO file_cleanup(path) VALUES(?) ON CONFLICT(path) DO NOTHING', [(path,) for path in paths])
        # Checkpoint transactions finish before taking the preview lock: an
        # async worker may still be committing another project's checkpoint.
        pending = collect_garbage(store)
    return {'id': project_id, 'deleted': True, 'deleted_runs': runs, 'deleted_assets': len(assets), 'cleanup_pending': pending is None or bool(pending)}
