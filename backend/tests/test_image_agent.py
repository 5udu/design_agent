import io,json
import httpx
import pytest
from PIL import Image
from auth_helpers import signed_in_client
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.config import Settings
from app.domain.models import ProjectCreate
from app.persistence.repositories import Store
from app.runtime.gateway import Gateway
from app.runtime.worker import process_run
from app.agent.graph import build_graph
from app.agent.observations import image_message

class Images:
    def __init__(self):self.calls=[]
    async def generate(self,prompt,reference=None,size=None):
        self.calls.append({'reference':reference,'size':size})
        output=io.BytesIO();Image.new('RGB',(80,80),'red').save(output,format='PNG')
        return output.getvalue(),{'model':'test-image','usage':{}}

def setup(tmp_path):
    config=Settings(_env_file=None,data_dir=tmp_path);store=Store(tmp_path)
    project=store.put('projects',{'id':'p',**ProjectCreate(name='国潮饭盒').model_dump()})
    run=store.put('runs',{'id':'r','project_id':'p','status':'running','message':'生成饭盒效果图'})
    images=Images();gateway=Gateway(store,images,config)
    return config,store,project,run,images,gateway

def call(key,name,**args):return {'id':key,'function':{'name':name},'arguments':args}

def test_project_needs_only_name_and_has_no_invented_copy_or_template():
    p=ProjectCreate(name='国潮饭盒')
    assert p.approved_copy==[] and p.brand==''
    assert set(p.model_dump())=={'name','brand','product','approved_copy'}

async def test_generated_delivery_is_full_image_and_edit_links_immutable_parent(tmp_path):
    _,store,_,run,images,gateway=setup(tmp_path)
    first=await gateway.execute(run,call('g','generate_image',prompt='真实饭盒',direction='饭盒效果图'))
    source=first['artifact'];original=(tmp_path/source['path']).read_bytes()
    second=await gateway.execute(run,call('e','edit_image',reference_asset_id=source['id'],prompt='改蓝色',title='蓝色饭盒'))
    assert source['kind']==second['artifact']['kind']=='product_render'
    assert images.calls[0]['size']=='2048x2048'
    assert images.calls[1]['reference'].startswith('data:image/png;base64,')
    assert second['artifact']['reference_asset_id']==source['id']
    assert store.get('versions',second['version_id'])['parent_version_id']==first['version_id']
    assert store.get('runs','r')['working_version_id']==second['version_id']
    assert (tmp_path/source['path']).read_bytes()==original

async def test_plain_image_inspection_does_not_force_ocr_or_claim_quality_pass(tmp_path):
    _,_,_,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call('g','generate_image',prompt='饭盒',direction='效果图'))
    async def forbidden(path):raise AssertionError('OCR was not requested')
    gateway.ocr.read=forbidden
    result=await gateway.execute(run,call('i','inspect_image',asset_id=created['artifact']['id']))
    assert result['status']=='succeeded' and result['quality_status']=='visual_review_required'
    assert 'ocr' not in result

async def test_image_agent_cannot_be_routed_back_to_document_tools(tmp_path):
    _,_,_,run,_,gateway=setup(tmp_path)
    result=await gateway.execute(run,call('p','patch_design',title='平面稿'))
    assert result['status']=='failed'

async def test_explicit_reference_without_design_version_is_still_observed(tmp_path):
    _,store,_,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call('g','generate_image',prompt='饭盒',direction='效果图'))
    source={**created['artifact'],'id':'raw-reference'};source.pop('version_id');store.put('assets',source)
    run=store.update_run(run['id'],target_asset_id=source['id'],working_version_id=None)
    result=await gateway.execute(run,call('read','read_project'))
    assert result['current_image']['id']==source['id'] and result['asset_ids']==[source['id']]

async def test_invalid_ratio_never_submits_image_request(tmp_path):
    _,store,_,run,images,gateway=setup(tmp_path)
    result=await gateway.execute(run,call('g','generate_image',prompt='饭盒',direction='效果图',aspect_ratio='bad'))
    assert result['status']=='failed' and not images.calls
    assert not store.tool_calls('r')[0].get('submitted_at')

