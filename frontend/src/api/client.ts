let csrfToken='';
export function setCsrfToken(value:string){csrfToken=value;}
export async function api<T>(path:string, data?:unknown, method?:'POST'|'DELETE'|'PUT'|'PATCH'):Promise<T> {
  const response=await fetch('/api'+path,{method:method||(data===undefined?'GET':'POST'),credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},...(data===undefined?{}:{body:JSON.stringify(data)})});
  if(!response.ok) {if(response.status===401&&path!=='/auth/login')window.dispatchEvent(new Event('session-expired'));const body=await response.json().catch(()=>({detail:'连接失败，请稍后重试'}));throw new Error(typeof body.detail==='string'?body.detail:'请检查输入内容');}
  return response.json();
}
