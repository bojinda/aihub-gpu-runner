import {installBrowser} from './recovery_browser_fixture.mjs';
installBrowser();
import { test, beforeEach } from 'node:test';
beforeEach(() => installBrowser());
import assert from 'node:assert/strict';
import { RecoveryFlow } from '../home_assistant/www/aihub-recovery-card.js';

const check = {eligible:true, token:'ticket-A', operation:'operation-A', action:'recover', expires:1060,
  effect:'Retire the never-started job.', job_id:'job-A', identity:{lease_id:'lease-A',boot_id:'boot-A'},incident:'incident-A'};

test('check never sends recovery and a cancelled dialog has no effect', async () => {
  const calls=[]; const flow=new RecoveryFlow(async v=>(calls.push(v),check),async()=>false,()=>1000);
  await flow.check(); await flow.recover(); assert.deepEqual(calls,[{action:'check'}]);
});
test('confirmation captures exact identity and ticket despite later replacement',async()=>{
  const calls=[];let finish;let description;let entered;const dialog=new Promise(r=>entered=r);
  const flow=new RecoveryFlow(async v=>(calls.push(v),v.action==='check'?check:{result:'succeeded'}),
    d=>(description=d,entered(),new Promise(r=>finish=r)),()=>1000);
  await flow.check();const pending=flow.recover();
  flow.checked={...check,token:'ticket-B',job_id:'job-B'};await dialog;finish(true);await pending;
  assert.equal(calls[1].token,'ticket-A');assert.equal(calls[1].confirmed,true);
  assert.ok(description.includes('job-A')&&description.includes('lease-A')&&description.includes('incident-A'));
});
test('expired approval never posts',async()=>{
  const calls=[];const flow=new RecoveryFlow(async v=>(calls.push(v),check),async()=>true,()=>1060);
  await flow.check();await assert.rejects(flow.recover(),/expired/);assert.equal(calls.length,1);
});
test('approval expiring inside dialog requires a new check',async()=>{
  const calls=[];let now=1000;const flow=new RecoveryFlow(async v=>(calls.push(v),check),async()=>{now=1060;return true},()=>now);
  await flow.check();await assert.rejects(flow.recover(),/expired/);assert.equal(calls.length,1);
});
test('double click cannot dispatch twice',async()=>{
  let finish,entered;const dialog=new Promise(r=>entered=r);const calls=[];const flow=new RecoveryFlow(async v=>(calls.push(v),check),()=>(entered(),new Promise(r=>finish=r)),()=>1000);
  await flow.check();const pending=flow.recover();await assert.rejects(flow.recover(),/already/);
  await dialog;finish(true);await pending;assert.equal(calls.filter(v=>v.action==='recover').length,1);
});
test('lost reply consumes browser ticket and remembers operation for verification',async()=>{
  const calls=[];const flow=new RecoveryFlow(async v=>{calls.push(v);if(v.action==='recover')throw new Error('unavailable');return check},async()=>true,()=>1000);
  await flow.check();await assert.rejects(flow.recover(),/unavailable/);
  assert.equal(flow.checked,null);assert.equal(flow.operation,'operation-A');assert.equal(calls.length,2);
});
test('blocked backend removes previous eligibility',async()=>{
  const flow=new RecoveryFlow(async()=>({eligible:false,failed_checks:['backend_unavailable']}),async()=>true,()=>1000);
  flow.checked=check;await flow.check();assert.equal(flow.checked,null);
});
test('abort uses its separate frozen approval',async()=>{
  const calls=[];const flow=new RecoveryFlow(async v=>(calls.push(v),{...check,action:'abort'}),async()=>true,()=>1000);
  await flow.check('check-abort');await flow.recover();assert.equal(calls[1].action,'abort');
});
