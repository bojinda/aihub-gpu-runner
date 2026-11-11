// Immutable dialog capture is the approval. No entity, helper or template can
// replace the ticket while the confirmation dialog is open.
export class RecoveryFlow {
  constructor(send, confirm, now = () => Date.now() / 1000) {
    this.send = send; this.confirm = confirm; this.now = now;
    this.checked = null; this.busy = false; this.operation = null;
  }
  async check(action = "check") {
    if (this.busy) throw new Error("An operation is already in progress.");
    this.checked = null;
    const result = await this.send({action});
    if (result.eligible === true) this.checked = structuredClone(result);
    return result;
  }
  async recover() {
    if (this.busy) throw new Error("An operation is already in progress.");
    const chosen = structuredClone(this.checked);
    if (!chosen || chosen.eligible !== true || this.now() >= chosen.expires) {
      this.checked = null;
      throw new Error("Run Check Recovery again; the approval has expired.");
    }
    this.busy = true;
    try {
      const description = `${chosen.effect}\n\nJob: ${chosen.job_id}\nLease: ${chosen.identity.lease_id}\nRequest: ${chosen.identity.request_hash}\nResources: ${(chosen.identity.resources || []).join(", ")}\nBoot: ${chosen.identity.boot_id}\nPolicy: ${chosen.identity.policy_id || "Bound by server ticket"}\nClass: ${chosen.classification || chosen.case}\nIncident: ${chosen.incident}\nExpires: ${new Date(chosen.expires*1000).toLocaleTimeString()}`;
      if (!await this.confirm(description)) return {result: "cancelled"};
      if (this.now() >= chosen.expires) throw new Error("Confirmation expired. Check again.");
      this.checked = null;
      this.operation = chosen.operation || null;
      const approval = {action: chosen.action, token: chosen.token, confirmed: true};
      if (chosen.loss_required === true) approval.accept_work_loss = true;
      return await this.send(approval);
    } finally { this.busy = false; }
  }
  async recoverNow(emergency = false) {
    // Fresh read-only check, followed by ONE immutable confirmation dialog.
    const checked = await this.check(emergency ? "check-emergency" : "check");
    if (!checked.eligible) return checked;
    return await this.recover();
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
      <p>The existing acknowledgment button only clears the display count.</p></ha-card>`;
      this.shadowRoot.querySelectorAll("button").forEach(button => button.onclick = () => this.act(button.dataset.action));
      this.refresh();
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
      if (value.configuration_errors?.length) this.flow.checked = null;
      const fields = {...(value.configuration_errors?.length ? {"Recovery configuration error": value.configuration_errors.join(", ")} : {}), Readiness: value.readiness === true ? "Ready" : value.readiness === false ? "Blocked" : "Unknown", ...(value.integrated ? {
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
        "Last observed": new Date(value.observed_at*1000).toLocaleString()};
      const list = this.shadowRoot.querySelector("dl"); list.replaceChildren();
      for (const [key, val] of Object.entries(fields)) {
        const dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = key; dd.textContent = val; list.append(dt, dd);
      }
      if (value.attempt?.id) this.flow.operation = value.attempt.id;
    } catch {
      this.status = null; this.flow.checked = null;
      this.shadowRoot.querySelector("dl").textContent = "Runner status unavailable; operator review required.";
    } finally { this.polling = false; this.buttons(); }
  }
  buttons() {
    const recover = this.shadowRoot.querySelector('[data-action="recover"]');
    const practical = this.status?.capabilities?.includes("practical_recovery_v1");
    const supported = practical || this.status?.capabilities?.includes("manual_idle_recovery_v1");
    const abort = this.flow.checked?.action === "abort";
    recover.disabled = this.flow.busy || !this.status || !!this.status.configuration_errors?.length || ((!supported || abort) && (!this.flow.checked || this.flow.now() >= this.flow.checked.expires));
    recover.textContent = abort ? "Confirm Abort…" : supported ? "Recover Now…" : "Recover…";
    const emergency = this.shadowRoot.querySelector('[data-action="emergency"]');
    emergency.hidden = !practical;
    emergency.disabled = this.flow.busy || !this.status || !!this.status.configuration_errors?.length;
    this.shadowRoot.querySelector('[data-action="check-abort"]').hidden = this.status?.integrated === true;
  }
  async act(action) {
    const display = this.shadowRoot.querySelector(".message");
    try {
      let result;
      if (action === "recover") result = (this.status?.capabilities?.includes("practical_recovery_v1") || this.status?.capabilities?.includes("manual_idle_recovery_v1")) && this.flow.checked?.action !== "abort"
        ? await this.flow.recoverNow() : await this.flow.recover();
      else if (action === "emergency") result = await this.flow.recoverNow(true);
      else if (action === "verify") {
        if (!this.flow.operation) throw new Error("No interrupted operation is recorded.");
        result = await this.flow.send({action: "verify", operation: this.flow.operation});
      } else result = await this.flow.check(action);
      display.textContent = result.eligible === true ? `Eligible: ${result.effect}` :
        result.eligible === false ? `Blocked: ${result.failed_checks.map(code => code.replaceAll("_", " ")).join("; ")}` : JSON.stringify(result);
    } catch (error) { display.textContent = error.message || "Unavailable; verify the recorded operation."; }
    this.buttons();
    await this.refresh();
  }
  getCardSize() { return 6; }
}
if (globalThis.customElements && !customElements.get("aihub-recovery-card")) customElements.define("aihub-recovery-card", AIHubRecoveryCard);
