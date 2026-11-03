import {REASONING_EFFORTS} from '../../types';
import type {ReasoningEffort} from '../../types';

const labels:Record<ReasoningEffort,string>={off:'off · 关闭',low:'low · 低',high:'high · 高',max:'max · 最高'};

export function ReasoningSelector({value,onChange,disabled}:{value:ReasoningEffort;onChange:(value:ReasoningEffort)=>void;disabled:boolean}){
 return <label className="reasoning-selector"><span>思考强度</span><select aria-label="思考强度" value={value} disabled={disabled} onChange={event=>onChange(event.target.value as ReasoningEffort)}>
  {REASONING_EFFORTS.map(effort=><option key={effort} value={effort}>{labels[effort]}</option>)}
 </select></label>;
}
