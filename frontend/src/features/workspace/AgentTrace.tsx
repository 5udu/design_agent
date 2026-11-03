import {useEffect,useState} from 'react';
import type {Conversation} from '../../types';
import {projectTrace} from './traceProjection';

const names:Record<string,string>={read_project:'读取项目与参考图',generate_image:'生成图片',edit_image:'编辑参考图片',inspect_image:'查看图片',ask_user:'等待你的回答'};

export function AgentTrace({conversation}:{conversation:Conversation}){
 const {run,events}=conversation;
 const running=['queued','running'].includes(run.status);
 const [expanded,setExpanded]=useState(running);
 useEffect(()=>setExpanded(running),[running]);
 const entries=projectTrace(events);
 const count=entries.filter(e=>e.kind==='tool').length;
 const status=running?'正在处理':run.status==='waiting_user'?'等待你的回答':run.status==='succeeded'?'已完成':run.status==='partial'?'保留部分结果':run.status==='cancelled'?'已结束':'执行遇到问题';
 return <details className="agent-trace" open={expanded} onToggle={e=>setExpanded(e.currentTarget.open)} data-run-id={run.id}>
  <summary><span className={running?'trace-spinner':'trace-status'}>{running?'':run.status==='succeeded'?'✓':'·'}</span><span>{status}{count>0&&<span className="trace-count"> · {count} 次工具调用</span>}{run.reasoning_effort&&<span className="trace-effort" title="本轮思考强度"> · {run.reasoning_effort}</span>}</span><span className="trace-chevron">⌄</span></summary>
  {expanded&&<div className="trace-content">{entries.length===0?<p className="trace-note">正在理解你的需求…</p>:entries.map(e=>{
   if(e.kind==='note')return <p className="trace-note" key={e.id}>{e.text}</p>;
   if(e.kind==='observation'||e.kind==='error')return <div className={'trace-event '+(e.kind==='error'?'trace-error':'')} key={e.id}><i/>{e.text}</div>;
   if(e.kind==='compact')return <div className="trace-event" key={e.id}><i/>{e.status==='running'?'正在整理较早的对话':e.status==='failed'?'对话整理未完成，原历史已保留':'已整理较早的对话，完整历史仍可查看'}</div>;
   if(e.kind!=='tool')return null;
   const labels:Record<string,string>={running:'处理中',waiting_user:'等待回答',succeeded:'已完成',failed:'未完成'};
   return <details className={'trace-result '+(e.status==='failed'?'trace-error':'')} key={e.id}><summary>{names[e.name]||e.name} · {labels[e.status]}</summary><pre>{JSON.stringify({input:e.arguments,result:e.result},null,2).slice(0,8000)}</pre></details>;
  })}</div>}
 </details>;
}
