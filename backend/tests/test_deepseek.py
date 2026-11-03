import json

import httpx
import pytest

from app.config import Settings
from app.providers.deepseek import DeepSeek, ContextWindowExceeded


@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
async def test_thinking_request_and_tool_reasoning_round_trip(monkeypatch, effort):
    config = Settings(_env_file=None, deepseek_api_key='test-key', deepseek_reasoning_effort=effort)
    messages = [
        {'role': 'user', 'content': '读取项目'},
        {'role': 'assistant', 'content': '', 'reasoning_content': 'private-context',
         'tool_calls': [{'id': 'read', 'type': 'function', 'function': {'name': 'read_project', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'read', 'content': '{}'},
    ]
    tools = [{'type': 'function', 'function': {'name': 'read_project', 'parameters': {'type': 'object', 'properties': {}}}}]

    def respond(request):
        body = json.loads(request.content)
        assert body['thinking'] == {'type': 'enabled'}
        assert body['reasoning_effort'] == effort
        assert body['messages'] == messages and body['tools'] == tools
        assert 'max_tokens' not in body  # Use the provider's thinking-mode default budget.
        return httpx.Response(200, json={
            'model': 'deepseek-flash', 'usage': {'completion_tokens': 12},
            'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': '项目已读取', 'reasoning_content': 'private-next-context'}}],
        })

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    message, metadata = await DeepSeek(config).complete(messages, tools)
    assert message['reasoning_content'] == 'private-next-context'
    assert metadata['reasoning_effort'] == effort
    assert 'private-next-context' not in json.dumps(metadata)


async def test_truncated_thinking_does_not_look_like_a_completed_turn(monkeypatch):
    config = Settings(_env_file=None, deepseek_api_key='test-key')
    assert config.deepseek_reasoning_effort == 'max'
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': ''}}],
    }))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(RuntimeError, match='推理未完成'):
        await DeepSeek(config).complete([{'role': 'user', 'content': '读取项目'}], [])


@pytest.mark.parametrize('effort', ['off', 'low', 'high', 'max'])
async def test_request_level_effort_controls_payload_without_mutating_defaults(monkeypatch, effort):
    config=Settings(_env_file=None,deepseek_api_key='test-key',deepseek_reasoning_effort='max')
    def respond(request):
        payload=json.loads(request.content)
        assert payload['thinking']=={'type':'disabled' if effort=='off' else 'enabled'}
        if effort=='off':assert 'reasoning_effort' not in payload
        else:assert payload['reasoning_effort']==effort
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'OK'}}]})
    client_type=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client_type(transport=httpx.MockTransport(respond),**kwargs))
    _,metadata=await DeepSeek(config).complete([{'role':'user','content':'仅回复OK'}],[],reasoning_effort=effort)
    assert metadata['reasoning_effort']==effort
    assert config.deepseek_reasoning_effort=='max'


async def test_server_default_can_disable_thinking(monkeypatch):
    config=Settings(_env_file=None,deepseek_api_key='test-key',deepseek_reasoning_effort='off')
    def respond(request):
        payload=json.loads(request.content)
        assert payload['thinking']=={'type':'disabled'} and 'reasoning_effort' not in payload
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'OK'}}]})
    client_type=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client_type(transport=httpx.MockTransport(respond),**kwargs))
    _,metadata=await DeepSeek(config).complete([{'role':'user','content':'OK'}],[])
    assert metadata['reasoning_effort']=='off'


async def test_invalid_effort_is_rejected_before_network_submission():
    config=Settings(_env_file=None,deepseek_api_key='test-key')
    with pytest.raises(ValueError,match='思考强度'):
        await DeepSeek(config).complete([{'role':'user','content':'OK'}],[],reasoning_effort='medium')


@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
async def test_summary_request_is_bounded_and_does_not_accept_truncated_text(monkeypatch, finish_reason):
    config=Settings(_env_file=None,deepseek_api_key='test-key')
    def respond(request):
        payload=json.loads(request.content)
        assert payload['thinking']=={'type':'disabled'}
        assert payload['max_tokens']==256 and 'tools' not in payload and 'reasoning_effort' not in payload
        return httpx.Response(200,json={'choices':[{'finish_reason':finish_reason,'message':{'content':'历史摘要'}}]})
    client_type=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client_type(transport=httpx.MockTransport(respond),**kwargs))
    provider=DeepSeek(config)
    if finish_reason=='length':
        with pytest.raises(RuntimeError,match='摘要被截断'):
            await provider.summarize([{'role':'user','content':'原始历史'}],max_tokens=256)
    else:
        summary,_=await provider.summarize([{'role':'user','content':'原始历史'}],max_tokens=256)
        assert summary=='历史摘要'


@pytest.mark.parametrize('status,error,overflow', [
    (400,'context_length_exceeded',True), (413,'maximum context length exceeded',True),
    (400,'invalid tool arguments',False), (429,'context_length_exceeded',False),
])
async def test_only_confirmed_overflow_qualifies_for_compaction_retry(monkeypatch,status,error,overflow):
    config=Settings(_env_file=None,deepseek_api_key='test-key')
    client_type=httpx.AsyncClient
    transport=httpx.MockTransport(lambda _:httpx.Response(status,json={'error':{'message':error}}))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client_type(transport=transport,**kwargs))
    with pytest.raises(ContextWindowExceeded if overflow else RuntimeError) as raised:
        await DeepSeek(config).complete([{'role':'user','content':'继续'}],[])
    assert isinstance(raised.value,ContextWindowExceeded)==overflow
