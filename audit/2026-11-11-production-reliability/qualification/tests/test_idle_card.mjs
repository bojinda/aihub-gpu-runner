import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AIHubRecoveryCard} from '../home_assistant/aihub-recovery-card.js';
const checked={eligible:true,action:'recover',classification:'manual_idle_only',case:'ollama_completed_idle_manual_v1',
 token:'idle1.exact',operation:'exact-attempt',expires:9999999999,job_id:'exact-job',incident:'exact-incident',
 identity:{lease_id:'exact-lease',request_hash:'exact-hash',boot_id:'exact-boot',policy_id:'exact-policy',resources:['gpu1']},
 effect:'Fail this job and release only GPU1. No cleanup, restart or replay.'};
const initial={available:true,integrated:true,capabilities:['manual_idle_recovery_v1'],readiness:null,admission_ready:true,
 capacity_reason:'normal_readiness_checks_apply',owners:{gpu1:{job_id:'exact-job'}},affected_jobs:['exact-job'],recovery_reasons:['process_refusal'],
 operator_required:true,maintenance:'open',observed_at:1000,incidents:[{job_id:'exact-job',resources:['gpu1'],phase:'execution_complete',
 classification:'manual_idle_check_required',failed_checks:[],error_code:'process_refusal'}]};
function dom(){
 const nodes=new Map();
 for(const key of ['dl','.message',...['check','recover','emergency','verify','check-abort'].map(x=>`[data-action="${x}"]`)])
  nodes.set(key,{textContent:'',children:[],replaceChildren(){this.children=[]},append(...x){this.children.push(...x)}});
 const shadow={querySelector:k=>nodes.get(k),querySelectorAll:()=>[]};
 globalThis.document={createElement:()=>({textContent:''})};
 return {nodes,shadow};
}
async function setup(send){
 const d=dom();const card=new AIHubRecoveryCard();card.attachShadow=()=>card.shadowRoot=d.shadow;
 card.hass={callWS:send};await new Promise(r=>setImmediate(r));return {card,...d};
}

test('missing mandatory gate displays exact configuration error without an operational recovery button',async()=>{
 const value={...initial,owners:{},affected_jobs:[],incidents:[],capabilities:[],recovery_operational:false,
   configuration_errors:['mandatory_admission_gate_not_configured']};
 const {card,nodes}=await setup(async()=>value);
 assert.ok(nodes.get('dl').children.some(x=>x.textContent==='mandatory_admission_gate_not_configured'));
 assert.equal(nodes.get('[data-action="recover"]').disabled,true);
 assert.equal(card.flow.checked,null);
});
test('actual card manual button confirms exact binding and displays verified completion',async()=>{
 let status=structuredClone(initial),dialog;const calls=[];
 globalThis.window={confirm:text=>(dialog=text,true)};
 const {card,nodes}=await setup(async m=>{
  calls.push(m);
  if(m.action==='status')return status;
  if(m.action==='check')return checked;
  assert.equal(m.action,'recover');assert.equal(m.token,'idle1.exact');assert.equal(m.confirmed,true);
  status={...status,owners:{},affected_jobs:[],incidents:[],operator_required:false,attempt:{id:'exact-attempt',phase:'succeeded'}};
  return {result:'released_readiness_pending',verified_release:true,operation:'exact-attempt'};
 });
 assert.equal(nodes.get('[data-action="recover"]').textContent,'Recover Now…');
 assert.equal(nodes.get('[data-action="emergency"]').hidden,true);
 await card.act('recover');
 for(const part of ['exact-job','exact-lease','exact-hash','exact-boot','exact-policy','gpu1','manual_idle_only'])assert.ok(dialog.includes(part));
 assert.equal(calls.filter(x=>x.action==='recover').length,1);
 assert.match(nodes.get('.message').textContent,/verified_release/);
 assert.ok(nodes.get('dl').children.some(x=>x.textContent.includes('succeeded')));
});
test('actual card displays exact blocker and never opens confirmation',async()=>{
 globalThis.window={confirm:()=>{throw Error('must not confirm')}};const calls=[];
 const {card,nodes}=await setup(async m=>(calls.push(m),m.action==='status'?initial:{eligible:false,failed_checks:['prior_or_rejected_process_still_present']}));
 await card.act('recover');assert.match(nodes.get('.message').textContent,/prior or rejected process still present/);
 assert.equal(calls.filter(x=>x.action==='recover').length,0);
});
test('actual card remembers uncertain operation and sends verify without a second release',async()=>{
 globalThis.window={confirm:()=>true};let status=structuredClone(initial);const calls=[];
 const {card}=await setup(async m=>{
  calls.push(m);if(m.action==='status')return status;if(m.action==='check')return checked;
  if(m.action==='recover'){status={...status,maintenance:'closed',attempt:{id:'exact-attempt',phase:'operator_required'}};throw Error('lost response; verify required')}
  assert.equal(m.action,'verify');assert.equal(m.operation,'exact-attempt');
  return {result:'released_readiness_pending',verified_release:true};
 });
 await card.act('recover');await card.act('verify');assert.equal(calls.filter(x=>x.action==='recover').length,1);
});
