import type {Project} from '../../types';
import {Dialog} from './Dialog';
import {Icon} from './Icon';

export function DeleteProjectDialog({project,busy,error,blocked,onClose,onDelete}:{project:Project;busy:boolean;error:string;blocked:boolean;onClose:()=>void;onDelete:()=>void}){
 return <Dialog titleId="delete-title" descriptionId="delete-description" destructive busy={busy} onClose={onClose}>
  <div className="project-modal delete-modal">
   <div className="modal-heading"><h2 id="delete-title">删除项目？</h2><button type="button" className="icon-button" aria-label="关闭删除确认" disabled={busy} onClick={onClose}><Icon name="close"/></button></div>
   <div className="delete-project-name"><Icon name="image"/><strong>{project.name}</strong></div>
   <p id="delete-description">这个项目的对话、执行记录和生成图片将一并删除，无法恢复。</p>
   {blocked&&<p className="delete-blocked">项目有正在执行或等待回答的任务，请先完成或结束任务。</p>}
   {error&&<p className="dialog-error" role="alert">{error}</p>}
   <div className="modal-footer"><button type="button" className="secondary-button" data-dialog-autofocus disabled={busy} onClick={onClose}>取消</button><button type="button" className="danger-button" disabled={busy||blocked} onClick={onDelete}>{busy?<><span className="trace-spinner"/>正在删除…</>:'删除项目'}</button></div>
  </div>
 </Dialog>;
}
