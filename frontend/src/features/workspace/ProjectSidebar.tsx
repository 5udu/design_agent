import {useState} from 'react';
import type {Project} from '../../types';
import {Icon} from './Icon';

export function ProjectSidebar({projects,currentId,open,onClose,onCreate,onOpen,onDelete}:{projects:Project[];currentId?:string;open:boolean;onClose:()=>void;onCreate:()=>void;onOpen:(id:string)=>void;onDelete:(project:Project)=>void}){
 const [search,setSearch]=useState('');
 const matches=[...projects].reverse().filter(project=>project.name.toLowerCase().includes(search.trim().toLowerCase()));
 return <>
  {open&&<button className="sidebar-overlay" aria-label="关闭项目列表" onClick={onClose}/>}
  <aside className={'project-sidebar '+(open?'is-open':'')} aria-label="项目管理">
   <div className="app-brand"><span className="brand-symbol"><Icon name="image"/></span><span>图像 Agent</span><small>STUDIO</small></div>
   <button className="new-project" onClick={()=>{setSearch('');onCreate();}}><Icon name="plus"/>新建项目</button>
   <div className="project-list-heading">项目<span>{projects.length}</span></div>
   <div className="project-search-wrap"><input className="project-search" aria-label="搜索项目" placeholder="搜索项目" value={search} onChange={event=>setSearch(event.target.value)}/>{search&&<button className="icon-button search-clear" aria-label="清空搜索" onClick={()=>setSearch('')}><Icon name="close"/></button>}</div>
   <nav className="project-list" aria-label="项目列表">{matches.map(project=><div key={project.id} className={'project-row '+(project.id===currentId?'is-active':'')}>
    <button className={'project-item '+(project.id===currentId?'is-active':'')} aria-current={project.id===currentId?'page':undefined} onClick={()=>onOpen(project.id)} title={project.name}><Icon name="image"/><span>{project.name}</span></button>
    <button className="project-remove" aria-label={'删除项目：'+project.name} title="删除项目" onClick={()=>onDelete(project)}><Icon name="trash"/></button>
   </div>)}{!projects.length?<p className="sidebar-empty">你的图像项目会保存在这里</p>:!matches.length?<div className="search-empty"><p>没有找到相关项目</p><button onClick={()=>setSearch('')}>清空搜索</button></div>:null}</nav>
   <div className="sidebar-footer"><span className="online-dot"/>图像创作工作区</div>
  </aside>
 </>;
}
