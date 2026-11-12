import {installBrowser} from './recovery_browser_fixture.mjs';
import {test,beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {EstopFlow} from '../home_assistant/www/aihub-recovery-card.js';
beforeEach(()=>installBrowser());
const checked=()=>({eligible:true,action:'estop',operation:'a'.repeat(32),generation:'b'.repeat(32),token:'fixture-ticket',expires:Date.now()/1000+60,effect:'GPU work is NOT stopped and may overlap.',owners:{},jobs:{}});
const done=()=>({operation:'a'.repeat(32),old_generation:{id:'b'.repeat(32)},new_generation:'c'.repeat(32),result:'ownership_relinquished',verified:true,replayed:false,physical_gpu_state:'NOT_ASSERTED'});
test('lost response survives reload and historical success, Verify targets exact E-stop',async()=>{
 const calls=[];const send=async m=>{calls.push(m);if(m.action==='estop-check')return checked();if(m.action==='estop')throw Error('lost');return done();};
 const flow=new EstopFlow(send,async text=>{assert.match(text,/NOT stopped/);return true;});
 await flow.check();await assert.rejects(flow.execute(),/lost/);
 const restored=new EstopFlow(send,async()=>true);await restored.sync();assert.equal(restored.pending.operation,'a'.repeat(32));
 await restored.accept({...done(),operation:'d'.repeat(32)});assert.ok(restored.pending);
 await assert.rejects(restored.check(),/pending/);await restored.verify();assert.equal(restored.pending,null);
 assert.equal(calls.filter(m=>m.action==='estop').length,1);assert.equal(calls.at(-1).operation,'a'.repeat(32));
});
test('concurrent tabs reserve one administrator confirmation',async()=>{
 let release,entered;const ready=new Promise(r=>entered=r);const calls=[];
 const send=async m=>(calls.push(m),m.action==='estop-check'?checked():done());
 const a=new EstopFlow(send,()=>{entered();return new Promise(r=>release=r);});const b=new EstopFlow(send,async()=>true);
 await a.check();await b.check();const first=a.execute();await ready;
 await assert.rejects(b.execute(),/active/);release(true);await first;
 assert.equal(calls.filter(m=>m.action==='estop').length,1);
});
test('cancellation and expiry send no mutation',async()=>{
 const calls=[];const flow=new EstopFlow(async m=>(calls.push(m),checked()),async()=>false);
 await flow.check();assert.equal((await flow.execute()).result,'cancelled');flow.checked.expires=0;
 await assert.rejects(flow.execute(),/Check/);assert.equal(calls.filter(m=>m.action==='estop').length,0);
});
test('wrong generation cannot clear pending, definite no-effect can',async()=>{
 const flow=new EstopFlow(async m=>m.action==='estop-check'?checked():{result:'uncertain'},async()=>true);
 await flow.check();await flow.execute();await flow.accept({...done(),old_generation:{id:'d'.repeat(32)}});assert.ok(flow.pending);
 await flow.accept({operation:'a'.repeat(32),result:'not_committed',verified:true,verified_no_effect:true});assert.equal(flow.pending,null);
});
