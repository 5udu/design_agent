import base64
import httpx


class Seedream:
    def __init__(self, config):
        self.config = config

    async def generate(self, prompt, reference=None, size='2048x2048'):
        if not self.config.seedream_api_key:
            raise RuntimeError('缺少 SEEDREAM_API_KEY')
        payload = {'model': self.config.seedream_model, 'prompt': prompt, 'size': size, 'response_format': 'b64_json', 'watermark': False}
        if reference:
            payload['image'] = reference
        async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=15)) as client:
            response = await client.post(self.config.seedream_base_url.rstrip('/') + '/images/generations',
                headers={'Authorization': f'Bearer {self.config.seedream_api_key}'}, json=payload)
            if response.is_error:
                raise RuntimeError(f'Seedream HTTP {response.status_code}: {self.config.safe_error(response.text)}')
            body = response.json()
            item = body['data'][0]
            if item.get('b64_json'):
                content = base64.b64decode(item['b64_json'])
            elif item.get('url'):
                # Download without Authorization: never forward API keys to a CDN.
                download = await client.get(item['url'])
                download.raise_for_status()
                content = download.content
            else:
                raise RuntimeError('图片服务没有返回有效图片')
        return content, {'model': body.get('model', self.config.seedream_model), 'usage': body.get('usage', {})}
