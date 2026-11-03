"""Heuristic token estimates, with image pixels priced separately from Base64."""
import json
import math
from PIL import Image


def estimate_tokens(messages, tools, directory):
    total = math.ceil(len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False).encode('utf8'))/2)
    for message in messages:
        if not isinstance(message.get('content'), list):
            continue
        for block in message['content']:
            if block.get('type') == 'image_ref':
                path = (directory/block['path']).resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise ValueError('预览路径越界')
                with Image.open(path) as picture:
                    width, height = picture.size
                total += 256*(1+math.ceil(width/336)*math.ceil(height/336))
    return total
