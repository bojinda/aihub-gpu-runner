# Runner-managed on-demand wallpaper — offline candidate

This delta extends the reviewed combined candidate. It does not replace the
scheduler or change models, prompts, workflows, CLIP loader, budgets, placement
of other workloads, or dashboard appearance. The sealed 2026-10-24 release is
unchanged and does not contain this new lifecycle; do not deploy its old wheel
as if it implements these additions.

## Opt-in contract

Only the named Comfy wallpaper target supports `lifecycle: "on_demand"`.
Omitting it preserves always-on behavior. Add explicit `startup_timeout` and
`shutdown_timeout` seconds; keep the existing execution/acquisition/cleanup
budgets unchanged. Startup must complete before the execution deadline is
created. Shutdown has its own persisted deadline; a stalled normal TERM is
handed to bounded failure recovery, rather than silently extending that deadline.

On-demand serving requires the scope-bound recovery policy to be installed.
Use `examples/recovery-policy.on-demand.example.json`: it requires
`boot_startup: "stopped_idle"` and separately calibrated aggregate
`stopped_idle_memory_mb` for both physical GPUs. The existing running/per-service
ceilings remain required. The stopped baseline permits identified idle music
contexts; unknown contexts, GPU faults, UUID/identity drift and unverified cleanup
retain ownership. No example identity/null ceiling is production-ready.

After full admission and both original native locks, the runner verifies the
exact stopped container, calibrated idle state, and immutable policy binding;
records start intent; starts that same container once; verifies its new generation,
physical UUID order and bounded readiness; then records submission intent and
submits once. Container recreation is never part of this path.

Terminal history and validated artifact retrieval precede durable result writes.
Result metadata and artifact hashes are recorded before shutdown. Only a verified
durable result permits stopping the backend. The runner records TERM intent and
original process identities, stops only the exact owned generation, verifies
process exit, both physical GPUs and expected other queues against the stopped
baseline, and releases both owners atomically. Stopped Comfy HTTP is not queried
for readiness. Success/result access is withheld until cleanup and handoff.

Jobs store `wallpaper_lifecycle`; the existing ownership journal gains
`wallpaper_lifecycle_receipts`. A normal receipt and paired release share one
AtomicStore write, then job success is finalized. If interrupted after release,
startup finishes that job from the receipt and verified artifact hashes, before
the old never-admitted-job reconciliation. No normal cycle creates a recovery
incident, audit or failure-attempt budget entry. Summary schema and counter code
are unchanged. Verified reboots and actual abnormal incidents still count.

Same-boot recovery observes surviving execution until its persisted execution
deadline; API errors alone prove neither death nor permission to terminate.
Startup failures use the startup deadline instead. Existing terminal history can
recover an artifact without another inference submission. Artifact persistence
gets at most two automatic publication attempts per incident; exhaustion retains
the backend/owners and requests an operator. Normal healthy shutdown sends one
TERM. Escalation is an abnormal recovery action: one policy-permitted KILL,
durable incident budget and intent precede it. Failed jobs normally finish stopped
and clean; only a future NEW job may start the container again.

Failure recovery keeps its original bounded overall observation deadline.
History/artifact reconciliation receives at most 5 seconds and one quarter of
the remaining attempt. The pre-termination wallpaper queue request receives at
most 2 seconds and one eighth of its remaining attempt. These HTTP timeouts do
not renew the overall deadline; exact-container termination, process exit and
stopped-GPU cleanup must still finish within it. Once the actual execution or
startup deadline has expired, unavailable wallpaper HTTP alone cannot block
otherwise-authorized termination. Foreign work, identity drift, unhealthy GPUs
and unverifiable cleanup continue to retain both owners.

Read-only HTTP uses one monotonic request deadline for connection, TLS, response
headers and the entire body (including chunked responses and artifacts). Every
raw receive uses only the remaining time; trickled bytes never renew the budget.
Numeric addresses need no resolver helper. Hostnames use one bounded, kill/reaped
DNS helper sharing that deadline, since system DNS has no cancellation API. No
HTTP helper threads or request retries are created. Response limits, strict JSON,
redirect/proxy refusal and certificate verification remain in force. Generation
POST requests keep their existing transport behavior.

Unattended recovery from a TERM-resistant hang requires an operator-approved
`allow_force: true` policy bound to the exact wallpaper container, plus an
observation window sufficient for the approved grace and verified cleanup.
The example defaults to `allow_force: false`; that setting deliberately requires
an operator if TERM does not stop the container. Normal shutdown sends only one
TERM. Abnormal escalation permits at most one approved KILL, with durable intent,
the existing failure-attempt budget and observed process/GPU cleanup afterward.

