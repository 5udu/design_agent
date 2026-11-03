import {useEffect,useState} from 'react';
import {api} from '../../api/client';
import {REASONING_EFFORTS} from '../../types';
import type {ReasoningEffort} from '../../types';

const preferenceKey='image-agent-reasoning-effort';
function savedEffort(){
 const value=localStorage.getItem(preferenceKey);
 return REASONING_EFFORTS.find(effort=>effort===value);
}

export function useReasoningEffort(){
 const [effort,setEffort]=useState<ReasoningEffort>(()=>savedEffort()||'max');
 useEffect(()=>{
  let disposed=false;
  api<{agent_settings:{reasoning_effort:ReasoningEffort}}>('/health').then(health=>{
   if(!disposed&&!savedEffort())setEffort(health.agent_settings.reasoning_effort);
  }).catch(()=>{});
  return()=>{disposed=true;};
 },[]);
 function selectEffort(value:ReasoningEffort){setEffort(value);localStorage.setItem(preferenceKey,value);}
 return [effort,selectEffort] as const;
}
