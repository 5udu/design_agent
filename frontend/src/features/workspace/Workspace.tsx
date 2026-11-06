import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {ProjectMembers} from './ProjectMembers';
import {api} from '../../api/client';
import {AgentTrace} from './AgentTrace';
import {mergeEvents,reconcileConversation} from './traceProjection';
import {useSessionEvents} from './useSessionEvents';
import {ReasoningSelector} from './ReasoningSelector';
import {useReasoningEffort} from './useReasoningEffort';
import {Icon} from './Icon';
import {Dialog} from './Dialog';
import {ProjectSidebar} from './ProjectSidebar';
import {DeleteProjectDialog} from './DeleteProjectDialog';
import type {Project,Asset,Run,Conversation,ProjectDetail,AgentEvent} from '../../types';

function Text({value}:{value:string}){return <p className="message-text">{value.split(/(\*\*[^*]+\*\*)/g).map((part,i)=>part.startsWith('**')?<strong key={i}>{part.slice(2,-2)}</strong>:part)}</p>;}
const states:Record<string,string>={queued:'排队中',running:'正在创作',waiting_user:'等待你的回答',succeeded:'',partial:'本轮保留了部分结果',failed:'本轮未完成',cancelled:'已结束'};

export function Workspace({account}:{account:ReactNode}){
 const [membersOpen,setMembersOpen]=useState(false);
 const [reasoningEffort,setReasoningEffort]=useReasoningEffort();
 const [projects,setProjects]=useState<Project[]>([]),[project,setProject]=useState<Project|null>(null),[assets,setAssets]=useState<Asset[]>([]),[conversations,setConversations]=useState<Conversation[]>([]);
 const [activeId,setActiveId]=useState(''),[message,setMessage]=useState(''),[reference,setReference]=useState('');
 const [binding,setBinding]=useState<{id:string;projectId:string;cursor:number}|null>(null);
 const [error,setError]=useState(''),[sending,setSending]=useState(false),[loading,setLoading]=useState(true),[creating,setCreating]=useState(false),[saving,setSaving]=useState(false),[sidebar,setSidebar]=useState(false),[preview,setPreview]=useState<Asset|null>(null);
 const [deleteTarget,setDeleteTarget]=useState<Project|null>(null),[deleting,setDeleting]=useState(false),[deleteError,setDeleteError]=useState(''),[toast,setToast]=useState(''),[showLatest,setShowLatest]=useState(false);
 const [form,setForm]=useState({name:'',brand:'',product:'',copy:''});
 const [createError,setCreateError]=useState('');
 const projectId=useRef(''),requestId=useRef(0),scroll=useRef<HTMLDivElement>(null),bottom=useRef<HTMLDivElement>(null),stick=useRef(true),input=useRef<HTMLTextAreaElement>(null);
 const active=conversations.find(c=>c.run.id===activeId)?.run;
 const busy=!!active&&['queued','running'].includes(active.status),waiting=active?.status==='waiting_user';
 const readonly=project?.role==='viewer';
 const refAsset=assets.find(a=>a.id===reference);
 function upsert(run:Run,events?:AgentEvent[]){setConversations(previous=>{
  const found=previous.find(c=>c.run.id===run.id);
  const merged=reconcileConversation(found,run,events);
  return found?previous.map(c=>c.run.id===run.id?merged:c):[...previous,merged];
 });}
 async function loadProjects(){const list:Project[]=[];while(true){const page=await api<Project[]>('/projects?limit=200&offset='+list.length);list.push(...page);if(page.length<200)break;}setProjects(list.reverse());return list;}
 async function openProject(id:string){
  const ticket=++requestId.current;setBinding(null);projectId.current=id;setLoading(true);setError('');setActiveId('');setConversations([]);setAssets([]);setReference('');setSidebar(false);setMessage('');setPreview(null);setShowLatest(false);
  try{const d=await api<ProjectDetail>('/projects/'+id+'?include_events=true');if(ticket!==requestId.current)return;
   setProject(d.project);setBinding({id:d.session.id,projectId:id,cursor:d.cursor});setAssets(d.assets);setConversations(d.conversations);setActiveId(d.runs.find(r=>['queued','running','waiting_user'].includes(r.status))?.id||'');
   const current=d.assets.find(a=>a.id===d.project.current_image_id)||d.assets.at(-1);setReference(current?.id||'');
   localStorage.setItem('packaging-project',id);window.history.replaceState(null,'','?project='+id);stick.current=true;
  }catch(e){if(ticket===requestId.current)setError((e as Error).message);}finally{if(ticket===requestId.current)setLoading(false);}
 }
 async function refresh(id:string){const d=await api<ProjectDetail>('/projects/'+id+'?include_events=true');if(projectId.current!==id)return;setProject(d.project);setAssets(previous=>[...new Map([...d.assets,...previous].map(asset=>[asset.id,asset])).values()]);for(const c of d.conversations)upsert(c.run,c.events);}
 function clearWorkspace(){
  requestId.current++;projectId.current='';setBinding(null);setProject(null);setConversations([]);setAssets([]);setActiveId('');setReference('');setMessage('');setPreview(null);setError('');setLoading(false);setSidebar(false);setShowLatest(false);stick.current=true;
  localStorage.removeItem('packaging-project');window.history.replaceState(null,'',window.location.pathname);
 }
 async function projectRemoved(id:string){
  const list=await loadProjects();
  if(projectId.current===id){if(list.length)await openProject(list.at(-1)!.id);else clearWorkspace();}
 }
 async function deleteProject(){
  if(!deleteTarget||deleting)return;
  const target=deleteTarget;setDeleting(true);setDeleteError('');
  try{
   const result=await api<{cleanup_pending:boolean}>('/projects/'+target.id,undefined,'DELETE');
   setDeleteTarget(null);setToast(result.cleanup_pending?'项目已删除，被占用的图片文件将在下次启动时继续清理':'项目已删除');
  }catch(error){setDeleteError((error as Error).message);return;}finally{setDeleting(false);}
  await projectRemoved(target.id).catch(error=>setError(error.message));
 }
 useEffect(()=>{let disposed=false;loadProjects().then(list=>{if(disposed)return;if(list.length){const id=new URLSearchParams(location.search).get('project')||localStorage.getItem('packaging-project');void openProject(list.find(p=>p.id===id)?.id||list.at(-1)!.id);}else setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});return()=>{disposed=true;requestId.current++;};},[]);
 useSessionEvents(binding,{
  event:event=>{
   const id=binding?.projectId;if(!id||projectId.current!==id||event.session_id!==binding?.id)return;
   setConversations(previous=>{
    const found=previous.find(c=>c.run.id===event.run_id);
    if(!found){
     if(event.kind!=='user_message')return previous;
     return [...previous,{run:{id:event.run_id,session_id:event.session_id,project_id:id,message:event.data.text,status:'queued'},events:[event]}];
    }
    return previous.map(c=>c.run.id===event.run_id?{run:event.kind==='run_status'?{...c.run,...event.data}:c.run,events:mergeEvents(c.events,[event])}:c);
   });
   if(event.kind==='user_message')setActiveId(event.run_id);
   if(event.kind==='artifact_created'){const asset=event.data.asset as Asset;setAssets(previous=>[...previous.filter(a=>a.id!==asset.id),asset]);if(asset.kind==='product_render')setReference(asset.id);}
  },
  run:detail=>{if(detail.run.project_id!==projectId.current)return;upsert(detail.run,detail.events);if(['succeeded','partial','failed','cancelled'].includes(detail.run.status))void refresh(detail.run.project_id).catch(e=>{if(projectId.current===detail.run.project_id)setError(e.message);});},
  error:setError,
  deleted:id=>{if(projectId.current===id)void projectRemoved(id).catch(error=>setError(error.message));},
 });
 useEffect(()=>{if(stick.current)bottom.current?.scrollIntoView({behavior:'smooth',block:'end'});},[conversations]);
 useEffect(()=>{if(!toast)return;const timer=window.setTimeout(()=>setToast(''),5000);return()=>window.clearTimeout(timer);},[toast]);
 useEffect(()=>{if(input.current){input.current.style.height='auto';input.current.style.height=Math.min(160,Math.max(44,input.current.scrollHeight))+'px';}},[message]);
 useEffect(()=>{const listener=(e:KeyboardEvent)=>{if(e.key==='Escape'){setPreview(null);setCreating(false);setSidebar(false);}};window.addEventListener('keydown',listener);return()=>window.removeEventListener('keydown',listener);},[]);
 async function create(){if(!form.name.trim()||saving)return;setSaving(true);setCreateError('');try{const next=await api<Project>('/projects',{name:form.name.trim(),brand:form.brand.trim(),product:form.product.trim(),approved_copy:form.copy.split('\n').map(x=>x.trim()).filter(Boolean)});await loadProjects();setCreating(false);await openProject(next.id);}catch(e){setCreateError((e as Error).message);}finally{setSaving(false);}}
 async function send(text=message){
  if(readonly)return;if(!text.trim()||busy||sending)return;setSending(true);setError('');stick.current=true;
  try{let current=project;if(!current){current=await api<Project>('/projects',{name:text.trim().slice(0,20)});await loadProjects();projectId.current=current.id;setProject(current);setBinding({id:current.session_id,projectId:current.id,cursor:0});window.history.replaceState(null,'','?project='+current.id);}
   if(waiting&&active){const resumed=await api<Run>('/runs/'+active.id+'/resume',{answer:text});if(projectId.current!==current.id)return;upsert(resumed);}
   else{const next=await api<Run>('/projects/'+current.id+'/runs',{message:text,target_asset_id:reference,reasoning_effort:reasoningEffort});if(projectId.current!==current.id)return;upsert(next,[]);setActiveId(next.id);}setMessage('');input.current?.focus();
  }catch(e){setError((e as Error).message);}finally{setSending(false);}}
 async function cancel(){if(!active)return;const id=active.project_id;try{const run=await api<Run>('/runs/'+active.id+'/cancel',{});if(projectId.current!==id)return;upsert(run);void refresh(id).catch(e=>{if(projectId.current===id)setError(e.message);});}catch(e){if(projectId.current===id)setError((e as Error).message);}}
 function useImage(asset:Asset){setReference(asset.id);setPreview(null);input.current?.focus();}
 function imagesFor(c:Conversation){const candidates=c.events.filter(e=>e.kind==='artifact_created').map(e=>e.data.asset as Asset);const ids=c.run.output_asset_ids;return ids?.length?ids.map(id=>assets.find(a=>a.id===id)||candidates.find(a=>a.id===id)).filter((a):a is Asset=>!!a):candidates;}
 const empty=conversations.length===0;
 return <div className="app-shell">
  <ProjectSidebar projects={projects} currentId={project?.id} open={sidebar} onClose={()=>setSidebar(false)} onCreate={()=>{setForm({name:'',brand:'',product:'',copy:''});setCreateError('');setCreating(true);}} onOpen={id=>void openProject(id)} onDelete={project=>{setDeleteTarget(project);setDeleteError('');}}/>
  <main className="main-workspace">
   <header className="workspace-header"><button className="icon-button sidebar-toggle" aria-label="打开项目列表" onClick={()=>setSidebar(true)}><Icon name="sidebar"/></button><div className="project-title">{project?.name||'新图像项目'}<span>图像工作区</span></div>{project&&<button className="members-button" onClick={()=>setMembersOpen(true)}>项目成员</button>}{account}<span className="header-status"><i className={busy?'pulse':''}/>{busy?'Agent 正在创作':waiting?'等待你的回答':'图像生成与编辑'}</span></header>
   {error&&<div className="error-banner" role="alert"><span>{error}</span><button aria-label="关闭提示" onClick={()=>setError('')}><Icon name="close"/></button></div>}
   <div className={'work-area '+(empty?'is-empty':'')}>
    <div className="messages" ref={scroll} onScroll={()=>{const el=scroll.current;if(el){stick.current=el.scrollHeight-el.scrollTop-el.clientHeight<140;setShowLatest(!stick.current);}}}>
     {empty?<div className="welcome"><span className="welcome-symbol"><Icon name="image"/></span><h1>{loading?'正在打开项目…':'想把什么变成图片？'}</h1><p>描述产品、风格，或说说你想改的地方。</p><div className="suggestions"><button onClick={()=>{setMessage('为 boss 品牌生成一个国潮饭盒的真实产品效果图，饭盒本体采用朱红与米白配色、祥云图案，三分之四视角，干净背景。');input.current?.focus();}}>国潮饭盒效果图 <span>↗</span></button><button onClick={()=>{setMessage('生成一只极简风香水瓶的产品效果图，透明玻璃、柔和自然光、干净背景。');input.current?.focus();}}>极简香水瓶 <span>↗</span></button></div></div>:<div className="conversation-list">{conversations.map(c=><article className="conversation" key={c.run.id} data-run-id={c.run.id}>
      <div className="user-message"><Text value={c.run.message}/></div>
      <div className="assistant-message"><div className="assistant-avatar"><Icon name="image"/></div><div className="assistant-body">
       <AgentTrace conversation={c}/>
       {c.events.filter(e=>e.kind==='question'||e.kind==='user_answer').map(e=>e.kind==='question'?<div className="question" key={e.id}><Text value={e.data.question}/>{c.run.status==='waiting_user'&&<div className="question-options">{(e.data.options||[]).map((option:string)=><button key={option} disabled={sending||readonly} onClick={()=>void send(option)}>{option}</button>)}</div>}</div>:<div className="reply-message" key={e.id}><span>你的补充</span><Text value={e.data.text}/></div>)}
       {imagesFor(c).length>0&&<div className="image-gallery">{imagesFor(c).map(asset=><figure className="image-card" key={asset.id}><button className="image-open" aria-label={'查看图片：'+asset.title} onClick={()=>setPreview(asset)}><img src={asset.url} alt={asset.title} loading="lazy"/></button><figcaption><div><strong>{asset.title||'生成图片'}</strong><span>{asset.width} × {asset.height}</span></div><div className="image-actions"><button title="基于此图修改" aria-label={'基于此图修改：'+asset.title} className={reference===asset.id?'is-reference':''} disabled={readonly} onClick={()=>useImage(asset)}><Icon name="image"/></button><a aria-label={'下载图片：'+asset.title} title="下载图片" href={asset.url+'?download=true'} download><Icon name="download"/></a></div></figcaption></figure>)}</div>}
       {c.run.summary&&<Text value={c.run.summary}/>} {c.run.error&&<p className="run-error">{c.run.error}</p>}
       {!['queued','running','waiting_user','succeeded'].includes(c.run.status)&&<span className="run-state">{states[c.run.status]}</span>}
      </div></div>
     </article>)}</div>}
     <div ref={bottom}/>
    </div>
    <div className="composer-shell">
     {showLatest&&!empty&&<button className="jump-to-latest" onClick={()=>{stick.current=true;setShowLatest(false);bottom.current?.scrollIntoView({behavior:'smooth',block:'end'});}}><Icon name="down"/>回到底部</button>}
     {waiting&&<div className="waiting-notice"><span>补充回答后，Agent 会继续这个任务。</span><button disabled={readonly} onClick={()=>void cancel()}>结束此任务</button></div>}
     <form className="composer" onSubmit={e=>{e.preventDefault();void send();}}>
      {refAsset&&<div className="reference-chip"><img src={refAsset.url} alt="当前参考图"/><span title={refAsset.title}>参考：{refAsset.title||'当前图片'}</span><button type="button" aria-label="移除参考图" title="移除参考图，开始新的创作" onClick={()=>setReference('')}><Icon name="close"/></button></div>}
      <textarea ref={input} aria-label="图像需求或修改意见" placeholder={waiting?'输入你的回答…':refAsset?'描述你希望修改的地方…':'描述想生成的图片，例如：一个国潮饭盒的产品效果图…'} value={message} disabled={readonly||busy||sending||loading} onChange={e=>setMessage(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/>
      <div className="composer-bottom"><div className="composer-controls"><span className="model-label"><span className="online-dot"/>DeepSeek · Seedream</span><ReasoningSelector value={(busy||waiting)?active?.reasoning_effort||'max':reasoningEffort} onChange={setReasoningEffort} disabled={readonly||busy||waiting||sending||loading}/></div><button type="submit" className="send-button" aria-label="发送需求" disabled={readonly||!message.trim()||busy||sending||loading}>{busy?<span className="trace-spinner"/>:<Icon name="send"/>}</button></div>
     </form><p className="composer-hint">{readonly?'你拥有查看权限，可浏览和下载项目图片':'Enter 发送 · Shift + Enter 换行'}</p>
    </div>
   </div>
  </main>
  {membersOpen&&project&&<ProjectMembers project={project} onClose={()=>setMembersOpen(false)} onChanged={()=>{void refresh(project.id);void loadProjects();}}/>}
  {creating&&<Dialog titleId="create-title" busy={saving} onClose={()=>setCreating(false)}><form className="project-modal" onSubmit={e=>{e.preventDefault();void create();}}><div className="modal-heading"><h2 id="create-title">新建项目</h2><button type="button" className="icon-button" aria-label="关闭新建项目" disabled={saving} onClick={()=>setCreating(false)}><Icon name="close"/></button></div><p>给这次创作起个名字，细节可以在对话里告诉 Agent。</p><label>项目名称<input required maxLength={120} placeholder="例如：国潮饭盒" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>{createError&&<p className="dialog-error" role="alert">{createError}</p>}<details className="optional-brief"><summary>补充产品信息（选填）</summary><label>品牌<input value={form.brand} onChange={e=>setForm({...form,brand:e.target.value})}/></label><label>产品<input value={form.product} onChange={e=>setForm({...form,product:e.target.value})}/></label><label>画面文字，每行一项<textarea value={form.copy} onChange={e=>setForm({...form,copy:e.target.value})}/></label></details><div className="modal-footer"><button type="button" className="secondary-button" disabled={saving} onClick={()=>setCreating(false)}>取消</button><button className="primary-button" disabled={saving||!form.name.trim()}>{saving?'创建中…':'创建项目'}</button></div></form></Dialog>}
  {deleteTarget&&<DeleteProjectDialog project={deleteTarget} busy={deleting} error={deleteError} blocked={deleteTarget.id===project?.id&&!!active&&['queued','running','waiting_user'].includes(active.status)} onClose={()=>{if(!deleting)setDeleteTarget(null);}} onDelete={()=>void deleteProject()}/>}
  {toast&&<div className="toast" role="status"><span className="online-dot"/><span>{toast}</span><button className="icon-button" aria-label="关闭通知" onClick={()=>setToast('')}><Icon name="close"/></button></div>}
  {preview&&<div className="image-lightbox" role="dialog" aria-modal="true" aria-label="图片预览" onMouseDown={e=>{if(e.target===e.currentTarget)setPreview(null);}}><button className="lightbox-close" aria-label="关闭图片预览" onClick={()=>setPreview(null)}><Icon name="close"/></button><img src={preview.url} alt={preview.title}/><div className="lightbox-actions"><button disabled={readonly} onClick={()=>useImage(preview)}>基于此图修改</button><a href={preview.url+'?download=true'} download>下载图片</a></div></div>}
 </div>;
}
