"""Container health and safe asset archive operations (database handled by pg_dump)."""
import argparse
import shutil
import sys
import tarfile
import tempfile
import time
import socket
from pathlib import Path
from app.config import settings
from app.persistence.repositories import Store


def restore_assets(archive, directory):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='restore-', dir=directory) as temporary:
        staged = Path(temporary)
        with tarfile.open(fileobj=archive, mode='r|gz') as bundle:
            for member in bundle:
                relative = Path(member.name)
                if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] not in {'assets', 'previews'} or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError('Archive contains an invalid asset path')
                bundle.extract(member, staged, filter='data')
        for name in ('assets', 'previews'):
            target = directory/name
            # These fixed directories are the only recursive removal targets.
            if target.resolve().parent != directory or target.is_symlink():
                raise ValueError('Asset directory must stay within DATA_DIR')
            if target.exists():
                shutil.rmtree(target)
            if (staged/name).exists():
                (staged/name).replace(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['worker-health', 'backup-assets', 'restore-assets'])
    args = parser.parse_args()
    if args.command == 'backup-assets':
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as bundle:
            for name in ('assets', 'previews'):
                path = settings.data_dir/name
                if path.exists():
                    bundle.add(path, arcname=name)
    elif args.command == 'restore-assets':
        restore_assets(sys.stdin.buffer, settings.data_dir)
    else:
        store = Store(settings.data_dir, settings.database_url, settings.db_pool_size)
        try:
            with store.connect() as db:
                active = db.execute('SELECT 1 FROM workers WHERE updated_at>? AND id LIKE ? LIMIT 1', (time.time()-30, socket.gethostname()+':%')).fetchone()
            raise SystemExit(0 if active else 1)
        finally:
            store.close()


if __name__ == '__main__':
    main()