async def test_same_agent_observes_product_and_returns_only_final_edit(tmp_path):
    config,store,project,run,images,gateway=setup(tmp_path)
    class Model:
        def __init__(self):self.turn=0;self.tools=[];self.messages=[]
        async def complete(self,messages,tools,*,reasoning_effort=None):
            self.turn+=1;self.tools=tools;self.messages=messages
            if self.turn==1:tool=call('g','generate_image',prompt='真实饭盒',direction='饭盒')
            elif self.turn==2:
                vid=store.get('runs','r')['working_version_id'];aid=store.get('versions',vid)['asset_id']
                tool=call('e','edit_image',reference_asset_id=aid,prompt='修正造型',title='饭盒修改版')
            else:return {'role':'assistant','content':'饭盒效果图已生成。'},{'model':'test','usage':{}}
            return {'role':'assistant','content':'正在处理图片','tool_calls':[{'id':tool['id'],'type':'function','function':{'name':tool['function']['name'],'arguments':json.dumps(tool['arguments'])}}]},{'model':'test','usage':{}}
    model=Model()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.db')) as cp:
        await process_run(build_graph(store,model,gateway,config,cp),store,run,config)
    assert {t['function']['name'] for t in model.tools}=={'read_project','generate_image','edit_image','inspect_image','ask_user'}
    done=store.get('runs','r');assert done['status']=='succeeded' and len(done['output_asset_ids'])==1
    assert store.get('projects','p')['current_image_id']==done['output_asset_ids'][0]
    assert any(isinstance(m.get('content'),list) and any(b['type']=='image_url' for b in m['content']) for m in model.messages)

async def test_reference_validation_history_and_full_conversation_api(tmp_path,monkeypatch):
    import app.main as server
    config,store,project,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call('g','generate_image',prompt='饭盒',direction='饭盒'))
    store.update_run('r',status='succeeded',summary='已生成饭盒。')
    store.append('r','user_answer',{'text':'保留boss字样'})
    project['current_image_id']=created['artifact']['id'];store.put('projects',project)
    store.put('assets',{'id':'foreign','project_id':'other'})
    monkeypatch.setattr(server,'store',store);monkeypatch.setattr(server,'assets',gateway.assets);monkeypatch.setattr(server,'settings',config)
    with signed_in_client(server) as client:
        assert client.post('/api/projects/p/runs',json={'message':'修改','target_asset_id':'foreign'}).status_code==400
        result=client.post('/api/projects/p/runs',json={'message':'改蓝色'}).json()
        assert result['target_asset_id']==created['artifact']['id']
        assert 'conversation_context' not in result
        session=client.get('/api/sessions/'+result['session_id']).json()
        assert any(e['kind']=='user_answer' and e['data']['text']=='保留boss字样' for e in session['events'])
        assert len(client.get('/api/projects/p?include_events=true').json()['conversations'])==2
        response=client.get('/api/assets/'+created['artifact']['id']+'/file?download=true')
        assert 'attachment' in response.headers['content-disposition']
        store.update_run(result['id'],status='succeeded')
        fresh=client.post('/api/projects/p/runs',json={'message':'全新生成','target_asset_id':''}).json()
        assert not fresh['working_version_id']


