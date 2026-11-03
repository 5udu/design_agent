export async function api<T>(path:string, data?:unknown, method?:'POST'|'DELETE'):Promise<T> {
  const response=await fetch('/api'+path, data===undefined?method?{method}:undefined:{method:method||'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(!response.ok) {const body=await response.json().catch(()=>({detail:'连接失败，请稍后重试'}));throw new Error(typeof body.detail==='string'?body.detail:'请检查输入内容');}
  return response.json();
}
