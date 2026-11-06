"""Local operator commands. Passwords are prompted, never accepted in argv."""
import argparse
import getpass
import json
from app.auth import Auth
from app.config import settings
from app.persistence.repositories import Store


def main():
    parser = argparse.ArgumentParser(description='员工账号与历史项目管理')
    parser.add_argument('command', choices=['create-admin', 'reset-password', 'adopt-unowned', 'transfer-projects'])
    parser.add_argument('username')
    parser.add_argument('--from-user', help='转交该员工拥有的全部项目')
    args = parser.parse_args()
    store = Store(settings.data_dir, settings.database_url, settings.db_pool_size)
    auth = Auth(store)
    try:
        with store.connect() as db:
            row = db.execute('SELECT id FROM users WHERE username=?', (args.username.lower(),)).fetchone()
        if args.command in {'create-admin', 'reset-password'}:
            password = getpass.getpass('Password (12+ characters): ')
            if password != getpass.getpass('Confirm password: '):
                raise SystemExit('Passwords do not match')
            if args.command == 'create-admin':
                user = auth.create_user(args.username, password, is_admin=True)
                print('Administrator created:', user['username'])
            elif row:
                auth.set_password(row['id'], password, None)
                print('Password reset; existing sessions revoked')
            else:
                raise SystemExit('Account not found')
        else:
            if not row:
                raise SystemExit('Destination account not found')
            count = 0
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                source = None
                if args.command == 'transfer-projects':
                    source = db.execute('SELECT id FROM users WHERE username=?', (args.from_user or '',)).fetchone()
                    if not source:
                        raise SystemExit('Source account not found; specify --from-user')
                query = 'SELECT data FROM projects WHERE owner_id=?' if source else 'SELECT data FROM projects WHERE owner_id IS NULL'
                for item in db.execute(query, (source['id'],) if source else ()).fetchall():
                    project = json.loads(item['data'])
                    project['owner_id'] = row['id']
                    store._put(db, 'projects', project)
                    db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (project['id'], row['id']))
                    auth.audit(None, 'operator_project_transfer', project['id'], {'owner_id': row['id']}, db)
                    count += 1
            print(f'Transferred {count} projects to {args.username}')
    finally:
        store.close()


if __name__ == '__main__':
    main()
