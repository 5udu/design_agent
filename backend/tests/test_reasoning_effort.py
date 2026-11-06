import pytest
from auth_helpers import signed_in_client
from app.config import Settings
from app.persistence.assets import Assets
from app.persistence.repositories import Store


@pytest.fixture
def client(tmp_path,monkeypatch):
    import app.main as server
    config=Settings(_env_file=None,data_dir=tmp_path,deepseek_reasoning_effort='max')
    store=Store(tmp_path)
    store.put('projects',{'id':'p','name':'思考强度测试'})
    monkeypatch.setattr(server,'settings',config)
    monkeypatch.setattr(server,'store',store)
    monkeypatch.setattr(server,'assets',Assets(store,config))
    with signed_in_client(server) as api:
        yield api,store,config


@pytest.mark.parametrize('effort',['off','low','high','max'])
def test_request_choice_is_saved_to_the_run(client,effort):
    api,store,_=client
    response=api.post('/api/projects/p/runs',json={'message':'生成图片','reasoning_effort':effort})
    assert response.status_code==202
    run=response.json()
    assert run['reasoning_effort']==effort
    assert store.get('runs',run['id'])['reasoning_effort']==effort
    assert api.get('/api/runs/'+run['id']).json()['run']['reasoning_effort']==effort


@pytest.mark.parametrize('effort',['medium','none'])
def test_unknown_effort_does_not_queue_a_task(client,effort):
    api,store,_=client
    assert api.post('/api/projects/p/runs',json={'message':'生成图片','reasoning_effort':effort}).status_code==422
    assert store.list('runs')==[]


def test_missing_choice_uses_server_default_and_health_advertises_choices(client):
    api,_,config=client
    config.deepseek_reasoning_effort='off'
    response=api.post('/api/projects/p/runs',json={'message':'仅回复OK'})
    assert response.status_code==202 and response.json()['reasoning_effort']=='off'
    settings=api.get('/api/health').json()['agent_settings']
    assert settings['thinking']=='disabled' and settings['reasoning_effort']=='off'
    assert settings['supported_reasoning_efforts']==['off','low','high','max']
