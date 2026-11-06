"""Consistent offline backup/restore for the Compose deployment, with checksums."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'restore', 'verify'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--env-file', default='.env.production')
    parser.add_argument('--confirm-replace-data', action='store_true')
    args = parser.parse_args()
    compose = ['docker', 'compose', '--env-file', args.env_file]
    folder = args.directory.resolve()
    def call(*parts, **kwargs):
        return subprocess.run([*compose, *parts], cwd=ROOT, check=True, **kwargs)
    def verify():
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        for name in ('database.dump', 'assets.tar.gz'):
            with (folder/name).open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if digest != manifest['sha256'][name]:
                raise SystemExit('Backup checksum mismatch: '+name)
        return manifest
    if args.command == 'verify':
        verify()
        print('Backup checksums verified')
        return
    if args.command == 'restore':
        if not args.confirm_replace_data:
            raise SystemExit('Restore replaces the deployment data; pass --confirm-replace-data')
        verify()
    else:
        folder.mkdir(parents=True, exist_ok=False)
    running = call('ps', '--status', 'running', '--services', capture_output=True, text=True).stdout.split()
    services = [s for s in ('api', 'worker') if s in running]
    call('stop', 'api', 'worker')
    try:
        if args.command == 'backup':
            with (folder/'database.dump').open('wb') as output:
                call('exec', '-T', 'db', 'pg_dump', '-U', 'design', '-d', 'design', '-Fc', stdout=output)
            with (folder/'assets.tar.gz').open('wb') as output:
                call('run', '--rm', '--no-deps', '-T', 'api', 'python', '-m', 'app.ops', 'backup-assets', stdout=output)
            manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'sha256': {}}
            for name in ('database.dump', 'assets.tar.gz'):
                with (folder/name).open('rb') as source:
                    manifest['sha256'][name] = hashlib.file_digest(source, 'sha256').hexdigest()
            (folder/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            print('Backup created:', folder)
        else:
            with (folder/'database.dump').open('rb') as source:
                call('exec', '-T', 'db', 'pg_restore', '-U', 'design', '-d', 'design', '--clean', '--if-exists', '--no-owner', '--no-privileges', '--exit-on-error', stdin=source)
            with (folder/'assets.tar.gz').open('rb') as source:
                call('run', '--rm', '--no-deps', '-T', 'api', 'python', '-m', 'app.ops', 'restore-assets', stdin=source)
            print('Database and assets restored')
    except Exception:
        print('Operation failed. API and workers remain stopped; inspect the error before restarting.')
        raise
    else:
        if services:
            call('start', *services)


if __name__ == '__main__':
    main()
