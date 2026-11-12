// Desired outcomes against the served card. DOM/transport/GPU replies are synthetic.
import {test, beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {AIHubRecoveryCard, RecoveryFlow, BrowserPendingStore} from '../home_assistant/www/aihub-recovery-card.js';
import {installBrowser} from './recovery_browser_fixture.mjs';
let browser;
beforeEach(() => {browser = installBrowser(); globalThis.window = {confirm: () => true};});
const ticket = {eligible: true, action: 'recover', operation: 'pending-operation', token: 'synthetic.ticket',
  expires: 9999999999, job_id: 'pending-job', incident: 'synthetic-incident', classification: 'manual_idle_only',
  identity: {lease_id: 'pending-lease', request_hash: '1'.repeat(64), boot_id: 'synthetic-boot', resources: ['gpu1']},
  effect: 'Fail this synthetic completed job and release only its lease. No replay.'};
const initial = {available: true, integrated: true, capabilities: ['manual_idle_recovery_v1'],
  admission_ready: true, capacity_reason: 'normal_readiness_checks_apply', owners: {}, affected_jobs: [],
  recovery_reasons: [], operator_required: true, maintenance: 'open', observed_at: 1000, incidents: [],
  attempt: {id: 'older-operation', phase: 'succeeded'}};
function dom() {
  const nodes = new Map();
  for (const key of ['dl', '.message', '.estop-state', ...['check', 'recover', 'emergency', 'verify', 'check-abort','estop-check','estop','estop-verify'].map(x => `[data-action="${x}"]`)])
    nodes.set(key, {textContent: '', children: [], replaceChildren() {this.children = [];}, append(...x) {this.children.push(...x);}});
  globalThis.document = {createElement: () => ({textContent: ''})};
  return {nodes, shadow: {querySelector: key => nodes.get(key), querySelectorAll: () => []}};
}
async function setup(send) {
  const d = dom(), card = new AIHubRecoveryCard();
  card.attachShadow = () => card.shadowRoot = d.shadow;
  card.hass = {callWS: send};
  await card.initialRefresh;
  return {card, ...d};
}
function service(calls, response = () => {throw Error('synthetic lost response');}) {
  return async message => {
    calls.push(message);
    if (message.action === 'status') return structuredClone(initial);
    if (message.action === 'check') return structuredClone(ticket);
    if (message.action === 'recover') return response(message);
    if (message.action === 'verify') return {operation: message.operation, result: 'operator_required'};
    throw Error('unexpected action');
  };
}

test('lost response and stale success retain exact Verify binding and fence a second mutation', async () => {
  const calls = [], {card, nodes} = await setup(service(calls));
  await card.act('recover');
  assert.equal(card.flow.operation, ticket.operation);
  assert.equal(nodes.get('[data-action="recover"]').disabled, true);
  await card.act('recover');
  await card.act('verify');
  assert.equal(calls.filter(x => x.action === 'recover').length, 1);
  assert.equal(calls.find(x => x.action === 'verify').operation, ticket.operation);
});

test('status while POST is in flight cannot replace the pending operation', async () => {
  let finish, entered;
  const started = new Promise(resolve => entered = resolve), calls = [];
  const {card} = await setup(service(calls, () => {entered(); return new Promise(resolve => finish = resolve);}));
  const action = card.act('recover'); await started;
  await card.refresh(); assert.equal(card.flow.operation, ticket.operation);
  finish({operation: ticket.operation, result: 'operator_required'}); await action;
  assert.equal(card.flow.pending.operation, ticket.operation);
});

test('pending operation arriving during Check returns Verify-only instead of stale eligibility', async () => {
  let finish, entered;
  const checking = new Promise(resolve => entered = resolve), calls = [];
  const first = new RecoveryFlow(async message => {
    assert.equal(message.action, 'check'); entered(); return new Promise(resolve => finish = resolve);
  }, async () => true, () => 1000);
  const check = first.check(); await checking;
  const second = new RecoveryFlow(service(calls), async () => true, () => 1000);
  await second.check(); await assert.rejects(second.recover(), /lost response/);
  finish(structuredClone(ticket));
  assert.deepEqual(await check, {eligible: false, operation: ticket.operation,
    failed_checks: ['pending_recovery_verify_required']});
  assert.equal(first.checked, null);
  assert.equal(calls.filter(message => message.action === 'recover').length, 1);
});

test('reload and another tab inherit only the pending binding, not credentials or authorization', async () => {
  const calls = [], first = await setup(service(calls));
  await first.card.act('recover');
  const raw = JSON.stringify(await first.card.flow.pendingStore.load());
  assert.ok(!raw.includes(ticket.token)); assert.ok(!raw.includes('confirmed'));
  const second = await setup(service(calls));
  assert.equal(second.card.flow.operation, ticket.operation);
  await second.card.act('recover'); await second.card.act('verify');
  assert.equal(calls.filter(x => x.action === 'recover').length, 1);
  assert.equal(calls.find(x => x.action === 'verify').operation, ticket.operation);
});

test('simultaneous tabs cannot both open and submit mutation confirmation', async () => {
  let finish, started;
  const entered = new Promise(resolve => started = resolve), calls = [];
  const send = service(calls), store = new BrowserPendingStore(browser.indexedDB);
  const first = new RecoveryFlow(send, () => {started(); return new Promise(resolve => finish = resolve);}, () => 1000, store);
  const second = new RecoveryFlow(send, async () => true, () => 1000, store);
  await first.check(); await second.check();
  const action = first.recover(); await entered;
  await assert.rejects(second.recover(), /another tab/);
  finish(true); await assert.rejects(action, /lost response/);
  assert.equal(calls.filter(x => x.action === 'recover').length, 1);
});

test('only a matching definitive response clears pending, never a historical result', async () => {
  const flow = new RecoveryFlow(service([]), async () => true, () => 1000);
  await flow.check(); await assert.rejects(flow.recover(), /lost response/);
  for (const result of [{operation: 'older-operation', verified_release: true},
    {operation: ticket.operation, result: 'operator_required'},
    {operation: ticket.operation, result: 'operator_required', verified_release: true},
    {operation: ticket.operation, result: 'released_readiness_pending', lease_id: 'wrong-lease', verified_release: true},
    {operation: ticket.operation, result: 'released_readiness_pending', identity: {boot_id: 'wrong-boot'}, verified_release: true},
    {operation: ticket.operation, result: 'released_readiness_pending', resources: ['gpu0'], verified_release: true},
    {operation: ticket.operation, result: 'released_readiness_pending', ownership_released: false, verified_release: true},
    {operation: ticket.operation, result: 'released_readiness_pending', terminal: 'reconciling', verified_release: true},
    {operation: ticket.operation, result: 'released_readiness_pending', replayed: true, verified_release: true},
    {operation: ticket.operation, result: 'aborted_verified', verified_release: true},
    {operation: ticket.operation, job_id: 'wrong-job', verified_release: true}]) {
    await flow.acceptResult(result); assert.equal(flow.pending.operation, ticket.operation);
  }
  await flow.acceptResult({operation: ticket.operation, job_id: ticket.job_id, verified_release: true,
    result: 'released_readiness_pending', terminal: 'failed', replayed: false});
  assert.equal(flow.pending, null); assert.equal(await flow.pendingStore.load(), null);
});

test('matching verified abort clears pending without claiming release', async () => {
  const flow = new RecoveryFlow(service([]), async () => true, () => 1000);
  await flow.check(); await assert.rejects(flow.recover(), /lost response/);
  await flow.acceptResult({operation: ticket.operation, result: 'aborted_verified', ownership_released: false});
  assert.equal(flow.pending, null);
});

test('storage failure and invalid pending data fail before POST', async () => {
  for (const kind of ['storage', 'corrupt']) {
    browser = installBrowser(); const calls = [];
    const store = new BrowserPendingStore(kind === 'storage' ? null : browser.indexedDB);
    if (kind === 'corrupt') await store.update(state => {state.pending = {operation: 'corrupt'};});
    const flow = new RecoveryFlow(service(calls), async () => true, () => 1000, store);
    await assert.rejects(flow.check()); await assert.rejects(flow.recover());
    assert.equal(calls.filter(x => x.action === 'recover').length, 0);
  }
});

test('ticket expiry during confirmation leaves no pending record and sends no mutation', async () => {
  let clock = 1000; const calls = [], checked = {...ticket, expires: 1001};
  const flow = new RecoveryFlow(async message => {calls.push(message); return checked;},
    async () => {clock = 1002; return true;}, () => clock);
  await flow.check(); await assert.rejects(flow.recover(), /expired/);
  assert.equal(await flow.pendingStore.load(), null); assert.equal(calls.length, 1);
});

test('expired unconfirmed reservation never authorizes a late POST', async () => {
  let clock = 0; const calls = [];
  const store = new BrowserPendingStore(browser.indexedDB, () => clock);
  const flow = new RecoveryFlow(service(calls), async () => {clock = 120001; return true;}, () => 1000, store);
  await flow.check(); await assert.rejects(flow.recover(), /reservation expired/);
  assert.equal(calls.filter(x => x.action === 'recover').length, 0);
  assert.equal(await store.load(), null);
});

test('persisted pending state fences recovery even when readback fails before POST', async () => {
  const calls = [], store = new BrowserPendingStore(browser.indexedDB);
  const save = store.save.bind(store);
  store.save = async value => {await save(value); throw Error('synthetic failed readback');};
  const flow = new RecoveryFlow(service(calls), async () => true, () => 1000, store);
  await flow.check(); await assert.rejects(flow.recover(), /failed readback/);
  assert.equal(calls.filter(x => x.action === 'recover').length, 0);
  const restarted = new RecoveryFlow(service(calls), async () => true, () => 1000);
  await assert.rejects(restarted.recoverNow(), /use Verify/);
  assert.equal((await restarted.pendingStore.load()).operation, ticket.operation);
});
