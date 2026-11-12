// Immutable dialog capture is the approval. No entity, helper or template can
// replace the ticket while the confirmation dialog is open.
// IndexedDB transactions serialize tabs even on a private HTTP HA origin.
// These records carry no credential, ticket or server-side authority.
export class BrowserPendingStore {
  constructor(indexedDB = globalThis.indexedDB, now = () => Date.now(), key = "state") {
    this.indexedDB = indexedDB; this.now = now; this.reservation = null; this.key = key;
  }
  async update(change, writable = true) {
    if (!this.indexedDB) throw new Error("Recovery persistence unavailable; mutation disabled.");
    const database = await new Promise((resolve, reject) => {
      const request = this.indexedDB.open("aihub-recovery-v1", 1);
      const timeout = setTimeout(() => reject(new Error("Recovery persistence unavailable.")), 2000);
      request.onupgradeneeded = () => request.result.createObjectStore("control");
      request.onsuccess = () => {clearTimeout(timeout); resolve(request.result);};
      request.onerror = () => {clearTimeout(timeout); reject(request.error);};
      request.onblocked = () => {clearTimeout(timeout); reject(new Error("Recovery persistence busy."));};
    });
    try {
      return await new Promise((resolve, reject) => {
        const transaction = database.transaction("control", writable ? "readwrite" : "readonly");
        const store = transaction.objectStore("control"); let result, failure;
        const request = store.get(this.key);
        request.onsuccess = () => {
          try {
            const state = request.result || {pending: null, reservation: null};
            result = change(state);
            if (writable) store.put(state, this.key);
          } catch (error) {failure = error; transaction.abort();}
        };
        transaction.oncomplete = () => resolve(result);
        transaction.onabort = transaction.onerror = () => reject(failure || transaction.error || new Error("Recovery persistence failed."));
      });
    } finally { database.close(); }
  }
  async load() {
    return this.update(state => {
      const value = state.pending;
      if (value === null) return null;
      if (value?.schema !== 1 || typeof value.operation !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(value.operation)
          || typeof value.job_id !== "string" || !value.identity || typeof value.identity.lease_id !== "string"
          || !["recover", "emergency", "abort"].includes(value.action))
        throw new Error("Invalid pending recovery record; operator review required.");
      return value;
    }, false);
  }
  async save(value) {
    await this.update(state => {
      if (!this.reservation || state.reservation?.id !== this.reservation || state.reservation.expires <= this.now())
        throw new Error("Recovery confirmation reservation expired; check again.");
      if (state.pending) throw new Error("Recovery outcome pending; use Verify.");
      state.pending = value;
    });
    if (JSON.stringify(await this.load()) !== JSON.stringify(value)) throw new Error("Recovery persistence readback failed.");
  }
  async clear(operation) {
    return this.update(state => {if (state.pending?.operation === operation) state.pending = null;});
  }
  async exclusive(work) {
    const bytes = new Uint8Array(16); globalThis.crypto.getRandomValues(bytes);
    const id = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    await this.update(state => {
      if (state.pending) throw new Error("Recovery outcome pending; use Verify.");
      if (state.reservation && state.reservation.expires > this.now()) throw new Error("Recovery is active in another tab; use Verify.");
      state.reservation = {id, expires: this.now() + 120000};
    });
    this.reservation = id;
    try { return await work(); }
    finally {
      this.reservation = null;
      await this.update(state => {if (state.reservation?.id === id) state.reservation = null;});
    }
  }
}

