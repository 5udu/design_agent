import {test} from 'node:test';
import assert from 'node:assert/strict';
import {projectTrace,mergeEvents,reconcileConversation} from '../src/features/workspace/traceProjection.ts';
import type {AgentEvent,Run} from '../src/types.ts';

function event(seq:number,kind:string,data:Record<string,unknown>,run='run'):AgentEvent{
 return {id:seq,seq,session_id:'session',run_id:run,source:'runtime',kind,data,created_at:seq};
}

test('history replay and repeated live events produce the same stable tool card',()=>{
 const start=event(1,'tool_started',{call_id:'call',name:'generate_image',arguments:{prompt:'饭盒'}});
 const finish=event(2,'tool_finished',{call_id:'call',name:'generate_image',result:{status:'succeeded',asset_ids:['image']}});
 const snapshot=projectTrace([start,finish]);
 const incremental=projectTrace(mergeEvents(mergeEvents([], [start]),[finish,start,finish]));
 assert.deepEqual(incremental,snapshot);
 assert.equal(snapshot.length,1);
 assert.equal(snapshot[0].kind,'tool');
 assert.equal(snapshot[0].id,'call:run:call');
});

test('a question changes state on the same node after resume',()=>{
 const start=event(1,'tool_started',{call_id:'ask',name:'ask_user'});
 const waiting=event(2,'tool_waiting',{call_id:'ask',name:'ask_user'});
 const finish=event(3,'tool_finished',{call_id:'ask',name:'ask_user',result:{status:'succeeded',answer:'深蓝色'}});
 const before=projectTrace([start,waiting])[0];
 const after=projectTrace([start,waiting,finish])[0];
 assert.equal(before.id,after.id);
 assert.equal(before.kind==='tool'&&before.status,'waiting_user');
 assert.equal(after.kind==='tool'&&after.status,'succeeded');
});

test('model final messages and request envelopes do not become duplicate trace notes',()=>{
 const nodes=projectTrace([
  event(1,'assistant_message',{text:'过程说明',phase:'progress'}),
  event(2,'assistant_message',{text:'最终交付',phase:'final'}),
  event(3,'request_prepared',{tools:[{name:'generate_image'}]}),
 ]);
 assert.deepEqual(nodes,[{id:'message:1',kind:'note',text:'过程说明'}]);
});

test('compaction adds one node without removing earlier trace cards',()=>{
 const nodes=projectTrace([
  event(1,'tool_started',{call_id:'read',name:'read_project'}),
  event(2,'tool_finished',{call_id:'read',name:'read_project',result:{status:'succeeded'}}),
  event(3,'context_compaction_started',{operation_id:'compact'}),
  event(4,'context_compacted',{operation_id:'compact',estimated_tokens_before:1000,estimated_tokens_after:100,covered_seqs:[1,2]}),
  event(5,'context_compaction_finished',{operation_id:'compact',status:'succeeded'}),
 ]);
 assert.equal(nodes.length,2);
 assert.equal(nodes[0].kind,'tool');
 assert.equal(nodes[1].kind==='compact'&&nodes[1].status,'succeeded');
});

test('call ids are scoped to runs and result-only pages can render',()=>{
 const nodes=projectTrace([
  event(1,'tool_finished',{call_id:'same',name:'read_project',result:{status:'failed'}},'first'),
  event(2,'tool_finished',{call_id:'same',name:'read_project',result:{status:'succeeded'}},'second'),
 ]);
 assert.equal(nodes.length,2);
 assert.notEqual(nodes[0].id,nodes[1].id);
});

test('late snapshots and resumed responses preserve the newest run state',()=>{
 const waiting:Run={id:'run',session_id:'session',project_id:'project',message:'饭盒',status:'waiting_user',updated_at:2};
 const original=reconcileConversation(undefined,waiting,[event(3,'run_status',{status:'waiting_user'})]);
 const resumed=reconcileConversation(original,{...waiting,status:'queued',updated_at:4});
 assert.equal(resumed.run.status,'queued');
 const final=reconcileConversation(resumed,{...waiting,status:'succeeded',updated_at:6,summary:'交付图片'},[event(7,'run_status',{status:'succeeded'})]);
 const stale=reconcileConversation(final,{...waiting,status:'running',updated_at:5},[event(5,'run_status',{status:'running'})]);
 assert.equal(stale.run.status,'succeeded');
 assert.equal(stale.run.summary,'交付图片');
 const raced=reconcileConversation(undefined,{...waiting,status:'running',updated_at:5},[event(7,'run_status',{status:'succeeded'})]);
 assert.equal(raced.run.status,'succeeded');
});
