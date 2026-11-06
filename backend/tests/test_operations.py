import hashlib
import io
import tarfile
import pytest
from app.auth import Auth
from app.config import Settings
from app.ops import restore_assets
from app.migrate import migrate
from app.persistence.repositories import Store
from app.persistence.assets import Assets
from PIL import Image
from test_storage import storage


def test_asset_restore_rejects_traversal_and_preserves_existing_files(tmp_path):
    directory = tmp_path/'data'
    (directory/'assets').mkdir(parents=True)
    (directory/'assets'/'keep.png').write_bytes(b'original')
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
        info = tarfile.TarInfo('../outside')
        info.size = 4
        bundle.addfile(info, io.BytesIO(b'evil'))
    archive.seek(0)
    with pytest.raises(ValueError):
        restore_assets(archive, directory)
    assert (directory/'assets'/'keep.png').read_bytes() == b'original'
    assert not (tmp_path/'outside').exists()


def test_asset_restore_replaces_only_asset_directories(tmp_path):
    (tmp_path/'important.db').write_bytes(b'database')
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
        info = tarfile.TarInfo('assets/restored.png')
        info.size = 5
        bundle.addfile(info, io.BytesIO(b'image'))
    archive.seek(0)
    restore_assets(archive, tmp_path)
    assert (tmp_path/'assets/restored.png').read_bytes() == b'image'
    assert (tmp_path/'important.db').read_bytes() == b'database'


def test_sqlite_import_preserves_history_and_image_checksums(storage, tmp_path):
    if not storage.postgres:
        pytest.skip('PostgreSQL import integration')
    source = Store(tmp_path/'legacy')
    project = source.put('projects', {'id': 'legacy', 'name': '历史项目'})
    run = source.put('runs', {'id': 'legacy-run', 'project_id': project['id'], 'status': 'succeeded', 'message': 'legacy'})
    source.append(run['id'], 'user_message', {'text': 'history'}, model_data={'role': 'user', 'content': 'history'})
    output = io.BytesIO()
    Image.new('RGB', (10, 20)).save(output, format='PNG')
    config = Settings(_env_file=None, data_dir=source.directory)
    asset = Assets(source, config).save_image(project['id'], run['id'], output.getvalue(), prompt='legacy', title='历史图片', requested_size='10x20', usage={})
    owner = Auth(storage).create_user('destination-owner', 'migration-password-123')
    counts = migrate(source.directory, storage, owner['username'])
    assert counts['projects'] == 1 and counts['session_events'] == 1
    assert storage.get('projects', 'legacy')['owner_id'] == owner['id']
    assert storage.events('legacy-run') == source.events('legacy-run')
    assert hashlib.sha256((storage.directory/asset['path']).read_bytes()).hexdigest() == asset['sha256']
    assert (source.directory/asset['path']).is_file()
    assert storage.append('legacy-run', 'test', {})['id'] > source.events('legacy-run')[-1]['id']
    with pytest.raises(ValueError):
        migrate(source.directory, storage, owner['username'])


def test_production_config_requires_tls_and_database():
    with pytest.raises(ValueError):
        Settings(_env_file=None, deployment_mode='production')
    Settings(_env_file=None, deployment_mode='production', database_url='postgresql://example/db', app_origin='https://design.example', cookie_secure=True)


def test_asset_and_version_metadata_commit_together(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.put('projects', {'id': 'p'})
    output = io.BytesIO()
    Image.new('RGB', (10, 10)).save(output, format='PNG')
    original = store._put
    def fail_version(db, table, item):
        if table == 'versions':
            raise RuntimeError('simulated commit failure')
        return original(db, table, item)
    monkeypatch.setattr(store, '_put', fail_version)
    with pytest.raises(RuntimeError):
        Assets(store, Settings(_env_file=None, data_dir=tmp_path)).save_image('p', 'run', output.getvalue(), prompt='test', title='test', requested_size='10x10', usage={})
    assert store.list('assets') == []
    assert store.list('versions') == []
    assert list((tmp_path/'assets').iterdir()) == []
