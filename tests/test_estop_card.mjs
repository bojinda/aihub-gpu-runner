import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EstopFlow} from '../home_assistant/www/aihub-recovery-card.js';
const done=()=>({timestamp:123,outcome:'success',released_leases:1,error:null});
test('one click is exactly one POST with no precheck, ticket, confirmation or browser store',async()=>{
 const calls=[];const f=new EstopFlow(async m=>(calls.push(m),done()));
 assert.equal((await f.execute()).outcome,'success');assert.deepEqual(calls,[{action:'estop'}]);
 assert.equal(f.pending,undefined);assert.equal(f.checked,undefined);
});
test('lost response is UNKNOWN and never automatically retried',async()=>{
 let calls=0;const f=new EstopFlow(async()=>{calls++;throw Error('lost');});
 assert.equal((await f.execute()).outcome,'unknown');await new Promise(r=>setTimeout(r,20));assert.equal(calls,1);
 const reloaded=new EstopFlow(async()=>{calls++;return done();});assert.equal(reloaded.result,null);assert.equal(calls,1);
});
test('definite failure retains the actionable code',async()=>{
 const f=new EstopFlow(async()=>({timestamp:1,outcome:'failure',released_leases:0,error:'estop_jobs_busy'}));
 assert.equal((await f.execute()).error,'estop_jobs_busy');
});
test('an in-flight button cannot send a second request',async()=>{
 let release,calls=0;const f=new EstopFlow(()=>{calls++;return new Promise(r=>release=r);});
 const one=f.execute();assert.equal(await f.execute(),null);release(done());await one;assert.equal(calls,1);
});
test('malformed or old successful status cannot be accepted as success',async()=>{
 for(const value of [{result:'ownership_relinquished',verified:true},null,{...done(),released_leases:-1}]){
  const f=new EstopFlow(async()=>value);assert.equal((await f.execute()).outcome,'unknown');
 }
});
test('two separate clicks each execute and need no mandatory status or Verify',async()=>{
 let calls=0;const f=new EstopFlow(async()=>{calls++;return done();});await f.execute();await f.execute();assert.equal(calls,2);
});
