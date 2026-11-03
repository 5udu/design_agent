"""Persist content-addressed model previews; materialize bytes only at dispatch."""
import base64
import copy
import hashlib
from pathlib import Path


def freeze_images(message, directory):
    message = copy.deepcopy(message)
    if not isinstance(message.get('content'), list):
        return message
    for block in message['content']:
        if block.get('type') != 'image_url':
            continue
        url = block['image_url']['url']
        if not url.startswith('data:image/'):
            raise ValueError('模型图片必须保存为本地预览')
        header, encoded = url.split(',', 1)
        content = base64.b64decode(encoded, validate=True)
        digest = hashlib.sha256(content).hexdigest()
        relative = 'previews/'+digest+'.image'
        path = Path(directory)/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        block.clear()
        block.update(type='image_ref', path=relative, sha256=digest,
                     mime_type=header[5:].split(';')[0])
    return message


def hydrate_images(message, directory):
    message = copy.deepcopy(message)
    if not isinstance(message.get('content'), list):
        return message
    for block in message['content']:
        if block.get('type') != 'image_ref':
            continue
        path = (Path(directory)/block['path']).resolve()
        if not path.is_relative_to(Path(directory).resolve()):
            raise ValueError('预览路径越界')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != block['sha256']:
            raise ValueError('模型预览完整性检查失败')
        url = 'data:'+block['mime_type']+';base64,'+base64.b64encode(content).decode()
        block.clear()
        block.update(type='image_url', image_url={'url': url})
    return message
