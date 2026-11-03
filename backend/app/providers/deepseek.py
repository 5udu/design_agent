from typing import get_args
import httpx
from app.domain.models import ReasoningEffort


class ContextWindowExceeded(RuntimeError):
    """A provider-confirmed overflow, eligible for one compact-and-retry."""


class DeepSeek:
    def __init__(self, config):
        self.config = config

    async def complete(self, messages, tools, *, reasoning_effort: ReasoningEffort | None = None):
        effort = reasoning_effort if reasoning_effort is not None else self.config.deepseek_reasoning_effort
        if effort not in get_args(ReasoningEffort):
            raise ValueError('不支持的思考强度')
        payload = {'model': self.config.deepseek_model, 'messages': messages, 'tools': tools,
                   'thinking': {'type': 'disabled' if effort == 'off' else 'enabled'}}
        if effort != 'off':
            payload['reasoning_effort'] = effort
        body = await self._request(payload)
        choice = body['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise RuntimeError('DeepSeek 输出达到长度上限，本轮推理未完成，请缩小任务后重试。')
        return choice['message'], {'model': body.get('model'), 'usage': body.get('usage', {}),
                                  'reasoning_effort': effort}

    async def summarize(self, messages, *, max_tokens):
        body = await self._request({'model': self.config.deepseek_model, 'messages': messages,
            'thinking': {'type': 'disabled'}, 'max_tokens': max_tokens})
        choice = body['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise RuntimeError('上下文摘要被截断，保留原历史')
        return choice['message'].get('content'), {'model': body.get('model'), 'usage': body.get('usage', {})}

    async def _request(self, payload):
        if not self.config.deepseek_api_key:
            raise RuntimeError('缺少 DEEPSEEK_API_KEY')
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            response = await client.post(self.config.deepseek_base_url.rstrip('/') + '/chat/completions',
                headers={'Authorization': f'Bearer {self.config.deepseek_api_key}'},
                json=payload)
            if response.is_error:
                if response.status_code in {400, 413} and any(marker in response.text.lower() for marker in
                    ('context_length_exceeded', 'maximum context length', 'context window exceeded', 'context_window_exceeded')):
                    raise ContextWindowExceeded('模型报告上下文窗口溢出')
                raise RuntimeError(f'DeepSeek HTTP {response.status_code}: {self.config.safe_error(response.text)}')
            body = response.json()
        return body
