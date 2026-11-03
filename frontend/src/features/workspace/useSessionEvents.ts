import {useEffect,useRef} from 'react';
import {api} from '../../api/client';
import type {AgentEvent,Conversation} from '../../types';

type Binding={id:string;projectId:string;cursor:number};
export function useSessionEvents(binding:Binding|null,handlers:{event:(event:AgentEvent)=>void;run:(detail:Conversation)=>void;error:(message:string)=>void;deleted:(projectId:string)=>void}){
 const callbacks=useRef(handlers);callbacks.current=handlers;
 useEffect(()=>{
  if(!binding)return;
  let disposed=false;
  const tickets=new Map<string,number>();
  const stream=new EventSource('/api/sessions/'+binding.id+'/events?after='+binding.cursor);
  stream.addEventListener('project_deleted',()=>{
   if(disposed)return;
   disposed=true;stream.close();callbacks.current.deleted(binding.projectId);
  });
  stream.onmessage=message=>{
   if(disposed)return;
   const event:AgentEvent=JSON.parse(message.data);
   callbacks.current.event(event);
   if(event.kind==='user_message'||event.kind==='run_status'){
    const ticket=(tickets.get(event.run_id)||0)+1;tickets.set(event.run_id,ticket);
    api<Conversation>('/runs/'+event.run_id).then(detail=>{
     if(!disposed&&tickets.get(event.run_id)===ticket)callbacks.current.run(detail);
    }).catch(error=>{if(!disposed)callbacks.current.error(error.message);});
   }
  };
  stream.onerror=()=>{if(!disposed&&stream.readyState===EventSource.CLOSED)callbacks.current.error('对话更新已断开，请重新打开项目');};
  return()=>{disposed=true;stream.close();};
 },[binding]);
}
