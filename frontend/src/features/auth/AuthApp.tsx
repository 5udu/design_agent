import {useEffect,useState} from 'react';
import {api,setCsrfToken} from '../../api/client';
import {Workspace} from '../workspace/Workspace';
import {Dialog} from '../workspace/Dialog';
import type {User} from '../../types';

type Identity={user:User;csrf_token:string};

export function AuthApp(){
 const [user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[newPassword,setNewPassword]=useState(''),[busy,setBusy]=useState(false),[changing,setChanging]=useState(false),[admin,setAdmin]=useState(false);
 function accept(identity:Identity){setCsrfToken(identity.csrf_token);setUser(identity.user);setError('');setPassword('');setNewPassword('');}
 function clear(){setCsrfToken('');setUser(null);setAdmin(false);setChanging(false);setPassword('');setNewPassword('');}
 useEffect(()=>{let alive=true;api<Identity>('/auth/me').then(v=>{if(alive)accept(v);}).catch(()=>{}).finally(()=>{if(alive)setLoading(false);});const expired=()=>clear();window.addEventListener('session-expired',expired);return()=>{alive=false;window.removeEventListener('session-expired',expired);};},[]);
 async function submit(){setBusy(true);setError('');try{
  if(user){await api('/auth/password',{current_password:password,new_password:newPassword});clear();setError('密码已更新，请重新登录');}
  else accept(await api<Identity>('/auth/login',{username,password}));
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function logout(){try{await api('/auth/logout',{});clear();}catch(e){setError((e as Error).message);}}
 const form=<form className="account-form" onSubmit={e=>{e.preventDefault();void submit();}}><h1>{user?'修改密码':'登录设计工作区'}</h1><p>{user?'更新密码后，所有设备需要重新登录。':'使用管理员为你创建的员工账号登录。'}</p>{!user&&<label>员工账号<input autoComplete="username" required value={username} onChange={e=>setUsername(e.target.value)}/></label>}<label>{user?'当前密码':'密码'}<input autoComplete="current-password" type="password" maxLength={256} required value={password} onChange={e=>setPassword(e.target.value)}/></label>{user&&<label>新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={newPassword} onChange={e=>setNewPassword(e.target.value)}/><small>至少 12 个字符</small></label>}{error&&<p role="alert" className="dialog-error">{error}</p>}<button className="primary-button" disabled={busy}>{busy?'处理中…':user?'保存新密码':'登录'}</button></form>;
 if(loading)return <div className="login-screen">正在连接工作区…</div>;
 if(!user||user.must_change_password)return <div className="login-screen">{form}</div>;
 return <><Workspace key={user.id} account={<div className="account-actions"><span>{user.display_name}</span>{!!user.is_admin&&<button onClick={()=>setAdmin(true)}>员工管理</button>}<button onClick={()=>{setError('');setChanging(true);}}>修改密码</button><button onClick={()=>void logout()}>退出</button></div>}/>{changing&&<Dialog titleId="password-title" onClose={()=>setChanging(false)} busy={busy}><div className="management-modal"><h2 id="password-title">账号设置</h2>{form}<button onClick={()=>setChanging(false)}>关闭</button></div></Dialog>}{admin&&<UserManager onClose={()=>setAdmin(false)}/>}</>;
}

function UserManager({onClose}:{onClose:()=>void}){
 const [users,setUsers]=useState<User[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [form,setForm]=useState({username:'',display_name:'',password:'',is_admin:false});
 const [resetId,setResetId]=useState(''),[resetPassword,setResetPassword]=useState('');
 async function load(){setUsers(await api<User[]>('/admin/users'));}
 useEffect(()=>{void load().catch(e=>setError(e.message));},[]);
 async function act(action:()=>Promise<unknown>){setBusy(true);setError('');try{await action();await load();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <Dialog titleId="users-title" onClose={onClose} busy={busy}><div className="management-modal"><div className="management-heading"><h2 id="users-title">员工管理</h2><button onClick={onClose}>关闭</button></div><p>新账号首次登录须修改初始密码。停用账号会立即撤销登录。</p>{error&&<p role="alert" className="dialog-error">{error}</p>}<div className="member-list">{users.map(u=><div className="member-row" key={u.id}><span>{u.display_name}<small>{u.username} · {u.is_admin?'管理员':u.disabled?'已停用':'员工'}</small></span><button disabled={busy} onClick={()=>{setResetId(u.id);setResetPassword('');}}>重置密码</button>{!u.is_admin&&<button disabled={busy} onClick={()=>void act(()=>api('/admin/users/'+u.id,{disabled:!u.disabled},'PATCH'))}>{u.disabled?'启用':'停用'}</button>}</div>)}</div>{resetId&&<form className="account-form compact" onSubmit={e=>{e.preventDefault();void act(async()=>{await api('/admin/users/'+resetId,{password:resetPassword},'PATCH');setResetId('');setResetPassword('');});}}><label>新临时密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={resetPassword} onChange={e=>setResetPassword(e.target.value)}/></label><button disabled={busy}>保存临时密码</button></form>}<form className="account-form compact" onSubmit={e=>{e.preventDefault();void act(async()=>{await api('/admin/users',form);setForm({username:'',display_name:'',password:'',is_admin:false});});}}><h3>创建员工账号</h3><label>账号<input required value={form.username} onChange={e=>setForm({...form,username:e.target.value})}/></label><label>姓名<input required value={form.display_name} onChange={e=>setForm({...form,display_name:e.target.value})}/></label><label>初始密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/></label><label className="checkbox-label"><input type="checkbox" checked={form.is_admin} onChange={e=>setForm({...form,is_admin:e.target.checked})}/>管理员</label><button className="primary-button" disabled={busy}>创建账号</button></form></div></Dialog>;
}
