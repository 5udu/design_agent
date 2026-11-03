"""Persist image files and metadata; validate ownership and integrity on reads."""
import hashlib
import io
import time
from PIL import Image
from app.persistence.repositories import uid


class Assets:
    def __init__(self, store, config):
        self.store, self.config = store, config

    def get(self, project_id, asset_id):
        asset = self.store.get('assets', asset_id)
        if not asset or asset['project_id'] != project_id:
            raise ValueError('图片不属于当前项目')
        path = (self.config.data_dir / asset['path']).resolve()
        if not path.is_relative_to(self.config.data_dir.resolve()) or not path.is_file():
            raise ValueError('图片不可读取')
        if asset.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
            raise ValueError('图片完整性检查失败，请恢复原文件')
        return asset, path

    def save_image(self, project_id, run_id, content, *, prompt, title, requested_size, usage, source=None):
        if source and source['project_id'] != project_id:
            raise ValueError('参考图片不属于当前项目')
        with Image.open(io.BytesIO(content)) as picture:
            picture.verify()
        asset_id, version_id = uid(), uid()
        filename = 'assets/' + asset_id + '.png'
        path = self.config.data_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(content)) as picture:
            width, height = picture.size
            picture.save(path, format='PNG')
        asset = {'id': asset_id, 'project_id': project_id, 'version_id': version_id,
                 'path': filename, 'url': f'/api/assets/{asset_id}/file', 'title': title, 'prompt': prompt,
                 'kind': 'product_render', 'width': width, 'height': height, 'usage': usage,
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'created_at': time.time(),
                 'run_id': run_id, 'reference_asset_id': source['id'] if source else None,
                 'requested_size': requested_size}
        self.store.put('assets', asset)
        self.store.put('versions', {'id': version_id, 'project_id': project_id,
            'parent_version_id': source.get('version_id') if source else None, 'asset_id': asset_id,
            'quality_status': 'visual_review_required', 'run_id': run_id, 'created_at': time.time()})
        return asset
