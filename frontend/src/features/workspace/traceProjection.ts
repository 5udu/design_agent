import type {AgentEvent,Conversation,Run} from '../../types';

export type TraceNode =
 | {id:string;kind:'tool';name:string;status:string;arguments?:unknown;result?:Record<string,any>}
 | {id:string;kind:'note'|'error'|'observation';text:string}
 | {id:string;kind:'compact';status:string};

export function mergeEvents(previous:AgentEvent[], incoming:AgentEvent[]):AgentEvent[]{
 const events=new Map<string,AgentEvent>();
 for(const event of [...previous,...incoming])events.set(event.session_id+':'+event.seq,event);
 return [...events.values()].sort((a,b)=>a.seq-b.seq);
}

export function reconcileConversation(previous:Conversation|undefined, run:Run, incoming:AgentEvent[]=[]):Conversation{
 const events=mergeEvents(previous?.events||[],incoming);
 const revision=(value:Run)=>value.updated_at??value.created_at??0;
 const latest=previous&&revision(previous.run)>revision(run)?previous.run:run;
 const status=events.filter(event=>event.kind==='run_status').at(-1);
 return {run:{...latest,...(status&&status.created_at>=revision(latest)?status.data:{})},events};
}

export function projectTrace(events:AgentEvent[]):TraceNode[]{
 const nodes:TraceNode[]=[];
 const calls=new Map<string,Extract<TraceNode,{kind:'tool'}>>();
 const compactions=new Map<string,Extract<TraceNode,{kind:'compact'}>>();
 for(const event of mergeEvents([],events)){
  const data=event.data;
  if(['tool_started','tool_waiting','tool_submitted','tool_finished'].includes(event.kind)){
   const key=event.run_id+':'+(event.call_id||data.call_id);
   let node=calls.get(key);
   if(!node){node={id:'call:'+key,kind:'tool',name:data.name||'tool',status:'running'};calls.set(key,node);nodes.push(node);}
   if(data.arguments!==undefined)node.arguments=data.arguments;
   if(event.kind==='tool_waiting')node.status='waiting_user';
   if(event.kind==='tool_finished'){node.result=data.result;node.status=data.result?.status==='failed'?'failed':'succeeded';}
  }else if(event.kind==='assistant_message'&&data.text&&data.phase!=='final'){
   nodes.push({id:'message:'+event.seq,kind:'note',text:data.text});
  }else if(event.kind==='visual_observation'){
   nodes.push({id:'image:'+event.seq,kind:'observation',text:'已准备实际图片供 Agent 观察'});
  }else if(['error','observation_failed','context_warning'].includes(event.kind)){
   nodes.push({id:'error:'+event.seq,kind:'error',text:data.message||'图片未能加载'});
  }else if(['context_compaction_started','context_compacted','context_compaction_finished'].includes(event.kind)){
   const key=data.operation_id as string;
   let node=compactions.get(key);
   if(!node){node={id:'compact:'+key,kind:'compact',status:'running'};compactions.set(key,node);nodes.push(node);}
   if(event.kind==='context_compacted')node.status='succeeded';
   if(event.kind==='context_compaction_finished')node.status=data.status;
  }
 }
 return nodes;
}
