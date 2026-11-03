import {useEffect,useRef} from 'react';
import type {ReactNode} from 'react';

export function Dialog({titleId,descriptionId,children,onClose,busy=false,destructive=false}:{titleId:string;descriptionId?:string;children:ReactNode;onClose:()=>void;busy?:boolean;destructive?:boolean}){
 const ref=useRef<HTMLDivElement>(null);
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement|null;
  const target=ref.current?.querySelector<HTMLElement>('[data-dialog-autofocus]')||ref.current?.querySelector<HTMLElement>('input:not([disabled])')||ref.current?.querySelector<HTMLElement>('button:not([disabled])');
  target?.focus();
  return()=>{if(previous?.isConnected)previous.focus();};
 },[]);
 return <div className="modal-backdrop" onMouseDown={event=>{if(!busy&&event.target===event.currentTarget)onClose();}}>
  <div className="dialog-content" ref={ref} role={destructive?'alertdialog':'dialog'} aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1} onKeyDown={event=>{
   if(event.key==='Escape'){event.stopPropagation();if(!busy)onClose();}
   if(event.key==='Tab'){
    const targets=[...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href]')].filter(target=>target.getClientRects().length>0);
    const first=targets[0],last=targets.at(-1);
    if(!first){event.preventDefault();event.currentTarget.focus();}
    else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
   }
  }}>{children}</div>
 </div>;
}
