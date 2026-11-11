import {installBrowser} from './recovery_browser_fixture.mjs';
installBrowser();
import { test, beforeEach } from 'node:test';
beforeEach(() => installBrowser());
import assert from 'node:assert/strict';
import { RecoveryFlow } from '../home_assistant/www/aihub-recovery-card.js';

const checked={eligible:true,action:'recover',token:'exact-token',operation:'operation',expires:1060,
  job_id:'job',identity:{lease_id:'lease',boot_id:'boot'},incident:'incident',effect:'Keep saved output.'};

test('Recover Now checks afresh and asks for one confirmation',async()=>{
  const calls=[];let confirmations=0;
  const flow=new RecoveryFlow(async m=>(calls.push(m),m.action==='check'?checked:{result:'recovered_ready'}),
    async()=>{confirmations++;return true},()=>1000);
  await flow.recoverNow();assert.equal(confirmations,1);
  assert.deepEqual(calls,[{action:'check'},{action:'recover',token:'exact-token',confirmed:true}]);
});

test('Emergency confirmation includes work loss and exact incident',async()=>{
  const calls=[];let dialog;
  const flow=new RecoveryFlow(async m=>(calls.push(m),{...checked,action:'emergency',loss_required:true,
    effect:'Stop this exact execution. Unfinished output may be lost. No replay.'}),async d=>(dialog=d,true),()=>1000);
  await flow.recoverNow(true);
  assert.equal(calls[0].action,'check-emergency');assert.equal(calls[1].action,'emergency');
  assert.equal(calls[1].accept_work_loss,true);assert.match(dialog,/output may be lost/);assert.match(dialog,/incident/);
});

test('cancelling emergency sends no mutation',async()=>{
  const calls=[];const flow=new RecoveryFlow(async m=>(calls.push(m),{...checked,action:'emergency',loss_required:true}),async()=>false,()=>1000);
  await flow.recoverNow(true);assert.deepEqual(calls,[{action:'check-emergency'}]);
});

test('dangerous concurrency remains blocked without a confirmation',async()=>{
  let dialogs=0;const flow=new RecoveryFlow(async()=>({eligible:false,failed_checks:['unrelated_target_work']}),async()=>{dialogs++;return true},()=>1000);
  const result=await flow.recoverNow(true);assert.equal(result.eligible,false);assert.equal(dialogs,0);
});

test('emergency cannot reuse a previous normal recovery ticket',async()=>{
  const calls=[];const flow=new RecoveryFlow(async m=>(calls.push(m),{...checked,action:'emergency',token:'emergency-token',loss_required:true}),async()=>true,()=>1000);
  flow.checked=checked;await flow.recoverNow(true);
  assert.equal(calls[1].token,'emergency-token');assert.equal(calls[1].action,'emergency');
});