async def test_tampered_reference_is_not_observed_or_submitted(tmp_path):
    _,_,_,run,images,gateway=setup(tmp_path)
    created=await gateway.execute(run,call('g','generate_image',prompt='饭盒',direction='饭盒'))
    asset=created['artifact']
    (tmp_path/asset['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError,match='完整性'):
        image_message(asset,gateway.assets)
    edited=await gateway.execute(run,call('e','edit_image',reference_asset_id=asset['id'],prompt='改蓝色',title='蓝色'))
    assert edited['status']=='failed' and len(images.calls)==1


async def test_reference_path_cannot_escape_data_directory(tmp_path):
    _,store,_,run,images,gateway=setup(tmp_path)
    store.put('assets',{'id':'outside','project_id':'p','path':'../outside.png'})
    result=await gateway.execute(run,call('e','edit_image',reference_asset_id='outside',prompt='改蓝色',title='蓝色'))
    assert result['status']=='failed' and not images.calls


async def test_optional_ocr_failure_keeps_visual_review_required(tmp_path):
    _,_,_,run,_,gateway=setup(tmp_path)
    created=await gateway.execute(run,call('g','generate_image',prompt='饭盒',direction='饭盒'))
    async def unavailable(path):return {'status':'unavailable','items':[]}
    gateway.ocr.read=unavailable
    result=await gateway.execute(run,call('i','inspect_image',asset_id=created['artifact']['id'],check_text=True))
    assert result['ocr']['status']=='unavailable'
    assert result['quality_status']=='visual_review_required'


async def test_designated_reference_is_observed_instead_of_latest_image(tmp_path):
    _,store,_,run,_,gateway=setup(tmp_path)
    first=await gateway.execute(run,call('a','generate_image',prompt='红色饭盒',direction='红色'))
    await gateway.execute(run,call('b','generate_image',prompt='蓝色饭盒',direction='蓝色'))
    run=store.update_run('r',working_version_id=first['version_id'],target_asset_id=first['artifact']['id'])
    result=await gateway.execute(run,call('read','read_project'))
    assert result['asset_ids']==[first['artifact']['id']]
    run=store.update_run('r',working_version_id=None,target_asset_id='')
    cleared=await gateway.execute(run,call('clear','read_project'))
    assert cleared['asset_ids']==[] and cleared['current_image'] is None


async def test_read_timeout_stays_unknown_and_never_resubmits(tmp_path):
    _,store,_,run,_,gateway=setup(tmp_path)
    class TimeoutImages:
        calls=0
        async def generate(self,*args,**kwargs):
            self.calls+=1
            raise httpx.ReadTimeout('response lost after submit')
    gateway.image_provider=provider=TimeoutImages()
    original=call('g','generate_image',prompt='饭盒',direction='饭盒')
    first=await gateway.execute(run,original)
    again=await gateway.execute(run,original)
    other=await gateway.execute(run,call('g2','generate_image',prompt='饭盒',direction='饭盒'))
    assert first['error_code']==again['error_code']=='unknown_submission'
    assert store.get('tool_runs','r:g')['status']=='unknown'
    assert other['status']=='failed' and provider.calls==1


def test_api_exposes_only_image_project_inputs_and_current_routes(tmp_path,monkeypatch):
    import app.main as server
    config,store,_,_,_,gateway=setup(tmp_path)
    monkeypatch.setattr(server,'store',store);monkeypatch.setattr(server,'assets',gateway.assets);monkeypatch.setattr(server,'settings',config)
    with signed_in_client(server) as client:
        project=client.post('/api/projects',json={'name':'新图像项目'})
        assert project.status_code==201 and 'mode' not in project.json()
        assert client.post('/api/projects',json={'name':'旧模式','mode':'editable_packaging'}).status_code==422
        assert client.post('/api/projects/p/runs',json={'message':'修改','target_version_id':'old'}).status_code==422
        paths=client.get('/openapi.json').json()['paths']
        assert all('/api/versions/' not in path and '/api/evaluations/' not in path for path in paths)
        assert '/api/projects/{project_id}/restore' not in paths
        assert '/api/projects/{project_id}/select' not in paths
        assert '/api/projects/{project_id}/compare' not in paths


def test_stored_project_metadata_cannot_enable_another_agent_mode(tmp_path):
    from app.context.builder import ContextBuilder
    from app.tools.registry import TOOL_SCHEMAS
    from app.agent.instructions import SYSTEM
    config,store,project,run,_,gateway=setup(tmp_path)
    project.update(mode='editable_packaging',package='固定纸盒模板',constraints=['旧模板要求'])
    store.put('projects',project)
    context=ContextBuilder(store,None,gateway.assets,config,TOOL_SCHEMAS)
    context.ensure_run(run)
    messages=[message for node in context.nodes(run['session_id']) for message in node.messages]
    assert messages[0]=={'role':'system','content':SYSTEM}
    assert '固定纸盒模板' not in json.dumps(messages,ensure_ascii=False)
    assert '旧模板要求' not in json.dumps(messages,ensure_ascii=False)
