import type {ReactNode} from 'react';

export function Icon({name}:{name:string}){
 const paths:Record<string,ReactNode>={
  plus:<path d="M12 5v14M5 12h14"/>,
  send:<path d="m6 12 6-6 6 6M12 6v13"/>,
  sidebar:<><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></>,
  image:<><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1"/><path d="m3 17 5-5 4 4 4-6 5 7"/></>,
  download:<><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></>,
  close:<path d="m6 6 12 12M18 6 6 18"/>,
  trash:<><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/></>,
  down:<path d="M12 4v16m-6-6 6 6 6-6"/>,
 };
 return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.image}</svg>;
}