export class RecoveryFlow {
  constructor(send, confirm, now = () => Date.now() / 1000, pendingStore = new BrowserPendingStore()) {
    this.send = send; this.confirm = confirm; this.now = now;
    this.checked = null; this.busy = false; this.operation = null;
    this.pendingStore = pendingStore; this.pending = null; this.persistenceError = null;
    this.syncPending();
  }
  async syncPending() {
    try {
      this.pending = await this.pendingStore.load(); this.persistenceError = null;
      if (this.pending) this.operation = this.pending.operation;
    } catch (error) { this.persistenceError = error.message; }
  }
  async requireClear() {
    await this.syncPending();
    if (this.persistenceError) throw new Error(this.persistenceError);
    if (this.pending) throw new Error("Recovery outcome pending; use Verify for " + this.pending.operation + ".");
  }
  async acceptResult(value) {
    // A status summary is never a completion receipt. Only the exact POST or
    // explicit Verify response may clear a pending operation.
    if (!this.pending || value?.operation !== this.pending.operation) return;
    for (const job of [value.job_id, value.request_id, value.identity?.job_id]) {
      if (job !== undefined && job !== this.pending.job_id) return;
    }
    for (const key of ["lease_id", "request_hash", "boot_id", "policy_id", "resources"]) {
      const expected = this.pending.identity[key];
      for (const observed of [value[key], value.identity?.[key], key === "lease_id" ? value.lease : undefined]) {
        if (observed === undefined) continue;
        const canonical = item => Array.isArray(item) ? JSON.stringify([...item].sort()) : item;
        if (expected === undefined || canonical(observed) !== canonical(expected)) return;
      }
    }
    const released = ["recovered_ready", "released_readiness_pending", "succeeded"].includes(value.result) &&
      value.ownership_released !== false && value.replayed !== true && value.no_replay !== false &&
      (value.terminal === undefined || ["success", "failed", "cancelled"].includes(value.terminal)) &&
      (value.verified_release === true || value.no_replay === true ||
       value.result === "succeeded" && value.verification?.audit_verified === true && value.verification?.paired_release === true);
    const aborted = value.verified_release !== true && value.ownership_released !== true &&
      (value.result === "aborted_verified" || value.result === "aborted" && value.verification?.client_access === "permanent_gateway_gate_open");
    if (!released && !aborted) return;
    await this.pendingStore.clear(this.pending.operation);
    this.pending = null;
  }
  async verify() {
    if (this.busy) throw new Error("An operation is already in progress.");
    await this.syncPending();
    if (this.persistenceError) throw new Error(this.persistenceError);
    if (!this.operation) throw new Error("No interrupted operation is recorded.");
    const operation = this.operation;
    this.busy = true;
    try {
      const value = await this.send({action: "verify", operation});
      await this.acceptResult(value);
      return value;
    } finally { this.busy = false; }
  }
  async check(action = "check") {
    if (this.busy) throw new Error("An operation is already in progress.");
    this.checked = null;
    await this.syncPending();
    if (this.persistenceError) throw new Error(this.persistenceError);
    if (this.pending) return {eligible: false, operation: this.pending.operation,
      failed_checks: ["pending_recovery_verify_required"]};
    const result = await this.send({action});
    await this.syncPending();
    if (this.persistenceError) throw new Error(this.persistenceError);
    if (this.pending) return {eligible: false, operation: this.pending.operation,
      failed_checks: ["pending_recovery_verify_required"]};
    if (result.eligible === true && !this.pending && !this.persistenceError) this.checked = structuredClone(result);
    return result;
  }
  async recover() {
    if (this.busy) throw new Error("An operation is already in progress.");
    const chosen = structuredClone(this.checked);
    if (!chosen || chosen.eligible !== true || this.now() >= chosen.expires) {
      this.checked = null;
      throw new Error("Run Check Recovery again; the approval has expired.");
    }
    if (typeof chosen.operation !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(chosen.operation) ||
        typeof chosen.identity?.lease_id !== "string") throw new Error("Recovery binding unavailable; mutation disabled.");
    this.busy = true;
    try {
      await this.requireClear();
      return await this.pendingStore.exclusive(async () => {
      await this.requireClear();
      const description = `${chosen.effect}\n\nJob: ${chosen.job_id}\nLease: ${chosen.identity.lease_id}\nRequest: ${chosen.identity.request_hash}\nResources: ${(chosen.identity.resources || []).join(", ")}\nBoot: ${chosen.identity.boot_id}\nPolicy: ${chosen.identity.policy_id || "Bound by server ticket"}\nClass: ${chosen.classification || chosen.case}\nIncident: ${chosen.incident}\nExpires: ${new Date(chosen.expires*1000).toLocaleTimeString()}`;
      if (!await this.confirm(description)) return {result: "cancelled"};
      if (this.now() >= chosen.expires) throw new Error("Confirmation expired. Check again.");
      await this.requireClear();
      const pending = {schema: 1, operation: chosen.operation, action: chosen.action,
        job_id: chosen.job_id, identity: Object.fromEntries(["lease_id", "request_hash", "boot_id", "policy_id", "resources"]
          .filter(key => chosen.identity[key] !== undefined).map(key => [key, structuredClone(chosen.identity[key])]))};
      // Persist and read back before sending even one mutation. Never persist
      // the bearer token, approval ticket or inferred authorization.
      await this.pendingStore.save(pending);
      this.pending = pending; this.operation = pending.operation;
      this.checked = null;
      const approval = {action: chosen.action, token: chosen.token, confirmed: true};
      if (chosen.loss_required === true) approval.accept_work_loss = true;
      const value = await this.send(approval);
      await this.acceptResult(value);
      return value;
      });
    } finally { this.busy = false; }
  }
  async recoverNow(emergency = false) {
    // Fresh read-only check, followed by ONE immutable confirmation dialog.
    await this.requireClear();
    const checked = await this.check(emergency ? "check-emergency" : "check");
    if (!checked.eligible) return checked;
    return await this.recover();
  }
}