Boot reconciliation never starts on-demand wallpaper. Stopped idle is healthy;
an unexpectedly running or unverifiable container leaves the boot fence closed.
Both meeting reservations and intended GPU1-only Ollama restoration checks remain
mandatory. Same-boot independently supervised host work remains untouched.

## Activation update for the reported checkpoint

Reported checkpoint: reviewed old wheel installed, runner stopped; recreated dual
wallpaper running; active CPU/GPU0 config; validated pending dual config; no scope
transition or recovery authorization; hourly scheduling disabled. Nothing below
has been executed. Each production action still requires an approved window.

1. Keep hourly scheduling disabled and exclude all participating callers. Preserve
   the installed wheel/venvs/packages, current app, exact trusted production CPU
   workflow/config and pending dual config. Preserve current jobs/history/locks;
   never use the repository's older CPU workflow as a rollback substitute.
2. Review/build a NEW immutable wheel containing this delta; install the matching
   package in AI-HUB, Media and host-stage interpreters without dependency/model
   changes. Preserve the sealed release as rollback evidence. Verify the already
   recreated container's final ID/image/contract, dual UUID exposure/order,
   restart policy `no`, no independent restarter and service ordering/access.
   No further recreation is required by this implementation.
3. Under separate exact-container authorization, obtain fresh running/stopped
   baselines for both GPUs and known services, and approve startup/shutdown limits.
   Stop only the approved idle wallpaper container and verify exit and stopped
   cleanup; never infer proof from elapsed time. Require all jobs terminal and
   owners absent. Do not start the old CPU-scoped runner against the dual backend.
4. Freeze a final config derived from the validated pending dual config, adding
   ONLY `lifecycle: "on_demand"`, explicit `startup_timeout` and `shutdown_timeout`.
   Keep workflow bytes/hash, models, GPU0 ceiling and measured GPU1 ceiling intact.
   Recompute scopes/hashes and refresh both staged Media/AI-HUB workflow hashes.
   Use the new combined fixed transition directly from frozen CPU to final dual
   on-demand config; do not first commit the older conditioning-only transition:

   ```sh
   "$NEW_PYTHON" -m aihub_gpu_runner --config "$FROZEN_CPU_CONFIG" \
     transition-wallpaper-on-demand --new-config "$FROZEN_FINAL_CONFIG" \
     --evidence-file "$APPROVED_ON_DEMAND_TRANSITION_EVIDENCE"
   ```

   Evidence retains all conditioning scope/config/template/journal/UUID/calibration
   and Media hash bindings. Replace its approval attestation with
   `operator_approved_wallpaper_on_demand_transition: true` and additionally attest
   `wallpaper_stopped: true`; all other conditioning attestations remain required.
   The single journal receipt enables the exact guarded CPU inverse. Config
   activation remains a separate fsynced byte replacement; interrupted transitions
   use the existing exact-evidence repeat and directory-fsync procedure.
5. Activate final config consistently and select Media's matching workflow.
   Populate the new private policy from fresh FINAL scope/container identities,
   measured running/stopped/service baselines and approved limits; authorize once
   using the existing `authorize-recovery` action while wallpaper is stopped.
   Only then start the new runner. Idle startup must leave wallpaper stopped.
6. Keep scheduling paused through separately approved controlled acceptance:
   two NEW runner-mediated operations should each start, submit once, save, stop
   and release both owners. Verify artifacts, CPU-reference quality, actual EPD
   appearance, stopped VRAM, unchanged normal recovery count and attempt ledger.
   Review before explicit scheduling acceptance; never replay an uncertain job.

## Rollback and exact unsupported cases

Before recovery authorization, with callers excluded, every job terminal, no
owners and verified cleanup, use the SAME new command with `--rollback`, final
config as `--config`, frozen CPU as `--new-config`, fresh reverse evidence and
`rollback_of` equal to the forward approval. Restore exact CPU config/workflow,
approved GPU0-only exposure/mounts and compatible saved packages separately.
Starting an always-on CPU backend is a separate approved activation action.

After authorization, this scope change is deliberately refused: a stopped-idle
policy cannot silently become an always-on CPU policy. Scope/container/policy
migration needs separate review. Keep the current ownership journal, receipts,
recovery audits and budgets; never delete a policy or restore an older journal.
Package rollback alone needs a reviewed compatible runtime/config/container plan.

Ambiguous start intent with no observed effect, identity/driver/placement/cleanup
failure, ambiguous surviving KILL, exhausted recovery/publication budgets and
legacy unresolved leases without lifecycle binding require an operator. No start
or inference replay is attempted. Same-boot Ollama/host-stage uncertainty and
dual-meeting restoration keep their existing operator paths. The new transition
is specifically the reported CPU-to-dual checkpoint, not an arbitrary scope or
already-authorized-policy migration.
