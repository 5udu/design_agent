"""One-time import of old trace events and available LangGraph snapshots."""
import json
import sqlite3
from contextlib import nullcontext, closing
from app.context.images import freeze_images


def migrate(store):
    with store.connect() as db:
        old = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        if not old:
            return
    from langgraph.checkpoint.sqlite import SqliteSaver
    checkpoint_path = store.directory/'checkpoints.db'
    snapshots = closing(sqlite3.connect(checkpoint_path, check_same_thread=False)) if checkpoint_path.exists() else nullcontext(None)
    with snapshots as connection, store.connect() as db:
        cp = SqliteSaver(connection) if connection else None
        db.execute('BEGIN IMMEDIATE')
        # Another Store may have completed the migration while this one waited.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return
        for row in db.execute('SELECT data FROM runs ORDER BY rowid').fetchall():
            run = json.loads(row['data'])
            run['session_id'] = store.ensure_session(run['project_id'], db)['id']
            run.pop('conversation_context', None)
            if 'turns' in run:
                run['steps'] = run.pop('turns')
            store._put(db, 'runs', run)
            history = db.execute('SELECT * FROM events WHERE run_id=? ORDER BY id', (run['id'],)).fetchall()
            saved = cp.get_tuple({'configurable': {'thread_id': run['id']}}) if cp else None
            messages = saved.checkpoint['channel_values'].get('messages', []) if saved else []
            user = store.append(run['id'], 'user_message', {'text': run['message']}, key=run['id']+':user', source='user',
                                model_data={'role': 'user', 'content': run['message']}, db=db)
            original_user = next((event for event in history if event['kind'] == 'user_message'), None)
            created = original_user['created'] if original_user else run.get('created_at', user['created_at'])
            db.execute('UPDATE session_events SET created=? WHERE id=?', (created, user['id']))
            for event in history:
                if event['kind'] == 'user_message':
                    continue
                data = json.loads(event['data'])
                if event['kind'] == 'assistant_message':
                    data['phase'] = 'final' if data.get('text') == run.get('summary') else 'progress'
                imported = store.append(run['id'], event['kind'], data, key='legacy:'+str(event['id']), source='legacy', db=db)
                db.execute('UPDATE session_events SET created=? WHERE id=?', (event['created'], imported['id']))
            tail = messages[2:] if len(messages) >= 2 else []
            if not tail:
                tail = [{'role': 'user', 'content': json.loads(event['data']).get('text', '')}
                        for event in history if event['kind'] == 'user_answer']
                if run.get('summary'):
                    tail.append({'role': 'assistant', 'content': run['summary']})
            if tail:
                frozen = [freeze_images(message, store.directory) for message in tail]
                store.append(run['id'], 'legacy_history', {'restored_from_checkpoint': bool(messages), 'message_count': len(tail)},
                             key=run['id']+':legacy', source='legacy', model_data=frozen, db=db)
            if saved:
                run['steps'] = saved.checkpoint['channel_values'].get('turns', run.get('steps', 0))
                store._put(db, 'runs', run)
            if run['status'] not in {'running', 'queued', 'waiting_user'}:
                store.close_pending(run['id'], '历史任务已结束，缺失的工具结果无法确认。', db)
        db.execute('DROP TABLE events')