export class EstopFlow {
  constructor(send) { this.send=send;this.busy=false;this.result=null; }
  async execute() {
    if(this.busy) return null;
    this.busy=true;
    try {
      const value=await this.send({action:"estop"});
      if(!["success","failure","unknown"].includes(value?.outcome) ||
         !Number.isFinite(value.timestamp) ||
         !(value.outcome==="unknown" && value.released_leases===null ||
           Number.isInteger(value.released_leases) && value.released_leases>=0 && value.released_leases<=2) ||
         !(value.error===null || typeof value.error==="string" && /^[a-z0-9_]{1,80}$/.test(value.error))) throw Error("invalid response");
      this.result=value;
    } catch (error) {
      const denied=["unauthorized","invalid_request","unknown_command"].includes(error?.code);
      this.result={timestamp:Date.now()/1000,outcome:denied?"failure":"unknown",released_leases:denied?0:null,
        error:denied?error.code:"response_unavailable_no_retry"};
    } finally { this.busy=false; }
    return this.result;
  }
}

const ElementBase = globalThis.HTMLElement || class {};
export class AIHubRecoveryCard extends ElementBase {
  setConfig(config) { this.config = config; }
  set hass(hass) {
    this._hass = hass;
    if (!this.flow) {
      this.flow = new RecoveryFlow(message => this._hass.callWS({type: "aihub_recovery/control", ...message}),
        text => Promise.resolve(window.confirm(text)));
      this.estop = new EstopFlow(message => this._hass.callWS({type:"aihub_recovery/control",...message}));
      this.attachShadow({mode: "open"});
      this.shadowRoot.innerHTML = `<style>
        ha-card{display:block;padding:20px}h2{font-size:20px;margin:0 0 14px}
        dl{display:grid;grid-template-columns:130px 1fr;gap:8px}dt{font-weight:600}
        dd{margin:0;overflow-wrap:anywhere}button{padding:10px;margin:4px;cursor:pointer}
        pre{white-space:pre-wrap;overflow-wrap:anywhere}.message{min-height:32px}
      </style><ha-card><h2>AI-HUB GPU Recovery</h2><dl></dl><div class="message" role="status" aria-live="polite"></div>
      <button data-action="check">Check Recovery</button><button data-action="recover" disabled>Recover…</button>
      <button data-action="emergency" hidden>Emergency Release…</button>
      <button data-action="verify">Verify interrupted operation</button>
      <button data-action="check-abort">Check Abort</button>
      <hr><p>Global E-stop relinquishes runner ownership. GPU work may continue and overlap.</p>
      <button data-action="estop">E-STOP</button><div class="estop-state" role="status">GPU physical activity: UNVERIFIED</div>
      <p>The existing acknowledgment button only clears the display count.</p></ha-card>`;
      this.shadowRoot.querySelectorAll("button").forEach(button => button.onclick = () => this.act(button.dataset.action));
      this.initialRefresh = this.refresh();
    }
  }
  connectedCallback() {
    this.timer ||= setInterval(() => this.refresh(), 15000);
  }
  disconnectedCallback() { clearInterval(this.timer); this.timer = null; }
  async refresh() {
    if (!this.flow || this.polling) return;
    this.polling = true;
    try {
      const value = await this.flow.send({action: "status"});
      if (value.available !== true) throw new Error("Unavailable");
      this.status = value;
      await this.flow.syncPending();
      if (value.configuration_errors?.length) this.flow.checked = null;
      const fields = {...(value.configuration_errors?.length ? {"Recovery configuration error": value.configuration_errors.join(", ")} : {}),
        ...(this.flow.persistenceError ? {"Recovery browser configuration error": this.flow.persistenceError} : {}),
        Readiness: value.readiness === true ? "Ready" : value.readiness === false ? "Blocked" : "Unknown", ...(value.integrated ? {
        Admission: value.admission_ready ? "Open" : "Blocked", Capacity: value.capacity_reason.replaceAll("_", " ")} : {}), "Retained owners": JSON.stringify(value.owners),
        "Affected job": value.affected_jobs.join(", ") || "None", Reason: value.recovery_reasons.filter(Boolean).join(", ") || "None",
        "Operator required": value.operator_required ? "Yes" : "No", Maintenance: value.maintenance,
        ...(value.media_output ? {"Wallpaper output": [value.media_output.operation_status, value.media_output.last_operation_error].filter(Boolean).join(": "),
          "Wallpaper dispatch": [value.media_output.dispatch_status, value.media_output.reason].filter(Boolean).join(": "),
          "Wallpaper intervention": value.media_output.operator_required ? "Required" : "No"} : {}),
        ...(value.media_output?.blocking_job ? {"Wallpaper blocker":
          `${value.media_output.blocking_job.target} ${value.media_output.blocking_job.state}: ${value.media_output.blocking_job.request_id} (${value.media_output.blocking_job.error_code || "verification required"})`} : {}),
        ...(value.incidents ? {"Recovery details": value.incidents.map(i => `${i.job_id}: ${i.resources?.join(", ")} / ${i.phase || "unknown phase"} / ${i.classification}; ${i.failed_checks.join(", ") || i.error_code || "Check for fresh eligibility"}`).join("\n") || "None"} : {}),
        ...(value.attempt ? {"Recovery attempt": `${value.attempt.id}: ${value.attempt.phase || value.attempt.status}; ${(value.attempt.checks || []).join(", ")}`} : {}),
        ...(this.flow.pending ? {"Pending Verify operation": this.flow.pending.operation,
          "Pending job / lease": `${this.flow.pending.job_id} / ${this.flow.pending.identity.lease_id}`} : {}),
        "Last observed": new Date(value.observed_at*1000).toLocaleString()};
      const list = this.shadowRoot.querySelector("dl"); list.replaceChildren();
      for (const [key, val] of Object.entries(fields)) {
        const dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = key; dd.textContent = val; list.append(dt, dd);
      }
      await this.flow.syncPending();
    } catch {
      this.status = null; this.flow.checked = null;
      this.shadowRoot.querySelector("dl").textContent = "Runner status unavailable; operator review required.";
    } finally { this.polling = false; await this.buttons(); }
  }
  async buttons() {
    await this.flow.syncPending();
    const pending = !!this.flow.pending || !!this.flow.persistenceError;
    const recover = this.shadowRoot.querySelector('[data-action="recover"]');
    const practical = this.status?.capabilities?.includes("practical_recovery_v1");
    const supported = practical || this.status?.capabilities?.includes("manual_idle_recovery_v1");
    const abort = this.flow.checked?.action === "abort";
    recover.disabled = pending || this.flow.busy || !this.status || !!this.status.configuration_errors?.length || ((!supported || abort) && (!this.flow.checked || this.flow.now() >= this.flow.checked.expires));
    recover.textContent = abort ? "Confirm Abort…" : supported ? "Recover Now…" : "Recover…";
    const emergency = this.shadowRoot.querySelector('[data-action="emergency"]');
    emergency.hidden = !practical;
    emergency.disabled = pending || this.flow.busy || !this.status || !!this.status.configuration_errors?.length;
    this.shadowRoot.querySelector('[data-action="check-abort"]').hidden = this.status?.integrated === true;
    this.shadowRoot.querySelector('[data-action="estop"]').disabled=this.estop.busy;
  }
  async act(action) {
    const display = this.shadowRoot.querySelector(".message");
    try {
      let result;
      if(action==="estop") {
        const button=this.shadowRoot.querySelector('[data-action="estop"]');button.disabled=true;
        result=await this.estop.execute();
        if(result) this.shadowRoot.querySelector(".estop-state").textContent=
          `${new Date(result.timestamp*1000).toLocaleString()} — ${result.outcome.toUpperCase()}; released leases: ${result.released_leases ?? "unknown"}; ${result.error || "GPU work continues; physical activity UNVERIFIED"}`;
        await this.buttons();return; // No pre-check, status refresh, retry or Verify.
      }
      else if (action === "recover") result = (this.status?.capabilities?.includes("practical_recovery_v1") || this.status?.capabilities?.includes("manual_idle_recovery_v1")) && this.flow.checked?.action !== "abort"
        ? await this.flow.recoverNow() : await this.flow.recover();
      else if (action === "emergency") result = await this.flow.recoverNow(true);
      else if (action === "verify") {
        result = await this.flow.verify();
      } else result = await this.flow.check(action);
      display.textContent = result.eligible === true ? `Eligible: ${result.effect}` :
        result.eligible === false ? `Blocked: ${result.failed_checks.map(code => code.replaceAll("_", " ")).join("; ")}` : JSON.stringify(result);
    } catch (error) { display.textContent = error.message || "Unavailable; verify the recorded operation."; }
    await this.buttons();
    await this.refresh();
  }
  getCardSize() { return 6; }
}
if (globalThis.customElements && !customElements.get("aihub-recovery-card")) customElements.define("aihub-recovery-card", AIHubRecoveryCard);
