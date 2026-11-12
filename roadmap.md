# AI-HUB — Production Reliability and Repository Restoration Roadmap

**Revision:** 1.0 — November 11, 2026, America/Toronto  
**Owner:** Operator  
**Canonical destination:** `aihub-gpu-runner/roadmap.md` after source reconciliation  
**Status:** Offline implementation candidate; D1–D4 reviewed. G0/G1/G2/G4 pass within the documented qualification envelope; G3 is partial and G5 remains on hold. See [qualification](docs/qualification.md). No production deployment, recovery, history rewrite or automatic-authority expansion is authorized by this document alone.

## 0. Decision and definition of success

Proceed with one coordinated, offline implementation effort based on the pinned audit sources. Another broad evidence-collection exercise is not a prerequisite. Targeted verification against the existing private originals is required for redacted source, deployment bindings, and the exact incident present at deployment time.

The objective is dependable ordinary Music, Wallpaper, ToDo, meeting and lesson operation using the existing GPU runner. This is not Aurora Gateway work, a replacement coordinator, or a new recovery service.

A release is not accepted because an installer passes, an operator recovers one lease, or one scheduled job succeeds. Acceptance requires:

- Ordinary known-safe backend turnover completes cleanup without routine operator rescue.
- Real unsafe or insufficiently evidenced activity remains contained, with a specific diagnosis and an honest supported recovery path or refusal.
- Home Assistant preserves the exact uncertain operation through Check, confirmation, polling, lost responses and Verify.
- Settled application history does not accumulate into a permanent dispatch blocker.
- The running software is reproducible from maintainable source repositories, not an assortment of unpublished patches and public forensic snapshots.
- Restored ordinary workloads remain stable through five consecutive eligible production days. This is an operational acceptance target, not a statistical guarantee against every future failure.

**Do not equate failure of historical attribution with proof that a GPU remains busy forever. Equally, do not equate present idle memory with authority to clear a lease.** Ordinary cleanup and independently authorized retirement need explicit, different proof contracts.

## 1. Existing decisions that remain binding

Keep the accepted Music GPU0 ceiling at **475 MiB**, its **five-second cleanup budget**, and the existing accepted scope/policy unless an independently demonstrated defect requires a separately approved change. Keep routine Ollama on GPU1, Wallpaper's currently deployed paired-GPU lifecycle, Music/WhisperX integration, and the explicit dual-GPU meeting path. The October roadmap's older GPU0-only Wallpaper description is historical, not the November production contract.

Keep the two ComfyUI containers and their environments separate. Preserve application models, prompts, stage-specific context settings, Home Assistant endpoints, output paths, song duration/playback/promotion, meeting map/reduce processing, approved speaker corrections, and audio-retention behavior. Keep direct Open WebUI and AnythingLLM functionality. Do not make a gateway, permanent firewall, new queue database, new daemon, or migration of every client a prerequisite for this repair.

GPU ownership remains with the runner and its existing native authority. Preserve physical lock identities, durable leases, receipts, audits, completion evidence and failed/no-replay dispositions. Do not delete state, replace a lock file, replay historical work, broadly signal processes, reset GPUs, or restore an old journal to obtain a green status.

Operator confirmation authorizes an exact recovery mutation; a past confirmation is consumed. An uncertain mutation is followed by Verify of that same operation, not a repeated Recover. Approved local commands are authorization for those commands; do not request redundant chat approval when the operator deliberately executes the approved procedure. Privileged deployment work remains interactive sudo; no sudoers or credential changes.

The legacy automation/cron that directly restarts or controls Wallpaper remains disabled. Preserve current scheduling and delivery settings; do not enable EPD delivery or unrelated publication. No website integration is needed. Gateway and later client migration remain deferred until the production stability gate and any pre-existing operator-confirmation milestones are satisfied.

## 2. Review baseline and evidence boundaries

### 2.1 Pinned sources

| Repository | Audit commit | Relevant review location |
|---|---|---|
| `bojinda/aihub-gpu-runner` | `30c14171bfc7544ff7cee6d1be4007233561b514` | Current runner, separately captured SDK/helper/plugin/HA variants, historical deltas and reports |
| `bojinda/meeting-pipeline` | `98da9238f543f823dd1a64acfc9e184f9e3aa6c6` | Deployed `bin/`, controller and tests |
| `bojinda/dashboard-generator` | `3a05a58fc780c9be84f8afe3819a73941e23643d` | Deployed Media application and SDK |
| `bojinda/daily-wakeup-song` | `56a90a5db0d6e78475a6f606f72076d99f796396` | Deployed song application, adapter and SDK |

All use the audit branch `codex/audit-production-reliability-20261111`. Their normal repository roots are **not thereby made current**. The audit README explicitly places production code in isolated snapshot directories. [S1] [S2]

The audit's last reported preservation readback was November 11 at 09:58:39 Toronto. It reported the seasonal Ollama lease still retained, successful ToDo receipts preserved, original locks unchanged, both HA automations ON/inactive and both host paths active/waiting. This is a historical snapshot, not a live readback for a later recovery. Exact operational identities belong in the private deployment packet, not this public-facing roadmap.

### 2.2 Evidence vocabulary

- **Independent reproduction:** Executed here against the specified published source or explicitly identified source expression, using disposable fixtures.
- **Source-confirmed:** A control-flow or contract property established from pinned source; not necessarily observed in production.
- **Audit-reported:** Host observations or prior test results reported by Codex, not repeated here.
- **Unresolved:** Evidence does not establish the historical cause, actual deployed private bytes, or live outcome.
- **Proposed requirement:** A design/acceptance requirement of this roadmap, not a claim that the system implements it today.

### 2.3 What this review did and did not execute

The independent review traced cleanup, native ownership/recovery, HA control transport/card, Media intent/cache/dispatch, and participating client boundaries. It reproduced two additional failure mechanisms with the exact published Media dispatch module and HA card. Both local source copies were Git-blob verified before execution. Eight Media cases and four card cases completed; these are characterization tests of the broken baseline. A separate three-input source-expression check exposed a published HA network-literal problem.

The full 297-test runner selection, nine original card cases, framework-dependent Media reporting suite, production GPU behavior, live HA mutation flow and private original captures were **not** independently rerun here. The original audit's passing counts remain attributed to that audit. Do not add the counts together as one qualification result. The vanished historical GPU PID has not been authenticated by any of these local tests.

## 3. Findings and required disposition

### F01 — Cleanup cannot correlate all ordinary process turnover

**Evidence:** Source-confirmed; current failure and earlier process ambiguity are audit-reported. `OllamaCleanupGuard.sample()` enumerates service processes before obtaining GPU compute rows. A leased-GPU PID absent from both that census and the original cleanup context is immediately rejected. The final census is never reached on that branch. Previously attested probes receive special treatment; a first-seen zombie has no such proof. [S3] [S4] [S5]

**Consequence:** Short-lived processes can fall between observations. A valid safety refusal can strand a completed job because the implementation missed the evidence it needs. This does not prove the latest unknown PID was benign, foreign, reused, duplicated, or caused by the driver.

**Required:** Fix the correlated observation protocol and evidence capture, not just enumeration order or a timeout. Preserve the distinction between an invalid sample that may be re-observed while the same lease stays owned, and a contradictory/unsafe condition that requires refusal. Missing historical executable identity must stay missing. See W2 and D1.

### F02 — Unknown and duplicate GPU rows are conflated; the failure PID can be lost

**Evidence:** Source-confirmed and audit-reported. The same error covers multiple branches. `mark_phase('compute_apps')` resets service/PID before the unknown-row rejection, so `ollama_cleanup_failure.pid` can be null even when a bounded diagnostic event contains the GPU PID. [S3] [S5]

**Required:** Versioned, bounded reasons and observations: unknown PID, duplicate `(physical GPU UUID, PID)` row, birth change, executable mismatch, unavailable observation and stale attested row must be distinguishable. Preserve compatibility with historical coarse codes without inventing facts about them. Do not overwrite old incident records to improve their diagnosis.

### F03 — The ordinary cleanup loop and manual retirement coverage do not line up

**Evidence:** Source-confirmed. The manual `IdleRecovery.ERRORS` list accepts four process-related codes but excludes the current unknown/duplicate class. The automatic worker returns without same-boot Ollama retirement. Increasing the ordinary cleanup deadline cannot help a branch that immediately raises `RecoveryRequired`. [S3] [S6] [S7]

**Required:** A coverage table based on evidence and allowed effects, not an ever-growing list of error-string exceptions. Current-lease retirement needs a reviewed fresh-proof contract and new exact operator approval. A broader manual case is not automatic authority. Automatic retirement after a retained failure remains a separate decision, not a hidden side effect of improving observations.

### F04 — Check Recovery contends with the system's own worker

**Evidence:** Source-confirmed, with audit-reported real contention. Idle Check uses several zero-wait coordination locks, including `recovery.lock`; the recovery worker holds that lock while doing finalization/receipt work. The audit's 3/8 timeout result is a bounded reproduction, not a universal failure rate. [S6] [S7] [S8]

**Required:** Meaningful prerequisite/unsupported verdicts, bounded named busy responses, one end-to-end deadline, and a reviewed lock-order graph. A lock busy result is not an unsafe-GPU verdict. Do not add arbitrary sleeps, increase every timeout, or stop the worker to make Check succeed.

### F05 — Cleanup can pass initial verification and still enter an unhandled final-check failure

**Evidence:** Source-confirmed; not established as the latest incident's cause. `_work()` performs multiple `commit_check()` calls after `verify()`, including around publication and lease release. `commit_check()` converts pending turnover into `ollama_cleanup_no_longer_idle` without a settling loop. `_work()` clears `cleanup_stage` before those checks; earlier late failures can become generic `supervision_or_storage_failure`, while a failure after terminal publication can become `ownership_release_unconfirmed`. Detailed guard diagnostics may survive separately, but primary classification loses the cause. [S3] [S9]

**Required:** Include the whole verify → publish → final check → durable release sequence in the correction and tests. Preserve precise cause and uncertainty phase. A retry must be bounded re-observation of the same owned work, never re-inference, repeated unload, duplicate publication or replay. A final-check improvement must not create a gap between physical evidence and atomic ownership release.

### F06 — Media reconciliation accumulates settled history into a permanent budget failure

**Evidence:** Independently reproduced on the exact published `media_dispatch.py`. `_receipt()` writes separate reconciliation records, but the overridden `reconcile_runner_jobs()` does not use them to skip already-settled historical references and intentionally leaves original records unchanged. Every pass counts those references against a maximum of eight. A ninth historical reference blocks dispatch even when every referenced native job is terminal, released and inactive. Repeated scans do not make progress. A separate 5,000-file cap counts files before filtering by scope. [S10] [S11]

**Required:** A verified completion/progress mechanism for application reconciliation that preserves original records and genuine uncertainty. Completed receipts must stop consuming the active unresolved-work budget. Bound per-pass work without treating growing valid history as permanent failure. Validate receipt identity/content before trusting it; continue checking current native ownership. Test restart/crash recovery and a workload well above eight recovered historical jobs and 5,000 ordinary records. Merely raising the constants is not a fix.

This is a newly found latent defect. The audit does not show that the live system has already crossed either threshold. It is not the explanation for the currently reported retained GPU1 owner.

### F07 — HA can lose the operation it must Verify

**Evidence:** Independently reproduced on the exact published card. `recover()` stores the newly confirmed operation ID, but `refresh()` unconditionally replaces it with `status.attempt.id`. An older successful attempt returned during/after a lost mutation response can replace the new ID; Verify then targets the old operation. The original lost-response test always changes status to the new attempt, leaving this case uncovered. [S12] [S13]

**Required:** Separate the pending locally confirmed operation from historical server-display state. Pin the exact operation and binding through polling, delayed responses, transport loss and reload/reconnect handling. A stale or unrelated attempt may be displayed as history; it must not satisfy or replace pending verification. Match returned identity/effect/receipt before declaring recovery complete.

The card also permits another Recover action after transport uncertainty because `busy` is reset and no persistent uncertain-operation state disables fresh mutation. A synthetic always-eligible server reproduced a second POST. This proves a frontend retry-control gap, **not duplicate native lease retirement**; server protections remain independently necessary. The desired UI state is Verify-only until the pending attempt has a definitive, matching disposition.

### F08 — Audit source is not a deployable canonical baseline

**Evidence:** Source-confirmed. The normal roots/defaults remain separate from snapshots; several legitimate runtime SDK/helper variants coexist. The public HA `Bridge` snapshot contains `192.0.2.0/16` in an IP-network allowlist. Python rejects that network literal under the default strict parser. The independent expression check reproduced the exception. [S2] [S14]

**Required:** Recover original production bytes from the private manifest-backed captures where publication transformed code. Treat the malformed public literal as a snapshot/reconstruction issue unless private originals establish otherwise. Do not assert it broke deployed HA, and do not silently replace it with a guessed literal. Promote maintainable source into ordinary trees, with explicit mapping and tests for each semantic restoration. Public reports, placeholder paths and examples are not installer input.

### F09 — History-size work and nested snapshots need measured budgets

**Evidence:** Source-confirmed scaling concern; exact contribution to the current timeout is unresolved. Worker finalization traverses durable receipts, and idle proof snapshots enumerate jobs and hash result files. Whole-state/history reads also occur in control/status paths. The audit reports 1,797 jobs and earlier large-journal startup delays. This supports testing history-size sensitivity, not a claim that every timeout is caused by journal size. [S6] [S7] [S8] [S15]

**Required:** Measure lock hold times, check latency and scan progress with production-shaped histories and larger fixtures. Use validated incremental state/receipts where justified. Preserve crash recovery and complete historical integrity. No journal deletion, truncation or blind compaction.

### F10 — Deployment acceptance covered components more reliably than the complete installed path

**Evidence:** Audit-reported historical omissions of the mandatory gate, helper/Music binding, and coordination-lock provisioning; source variants and actual entrypoints are documented separately. Framework-dependent Media reporting tests were not initialized in the reported environment. [S2] [S5] [S16]

**Required:** One private release matrix covering runner, import launcher, host SDK, helper wheel, backend hooks/plugins, Media SDK/app, HA bridge/card and settings. Validate the actual `recovery_media:app` entrypoint and installed package paths. Production-equivalent framework tests must run in a disposable environment with dependencies installed, not be reclassified as optional successes.

### F11 — Cooperative client boundaries must be stated honestly

**Evidence:** Audit-reported direct Ollama clients remain present; current queues and connections cannot reconstruct whether one competed with the historical job. The meeting wrapper and Music adapter intentionally use shared admission instead of independent locks. [S1] [S5] [S17] [S18]

**Required:** Preserve these clients and the cooperative operating model. For a recovery window, document which existing mechanism excludes/drains relevant producers through the final authority call. Do not claim a cooperative permit is a host-enforced perimeter for bypass clients. Do not turn this issue into a permanent firewall or Gateway prerequisite. Any stronger assumption needed for an automatic policy is an explicit decision.

### F12 — The ToDo fix and Music limit are not demonstrated causes of this incident

**Evidence:** Audit-reported successful fresh November 11 ToDo formatting and later cache reuse, with the successful receipt still present in the later audit; current ToDo intent is not reported pending. Source separates standalone identity/cache from Wallpaper's retained-owner dispatch guard. No causal evidence implicates the accepted Music limit. [S5] [S10] [S19]

**Required:** Preserve ToDo UUID intent, terminal receipts, request/date validation and cache behavior. Fix F06 in Media dispatch without reopening the old ToDo design or clearing caches. Test cold restart, changed inputs, date rollover, invalid output and pending-outcome preservation. Do not claim the whole system reliable merely because formatting/cache succeeds once.

## 4. Decisions to freeze before candidate implementation

### D1 — Evidence sufficient for ordinary release versus manual retirement

Write a short contract table using the existing architecture:

| Case | Ordinary behavior | Retirement policy |
|---|---|---|
| Exact previously authenticated probe, same lifetime, positive exit, fresh stable idle | May finish the same owned cleanup under existing authority | No manual recovery should normally be needed |
| New process still observable with complete trusted identity | Discard contaminated sample; obtain complete fresh proof within a bounded budget | No inferred trust from names/argv alone |
| Unknown compute or first-seen zombie with no trusted pre-exit identity | Do not accept that sample as a benign probe or successful cleanup | Consider independently proven present quiescence under an expressly reviewed manual contract; retain historical uncertainty |
| Still-active unknown computation, identity contradiction, changed generation, driver fault, inconsistent durable state | Refuse and retain ownership | No automatic release; exact blocker/authorized containment path |
| Lost response after a mutation may have begun | Keep the same operation and current fences | Verify-only; no new mutation attempt |

Sequential polling cannot guarantee that it observes the executable of every arbitrarily short-lived process. A correction that merely polls faster or reverses query order does not close the first-observation gap. Choose a supported trusted-lifetime evidence mechanism already feasible in the installed environment, or explicitly preserve the remaining gap and use an independently reviewed retirement contract. Do not prescribe an unqualified privileged tracing subsystem just to satisfy the roadmap.

A fresh-proof manual retirement must prove its own safety; it does not retroactively prove the unknown process was harmless. The exact current job can remain failed/no-replay even if new work is later allowed. Unknown historical identity is a reason to preserve uncertainty, not automatically an eternal ban on a properly authorized future state transition.

**Authority boundary:** Ordinary bounded cleanup re-observation is not the same as automatic retirement of a retained failed lease. Keep automatic Ollama retirement disabled unless separately approved. If expected routine turnover still frequently needs manual intervention, report unattended acceptance as failed; do not hide that limitation behind a successful manual recovery.

### D2 — Safe deployment when the current lease needs new recovery support

Resolve the bootstrap sequence before an operator packet is issued. An installer that insists on owner-free state while only the new code can retire the existing lease creates a circular dependency.

Preferred review target: a narrowly justified CPU-runner handover with completed/inactive work, immutable preserved ownership/history, original lock identities, startup behavior proved against that retained state, and no new backend action or implicit recovery. A later normal HA confirmation then authorizes the exact supported retirement. This is a proposed deployment capability, not existing permission to restart.

If the existing installation already has an admissible exact-state recovery route, use it only with fresh evidence and the operator authorization that actually applies. Do not substitute a terminal command for required HA confirmation, re-run a historical installer, or clear ownership to satisfy an installation precondition. If neither route can be proved, report this exact blocker before production, not after partially installing.

### D3 — Publication and history remediation

Ordinary development branches should contain implementation, generic tests, examples and useful technical documentation. Production configurations, incident IDs, raw logs, deployment inventories, private paths/identities, receipts, member content and forensic archives remain private.

Preserve a verified private archive before removing any public material. Assess exposed material and reachable refs. Deleting a directory at the branch tip does not remove prior commits or other copies. History rewrite, force push, branch deletion, visibility changes and credential rotation each need an explicit plan/authorization where applicable. Do not promise erasure from existing clones. If live secrets are actually discovered, surface that immediately and prepare the appropriate rotation/removal action; do not invent a compromise. [E1]

### D4 — One bounded deadline and lock-order design

Inventory the existing HA bridge, Media proxy and runner deadlines and ticket lifetime. Design one total request budget with reserve below the shortest transport limit, including coordination waits, proof, final checks and response handling. Do not allocate a full timeout independently to every lock or repeated proof.

Read-only checks may retry bounded observations or return a named busy response. Mutation code must not automatically replay a submitted authority call. A preliminary unsupported/configuration answer may avoid expensive observation, but it cannot mint an eligible ticket without full fresh validation. Record the acquisition order, which locks are released before waiting, and how worker progress is preserved.

## 5. Work packages and dependency order

### W0 — Preserve private evidence and establish source authority

**Depends on:** pinned audit sources; access to existing private originals.  
**Allowed effects:** local files and approved new source branches only; read-only production verification when needed.

Create one private manifest of current source origins, original/published hashes, release/load paths and transformations. Preserve all existing branches and worktrees. Record the actual current incident/scheduling state once; do not replay the audit's historical recovery instructions.

Select a legitimate pre-audit source base for each repository. Do not merge or cherry-pick the umbrella audit commits into clean development history. Reconcile the approved source files into ordinary package/app directories, with a per-file record: unchanged, newly represented, restored from transformed snapshot, development-only, or intentionally deferred. Use the correct deployed feature branch for meeting-pipeline rather than assuming `master` is its baseline.

If original source embeds private settings, separate those settings through an explicit, tested configuration change rather than publishing them or substituting executable placeholders. Preserve the original semantics and deployment values privately.

Promote the root runner package and required HA/helper/backend integration sources into sensible existing source/test/deployment locations. Keep multiple deployed SDK generations explicitly mapped. Do not flatten unrelated wheels into one directory or force all consumers onto a new SDK merely to make version numbers match. Prefer tested package dependencies for future builds; changing consumers remains a reviewed compatibility action.

**Gate G0:** A fresh checkout can identify/build/test the intended source without importing `audit/`, `tmp/`, a local candidate tree or an undocumented wheel. Every runtime component has a source origin and any unrepresented bytes are explicit. Snapshot placeholders cannot enter a production build. Public branches contain no forensic dumps or private operational fixtures.

### W1 — Characterize the baseline and freeze the contract

**Depends on:** W0.  
Import suitable generic characterization cases from this packet into normal tests. Replace environment-dependent fixtures with synthetic equivalents without weakening assertions. Run the selected original suites and complete missing Media framework coverage in a disposable dependency-correct environment.

Add reproduction coverage for F05 as well as F06/F07. Establish which tests demonstrate a baseline defect, which are expected failures, and which are existing safety passes. Freeze D1–D4 and a source-to-runtime compatibility matrix before writing the correction. These are bounded design decisions, not a new architecture proposal.

**Gate G1:** Reviewable defect inventory, explicit authority changes, a real lock-order/deadline model, and a desired-outcome test matrix. No claim that existing passing safety tests establish recovery liveness.

### W2 — Correct observation, diagnostics and final release checks

**Depends on:** G1.  
**Primary modules:** `ollama_cleanup.py`, `ollama_probe.py`, `ollama_diagnostics.py`, narrowly justified `_work()` error/publication handling.

Collect correlated process/compute observations with a common deadline and bounded retention. Record enough timestamps and PID-specific facts to distinguish process appearance, disappearance, PID reuse, duplicate rows and metadata failure. Preserve executable, argv, parent birth, process birth, namespace, cgroup and container-generation checks when they are required for trust. A name, apparent command line, low VRAM, missing `/proc` entry or elapsed delay alone is insufficient.

When the evidence only invalidates the current sample, retain the same lease, reset stability, and re-observe within the existing bounded cleanup operation. Do not silently promote an unknown process to the trusted probe set. Verify positive exits and fresh accepted stable samples after eligible turnover. Unsafe contradictions remain refusals.

Exercise the same rules at every post-verification boundary. Keep precise failure phase/cause after publication and during release. Do not discard completion/output evidence merely because release is uncertain, and do not falsely mark a saved result as released.

If the exact pinned Ollama implementation is needed to understand helper lifetime, inspect that implementation in the existing disposable/private environment. Do not infer that `/api/ps` causes the churn, upgrade Ollama, or alter models without evidence. Record any third-party behavior assumption as such.

**Gate G2:** Real CPU-process tests plus controlled synthetic GPU-row tests cover all observation windows; full negative safety tests pass; no inference/unload/release replay; final-check races and timeout exhaustion give truthful outcomes. A missing first-observation proof remains explicitly covered rather than forgotten.

### W3 — Make existing recovery usable and exact

**Depends on:** G1; coordinate with W2 diagnostics.  
**Primary modules:** `idle_recovery.py`, `recovery_control.py`, `recovery.py`, narrowly justified API/status and native settlement interfaces.

Implement bounded, named coordination busy results and the reviewed lock order. Separate a supported/unsupported prerequisite decision from expensive proof where safely possible. Measure worker/Check contention using the actual lock implementation. Optimize broad history scans only when measurements justify it; preserve persistent record checks and restart reconstruction.

Implement the approved manual completed/inactive retirement contract, not merely another error string. Bind exact job, request, lease, boot, accepted scope/policy, original locks and container/service generations. Prove absence of competing supervised work, required queue/model state, present process state, driver health and stable physical idle under the agreed producer-exclusion model through the mutation. Include all relevant captured/settling/attested/rejected process evidence; `failure.pid == null` cannot silently turn into "nothing to verify." Legacy sparse evidence requires an explicit supported/refused path.

The effect is one failed/no-replay disposition for the selected lease, one durable audit, preserved unrelated history and no backend cleanup/restart/reset. Revalidation must happen at execution. Lost responses enter verification; they cannot trigger another authority invocation. Do not enable a new automatic recovery class as part of this package without D1 authorization.

**Gate G3:** Exact current-class eligibility can be meaningfully checked under a production-shaped configuration. Busy, unsupported, unsafe, missing evidence and unavailable observation are distinct. Successful/uncertain/failed-before-authority outcomes have deterministic verification behavior and preserve exactly-once mutation semantics.

### W4 — Repair HA pending-operation handling and Media historical reconciliation

**Depends on:** G1; integrate with W3 response contract.  
**Primary files:** HA card; `media_dispatch.py`; framework/status tests. ToDo formatting logic stays unchanged unless a failing test justifies a targeted change.

For HA, store pending confirmation identity separately from server history. A polling response for an older/different operation cannot overwrite it. Do not re-enable Recover after an uncertain POST simply because the request function returned. Verify addresses the pinned operation; matching definitive evidence is required before clearing the local pending state. Cover reload/reconnection and concurrent tabs with explicit server reconciliation or a safely retained non-secret operation reference; do not persist bearer credentials or reusable approval tokens in browser storage.

For Media, validate and consume reconciliation completion evidence without changing original operation history. Maintain crash-safe progress so an already-settled record is not repeatedly charged against a pending-work cap. Continue rejecting unresolved work and identity conflicts. A missing-job/404 receipt must retain its original no-submission proof rules; do not make historical 404s a permanent exemption from identity checks or new current ownership. Bounds limit one pass and return meaningful progress/busy state; they must not make all future work impossible merely because the archive grows.

Keep ToDo successful content cached under its exact input/date semantics, preserve pending intents and terminal receipts, and keep native ownership as a separate prerequisite for Wallpaper. No cache clearing, automatic new ID for an uncertain request, or fallback that bypasses a GPU fence.

**Gate G4:** Desired-outcome versions of every independent characterization pass. Nine and many more settled historical references admit new work without rereading all of them indefinitely. Genuine unsettled references still block. Stale HA status cannot change the Verify target, and uncertain mutations cannot initiate a second frontend recovery flow.

### W5 — Build and qualify one coherent candidate

**Depends on:** G0–G4.  
Build from committed canonical source in a clean environment. Create one private deployment manifest with code commits, artifact hashes, actual entrypoints/import origins, SDK/helper/plugin compatibility, effective settings/schema, existing lock prerequisites, rollback artifacts and schedule restoration requirements.

Separate source reconstruction from behavioral fixes in reviewable commits. Four repositories need maintainable source, but do not redeploy four applications if only the runner, Media reconciliation and HA card need runtime changes. Keep unchanged components byte-identical where practical. Source-only improvements to meeting/song repositories must not silently change their live SDKs or scheduling.

Run real disposable HA Core → actual Media entrypoint → runner control integration, including authentication, confirmation, stale tickets, normal/unsupported/busy responses, lost responses, historical status, exact Verify and restoration. Use real temporary journals/locks and CPU-only process tests; GPU observations can be simulated only where clearly labeled. Import/build tests must not accidentally use the old repository-root package or an audit fixture instead of the candidate wheel.

**Gate G5:** Independent cross-service/authority review accepts the complete correction and rollback/retained-state handover. No unresolved test-initialization failure is counted as a pass. One manifest describes the candidate actually to be installed; no late unreviewed source changes.

### W6 — One attended production procedure

**Depends on:** G5 and fresh operator authorization for the bounded procedure.  
Record original schedules, host path states, loaded sources, current incident, ownership and backup state. Apply only the temporary exclusions required by the approved contract. Preserve legacy-disabled controls as disabled. Use the D2 handover/recovery sequence; do not improvise an owner-clear workaround.

Use interactive sudo for privileged steps. Verify the loaded code/settings/prerequisites after installation. Obtain a fresh HA Check for the actual current incident and have the operator confirm its exact effect once. Record the operation ID before the mutation can become uncertain. If any response is uncertain, use only that operation's Verify route. Do not press Emergency to bypass an unsupported idle contract.

Verify one matching audit, one exact lease disposition, no replay and unchanged unrelated jobs/results/locks. Let existing Media reconciliation settle application records correctly; do not edit receipts to make status green. Restore original scheduling states after completion or abort through existing gates; never reopen an unresolved recovery fence merely to make the schedule/status display appear normal. Any required continuing containment must be explicitly reported and handled under the approved abort procedure, not silently left behind.

**Gate G6:** Installation, exact recovery outcome, scheduling restoration and application reconciliation are independently reported. Restoration is not yet soak acceptance.

### W7 — Ordinary-cycle and unattended acceptance

**Depends on:** G6.  
Observe normal eligible schedule cycles rather than forcing production failures. Respect existing presence/time conditions. Require successful full Wallpaper generation with cleanup/release; both fresh and unchanged-input cached ToDo behavior; correct quote/cache classification; normal Music behavior and preserved meeting/lesson integration. Delivery remains in its pre-existing state.

Track five consecutive eligible production days using a small private ledger: expected/observed invocations, output result, owned resources, cleanup/settlement, recovery activity, application reconciliation, source versions and any operational exception. Include enough ordinary workload diversity to exercise the corrected paths; record absent workloads honestly rather than manufacturing production work to fill a table.

A retained job needing routine manual rescue, duplicate execution, wrong-operation Verify, lost ownership, false success, recurring false attribution refusal or historical-budget dead end fails unattended acceptance. Record outside cooperative-client interference separately and classify it on evidence: do not blame a direct client without proof, and do not call permanent retention acceptable because the client was external. Genuine unsafe conditions may correctly require an operator; their frequency and handling remain visible.

**Gate G7:** Five eligible production days meet the recorded operational criteria with no hidden repair/reset/state deletion between samples. Gateway work remains deferred until this gate and existing operator milestones are explicitly accepted.

## 6. Required qualification matrix

These are minimum scenario families; test names/counts must reflect unique scenarios, not inherited fixture executions presented as new coverage.

| ID | Scenario | Required result |
|---|---|---|
| Q-O1 | Existing attested probe exits normally between censuses | Same-lease cleanup converges; no new inference |
| Q-O2 | New fully observable probe appears between census and GPU query | Contaminated sample discarded; complete trusted correlation required |
| Q-O3 | First observation already zombie, allowed executable versus unrelated executable | No name-based trust; explicit gap/manual disposition, never false normal success |
| Q-O4 | Unknown leased-GPU PID never appears in a usable census | PID/timing/reason preserved; no invented ancestry or harmlessness |
| Q-O5 | Duplicate compute row, stale attested row, PID birth reuse | Distinct truthful classifications; contradiction remains refused |
| Q-O6 | Executable/argv/parent/cgroup/namespace/container/image mismatch | Refusal preserved, including spoofed names/arguments |
| Q-O7 | Metadata permission failure, missing process, malformed output, parser truncation | Observation unavailable versus positive exit remain distinct |
| Q-O8 | Valid turnover during each post-verify/publication/release check | No replay; precise cause; bounded handling; no false released success |
| Q-O9 | GPU/driver health fault, excess residency, foreign GPU activity | Existing safety refusal preserved |
| Q-O10 | Cleanup deadline expires | Bounded outcome; exact stage and ownership retained where required |
| Q-R1 | Automatic worker holds recovery coordination lock | Named bounded busy response or completed Check; no deadlock |
| Q-R2 | Concurrent Check/Check, Check/status, Check/mutation, worker/caller | Consistent lock ordering, bounded progress, no duplicate authority |
| Q-R3 | Missing mandatory gate/helper/coordination file or changed scope | Specific refusal before mutation; ordinary unrelated service behavior preserved |
| Q-R4 | Approved completed/inactive exact lease under fresh proof | One failed/no-replay disposition and audit; only authorized ownership released |
| Q-R5 | Unknown active compute, competing work, changed binding or replaced lock | Refusal; no cleanup/kill/reset side effect |
| Q-R6 | Crash before authority intent, after intent, after native commit, before response | Exact durable verification; no second authority call |
| Q-R7 | Retained-state candidate startup/handover | No implicit owner release/replay; original state and locks remain authoritative |
| Q-R8 | Growing job/result/receipt history | Measured bounded checks and worker progress without archive deletion |
| Q-H1 | Lost POST response and older successful status | Pending operation cannot be replaced; Verify exact new operation |
| Q-H2 | Status arrives while POST is in flight | Same binding retained regardless of response order |
| Q-H3 | Uncertain response followed by another Recover click | No fresh mutation; Verify-only until matching definitive disposition |
| Q-H4 | Reload/reconnect, simultaneous card/tabs, token expiry | Recover exact pending context safely; no historical success substitution |
| Q-H5 | Matching completed/aborted/uncertain response | UI reflects actual effect; no candidate/eligibility/recovery conflation |
| Q-M1 | Nine-plus historical references with valid terminal receipts | No permanent eight-reference blocker; current readiness still checked |
| Q-M2 | Above 5,000 ordinary records, old scopes and restart | Bounded progress; no unconditional archive-size outage |
| Q-M3 | Receipt tampering, identity mismatch, changed record, unresolved native state | Refuse; never treat arbitrary receipt existence as authority |
| Q-M4 | Crash between receipt write and progress/pointer update | Resume deterministically with originals preserved |
| Q-M5 | Never-submitted/404 reconciliation | Preserve original bounded absence proof; no silent late submit/replay |
| Q-T1 | Fresh ToDo, unchanged input cache, cold app restart | Exact valid output; no unnecessary repeated formatting |
| Q-T2 | Midnight/date rollover and changed task payload | Fresh identity only when prior operation resolved; no stale-date success |
| Q-T3 | Pending intent and changed input, invalid model response, failed terminal receipt | Honest pending/invalid/failed classifications; no deletion or duplicate submit |
| Q-I1 | Actual HA → Media → runner route with production-shaped config | All supported actions and credentials/roles work; refusal meanings preserved |
| Q-I2 | Candidate wheel/import path, helper/plugin/host SDK compatibility | Tested actual bytes, not interpreter name/image tag assumptions |
| Q-I3 | Redacted executable constants/path fixtures | Restore verified semantics; no placeholder enters runtime package |
| Q-I4 | Music/Wallpaper/WhisperX/meeting/lesson/client compatibility | Existing behavior and sole runner ownership preserved |
| Q-I5 | Schedule/host path restoration, complete abort path | Original state restored without overriding unresolved safety gates |
| Q-L1 | Natural fresh formatting, cache hit and full Wallpaper cycle | Separate live acceptance evidence, correct output and released ownership |
| Q-L2 | Five eligible production days | No routine manual rescue or hidden intervention; failures remain visible |

## 7. Repository layout and release discipline

Use existing ordinary source layouts: the runner's package/tests, Media's app/tests, meeting bin/controller/tests, and the song application's existing layout. Generic HA integration/card, helper and backend-hook source should have named maintained homes in the appropriate existing repository; the runner is the default home for runner-specific integrations unless current project structure already defines one. Do not introduce a fifth repository merely for this cleanup.

Keep one canonical `roadmap.md` in the runner repository. Companion READMEs point to its accepted revision; they do not carry diverging roadmaps. Integrate this document while preserving the old roadmap's nonconflicting product constraints and deferred milestones. Old October deployment statuses must be clearly superseded, not repeated as current facts.

The next development branches should be based on clean, explicitly identified source history, not the audit branch. Keep the audit baseline privately recoverable and document source provenance. Committing a later deletion of `audit/` is not equivalent to preventing its history from entering a clean branch.

Use the normal Git-based path: **Codex → reviewed commit → push → operator pull/deploy**. Do not return to editing production files that never reach the source repository. Public CI tests use synthetic identities and configuration. Private deployment manifests select actual paths, credentials, GPU/container identifiers and scope without placing them in public templates.

Normal publish/merge decisions, public-audit history removal, runtime deployment and exact recovery are distinct approvals. A passed code review does not authorize all four.

## 8. Anti-drift controls and stop rules

G0–G5 are internal checkpoints within one implementation assignment, not six separate operator handoffs. Complete independent read-only/offline work without repeatedly returning individual diagnostic commands. Consolidate genuinely blocked policy decisions; return one candidate for review and one eventual attended procedure.

Every proposed change must reference a finding/requirement ID, the failing baseline test or source evidence, its intended effect and its non-effects. Prefer a small coherent patch set over a succession of production hotfixes; do not promise that the number of changed lines will be one.

Maintain one progress table in this roadmap. Put raw evidence and per-host approvals in the private packet, not a growing family of public reports. New adjacent issues may be recorded, but they are not automatic scope expansion. A newly discovered blocker must state which gate it prevents and the narrow evidence needed to resolve it.

Stop production progression for missing original bytes, unreviewed authority expansion, unresolved lock-order/deadline hazards, unverified current lease/bindings, test-import failures on required paths, ambiguous completed mutation, failed restoration or an unreviewed late package change. Continue safe offline work on independent gates where possible; do not use a blocked production gate as a reason to redesign everything.

Do not claim closure when only a code count, unit-test count, UI label, one recovery, or one ordinary cycle has passed. Report the gate actually passed and the one still blocked.

## 9. Progress ledger — initial state

| Gate | Status | Required evidence |
|---|---|---|
| G0 Canonical source/private evidence | PASS, offline source/build scope | Clean ancestry, original-to-canonical private map, separate committed builds and verified installed imports; no audit ancestry |
| G1 Contracts and failing baseline scenarios | PASS | D1–D4 frozen and independently reviewed; baseline reproductions separated from desired-outcome tests; proposed authority explicitly disabled |
| G2 Cleanup observation/release correction | PASS, qualified CPU/synthetic observation envelope | Correlated observation, real process/parser negatives, common cleanup budget and final boundary tests; no historical PID authentication or live acceptance |
| G3 Exact usable recovery | PARTIAL / authority decision remains | Existing supported class qualifies; exact unsupported class returns meaningful refusal. Missing historical identity is not newly authorized for retirement |
| G4 HA/Media liveness corrections | PASS, Linux deployment envelope | Settled-history progress and tamper/current-owner refusal; actual browser/HA/Media/native one-call uncertain Verify; Windows large-history latency limit recorded |
| G5 Coherent release review | HOLD | Source accepted by authority review; committed artifacts and installed integrations qualify. New historical-evidence authority and configured privileged helper/container qualification remain unresolved |
| G6 Attended restoration | Not authorized/executed by this roadmap | Fresh state, exact operator approval, audit/release/restoration proof |
| G7 Unattended acceptance | Not established | Five eligible production days and workload ledger |

## 10. Sources and provenance

Source links refer to immutable audit commits. They are review references, not deployment instructions. Function names identify the relevant portion where a file is large. Private historical originals remain necessary wherever the manifest records a transformation.

[S1]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/ARCHITECTURE.md
[S2]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/SOURCE-DIFF.md
[S3]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/ollama_cleanup.py
[S4]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/ollama_probe.py
[S5]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/FAILURE-ANALYSIS.md
[S6]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/idle_recovery.py
[S7]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/recovery.py
[S8]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/recovery_control.py
[S9]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/core.py
[S10]: https://github.com/bojinda/dashboard-generator/blob/3a05a58fc780c9be84f8afe3819a73941e23643d/audit/2026-11-11-production-reliability/snapshots/deployed/app/media_dispatch.py
[S11]: https://github.com/bojinda/dashboard-generator/blob/3a05a58fc780c9be84f8afe3819a73941e23643d/audit/2026-11-11-production-reliability/snapshots/sdk/aihub_gpu_runner/client.py
[S12]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/home-assistant/www/aihub-recovery-card.js
[S13]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/qualification/tests/test_idle_card.mjs
[S14]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/home-assistant/custom_components/aihub_recovery/bridge.py
[S15]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/snapshots/current/aihub_gpu_runner/native_settlement.py
[S16]: https://github.com/bojinda/aihub-gpu-runner/blob/30c14171bfc7544ff7cee6d1be4007233561b514/audit/2026-11-11-production-reliability/TEST-RESULTS.md
[S17]: https://github.com/bojinda/meeting-pipeline/blob/98da9238f543f823dd1a64acfc9e184f9e3aa6c6/audit/2026-11-11-production-reliability/snapshots/deployed/bin/with-gpu-lock.sh
[S18]: https://github.com/bojinda/daily-wakeup-song/blob/56a90a5db0d6e78475a6f606f72076d99f796396/audit/2026-11-11-production-reliability/snapshots/deployed/gpu_runner.py
[S19]: https://github.com/bojinda/dashboard-generator/blob/3a05a58fc780c9be84f8afe3819a73941e23643d/audit/2026-11-11-production-reliability/snapshots/deployed/app/standalone_text.py
[E1]: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository

External reference E1 is used only for GitHub publication/history-removal guidance. Runtime findings above derive from the pinned project sources, audit reports and explicitly described local reproductions.


# Preserved long-term requirements

These accepted pre-audit requirements remain deferred. Their historical milestone labels do not replace the G0–G7 reliability gates above. No implementation or deployment follows from this section.

## Deferred gateway milestone â€” Interactive gateway, after coexistence and manual-upload confirmation

**Objective:** Bring interactive clients into the same coordinator without modifying their source code or replacing the automation job API.

Add an Ollama-compatible interface in front of the existing resource manager. Inventory the actual calls used by the installed clients before fixing the endpoint scope. Preserve streaming/non-streaming response formats, errors, request cancellation, model options, and any tool/image payloads actually used. Native Ollama streaming uses newline-delimited JSON; a custom job-status payload is not a compatible replacement. [4]

Do not expose unrestricted model-management operations simply because an API path exists. Bound request size, context, wait time, and concurrency; use authenticated clients and allowlisted model/backend policies. Metadata reads should not reserve a GPU.

**Client migration order: one at a time, each with rollback.**

| Client | Verification scope |
|---|---|
| Open WebUI | Chat, streaming/cancellation, model discovery, and separate local embedding or background-generation connections if configured. [5] |
| AnythingLLM | Ollama chat plus its independently configured embedding path if it uses AI-HUB; check background document indexing. [6] |
| Continue in VS Code | Audit each configured local model role: chat, edit/apply, autocomplete, and embeddings where present. Point only AI-HUB-backed roles at the gateway; do not assume all roles use one endpoint. [7] |

Continue autocomplete needs a short waiting policy and cancellation of obsolete requests. Background generation may wait longer. Interactive priority is a later explicit policy, not a promise to interrupt an active GPU task. Retain warm models only when safe for the next admitted workload; an open chat session does not own the GPU indefinitely.

On disconnect, cancel queued work where appropriate. Once backend inference has begun, resolve its state before handing resources to an incompatible request. Preserve existing client timeouts or document deliberate changes.

**Gateway acceptance:** All configured local generation/embedding paths use the coordinator; streaming and cancellations behave correctly; meeting, wallpaper, and music regression tests still pass. Restrict direct backend access only after migrated paths and administrative recovery access are verified. Do not call the system globally coordinated while bypass paths remain active.

## Deferred advanced coordination â€” Later advanced GPU coordination

Core dual-GPU meeting sessions are now R2-D, before gateway work, and must become
stable with the normal meeting/wallpaper/music workflows under separately approved
live tests. They are not an optional feature deferred until gateway stabilization.

Other advanced policies remain later and separately scoped. After the gateway and
interactive migrations stabilize, record acceptance of any approved advanced work
without changing the accepted core session/admission/recovery behavior. Do not
reopen experimental whole-meeting extraction or expand this checkpoint.

## Deferred draft-only website upload â€” Automatic website draft upload, final phase only

**Deferred final phase:** Begin only after the GPU runner, working meeting/wallpaper/music pipeline, Ollama gateway and Open WebUI/AnythingLLM/Continue migration, and advanced GPU coordination have been proven stable and accepted by the operator. Core R2-D dual-GPU meeting operation must also have accepted live stability.

Future automation must upload **drafts for operator review only** and must **never automatically publish**. The operator retains review, correction, redaction, and publication control.

Do not design, implement, gather website integration requirements, request credentials/access, or perform website verification for W1 now. Its details and validation scope require a separate authorization when this final phase is reached. Earlier manual-upload confirmation authorizes gateway progression, not website automation.



# Candidate implementation ledger

See [reliability contracts](docs/reliability-contracts.md), [finding matrix](docs/reliability-candidate.md) and [component provenance](docs/source-reconstruction.md). Offline qualification is separate from installation, exact recovery authorization and five eligible production days. Unknown-history manual retirement is disabled; no new automatic authority is included.


## Remaining qualification checkpoints — explicit review stops

The four published implementation commits are frozen. Offline implementation and
its documented CPU/synthetic tests are complete for the accepted source scope;
this does not complete real-environment qualification or production acceptance.
G0/G1/G2/G4 retain their qualified offline verdicts. G3 remains partial for the
unsupported historical-observation class; G5 remains HOLD. No test count closes
either gate, and the roadmap is not complete.

| Checkpoint | Required evidence | Stop for review before |
|---|---|---|
| H1a: isolated host/procedure approval | Explicit disposable Docker-capable Linux host, CPU-only image digest, no production state/devices/mounts, exact original launcher/wheel and synthetic root-only configuration, bounded case matrix | Creating or operating the container fixture on that host |
| H1b: configured helper execution | Actual no-argument launcher at fixed paths, real container-init/cgroup/namespace/birth, valid capture/recovery-capture/graceful perform and wrong-generation/binding/action refusals, exact effect counts and interruption evidence | Advancing to authority or deployment decisions; unsupported host isolation returns NOT EXECUTED |
| H2: manual contract decision | Existing disabled proposal plus a concrete reviewed exclusion of cooperative and direct/unmanaged producers throughout commit, and explicit treatment of missing historical identity | Implementing or enabling new authority; no coarse error allowlist substitution |
| H3: coherent candidate release review | H1 evidence, H2 decision, exact immutable artifact/settings compatibility, retained-state handover/abort plan; any necessary code revision requires a newly reviewed successor rather than edits to frozen commits | Issuing an attended production GO |
| H4: exact attended deployment/recovery approval | Fresh current binding/exclusion checks, recorded original ingress states, exact HA effect/operation, Verify-only uncertainty, rollback boundaries | Any production mutation; recovery is a separately explicit HA confirmation |
| H5: ordinary scheduled acceptance | Actual Wallpaper/ToDo/cache and other required workload artifacts, durable releases and five eligible production days | Declaring unattended production reliability accepted |

H1 is currently NOT EXECUTED: the available development environment has no local
Docker engine/client, and no remote fixture host is authorized. Prior helper import
and argument-refusal checks remain valid limited evidence; direct function tests
and synthetic rows do not qualify configured privileged capture/perform. The fixed
production helper configuration must never be repointed to make the fixture work.

Only checkpoint documentation advances here. Preserve the coherent implementation,
existing long-term requirements and original private evidence. Each remaining stop
requires review; authorization for one checkpoint does not authorize its successor.


## System-wide E-stop amendment

# AI-HUB — System-wide E-stop Roadmap Addendum

**Revision:** E0, 2026-11-11 (Toronto).  **Status:** design/implementation checkpoint, NOT deployed or production-authorized.  **Canonical merge target:** `bojinda/aihub-gpu-runner/roadmap.md` on the existing local roadmap branch; merge this as a narrowly scoped amendment rather than replacing the later canonical roadmap with an older copy.

## Operator decision (supersedes previous emergency-release requirements)

E-stop is an **administrator-triggered, global force-relinquishment of the runner's coordination ownership of GPU0 and GPU1**. It applies to whatever is currently using either GPU, including ordinary Ollama, on-demand Wallpaper/Comfy, Music/Comfy, WhisperX host stages, routine meeting stages, and paired/dual-GPU meeting sessions. It applies during waiting, running, cleanup_pending, reconciling, and other retained conditions; it does not require historical process identity, physical idleness, empty VRAM/model residency, successful cleanup, or a recognised error class.

E-stop **does not stop, pause, restart, unload, signal, re-execute, or reset** any GPU workload or device. The operator explicitly accepts that old work may continue and new work may overlap it. The action releases the **runner's authority/leases**, not hardware memory or an executing process. Workload termination is deferred to a later, separately designed feature.

**Required user-visible result:** E-stop invalidates all current GPU lease bindings and returns current runner admission to a logically free generation without waiting for GPU work/cleanup. A historical active process that still holds the old generation's OS `flock` must not make the **new generation** falsely appear blocked as a retained logical lease. New work may still independently fail a backend's ordinary execution/preflight limits; report that honestly instead of reporting hardware as idle. Do not label physical GPU work as stopped.

## Technical reality and boundary

- Linux `flock` is associated with the lock-holder's open file description. A separate administrator request cannot simply `LOCK_UN` an arbitrary live worker's descriptor. Editing `ownership.json` alone will **not** make the old file's `flock` available while that process holds it.
- The existing `Admission.try_acquire()` requires BOTH `state.owners` free and successful nonblocking `NativeLock.acquire()` of the currently configured physical lock paths. Independently launched `HostStage` jobs take those same locks. Therefore, **an E-stop implementation that only clears owners is incomplete**, even if its UI says success.
- To meet the operator's explicit semantics without stopping work, use a correctly designed, **crash-safe admission generation / lock namespace transition**, or demonstrate an equally sound mechanism that gives new cooperative work new lock authority while old holders can continue. Preserve all old lock files/inodes as evidence. Do **not** unlink or replace old held locks as an undocumented workaround. Avoid a second scheduler or a separate shadow registry.
- The old/legacy launchers and client packages must be inventoried. New normal admissions (runner workers and host/meeting stage clients) must use the current generation. Older binaries that cannot understand the new generation must **fail closed** and must not claim old generations for new work. If the active host SDK requires a compatible update, include that in the candidate and deployment plan; do not pretend that the earlier three-component manifest already covers this change.

## E-stop contract (one global action first)

1. **Authority:** authenticated active HA administrator, separate operate credential, direct user confirmation with clear warning that GPU work may continue and overlap; dedicated E-stop operation/ticket, not a fabricated existing `Recover` or Wallpaper emergency proof. No anonymous, automation, client or GET-triggered mutation. A fixed backend action may be added through the HA bridge and Media proxy allowlists; never expose arbitrary paths/commands.
2. **Exact invocation:** fresh read-only status/check binds current admission generation, boot ID, scope, policy, both original current resource lock identities, all current owners (possibly none), each job/request/hash/lease/resources, and action. The token is short-lived and one-shot. **Global E-stop clears every currently held resource lease**, including both resources of a paired owner; no partial dual-lease remainder.
3. **Before commit:** acquire bounded coordination mutexes, validate the exact ticket against current state, persist a durable operation/intent and per-lease disposition, and prepare any required *new generation* lock paths and identities. One current generation is authoritative. No backend, GPU or Docker reads/writes are required to establish administrative eligibility. A busy or unavailable journal is reported truthfully; no claim of hard real-time latency.
4. **Commit:** atomically switch the authoritative journal to the new admission generation and revoke/retire every old lease. Persist an append-only audit/receipt containing old owners, new generation, action, confirmed actor, signed binding, no-replay status, and the fact that **physical execution was NOT inspected or stopped**. Follow crash-consistent ordering: precreate/fsync new lock identities, then commit one authoritative generation switch; interrupted preparations are not authority.
5. **After commit:** API reporting and scheduling use the new generation; old owner's job is failed/administratively abandoned for runner authority, with its original incident, outputs, requests and history retained. **A revoked old job must never become an accepted success or a new owner through late worker writes.** Use an immutable revocation ledger or equivalent authoritative overlay. A stale worker's `_modify`, `Lease.release`, journal write, host `generate`, result publication, or handoff must not mutate fresh ownership/terminal disposition. Old worker processes are left running (at most their later cooperative writes fail). Preserve physical lock files from each generation.
6. **Idempotence:** one administrator call/one audit for the ticket. After lost HTTP response, verify the exact operation only; no automatic resubmit or using an old success as evidence. Fresh deliberate E-stop after a completed/verified prior action may be separately authorised. Superseded by the accepted E1 decision: every separately confirmed E-stop selects a fresh generation, even with zero recorded owners and old held OS locks; report zero revoked leases truthfully. Status and Verify do not rotate.
7. **Status:** display `runner ownership: FREE / revoked leases: N / current generation: …` separately from `GPU physical state: NOT ASSERTED`. If new admission fails due to a *distinct* readiness/VRAM/backend limitation, report that independently. Do not claim the OS kernel unlocked an old FD.

## Important integration points

- **Runner:** `aihub_gpu_runner/core.py` (`Admission`, `NativeLock`, `Lease`, `Runner._work`, `_schedule`, `result`, `status`), recovery/maintenance startup and readback, per-generation lock selection, stale-worker precedence.
- **Host work:** `aihub_gpu_runner/host_stage.py`, `HostOllamaClient`, meeting/lesson direct host SDK and guarded launchers; prove in-flight dual, gpu0 and gpu1 stage paths cannot overwrite new generation. Older installed versions must fail closed.
- **HA path:** `aihub_gpu_runner/integrated_runtime.py`, `dashboard-generator/app/recovery_routes.py`, `home_assistant/custom_components/aihub_recovery/bridge.py`, `home_assistant/www/aihub-recovery-card.js`. Add one explicit E-stop control and exact Verify; keep existing Recover and Wallpaper-specific emergency behavior unchanged unless independently justified and clearly migrated.
- **Other clients:** Music and Wallpaper requests remain normally admitted by runner; do not change Comfy inference, model, 475-MiB Music threshold, five-second budget, ToDo four-file cache/identity records, existing schedules, or Aurora Gateway.

## Acceptance matrix — offline first

- Running Ollama holds GPU1 physical `flock`: E-stop succeeds, old process persists, old lock remains held, **new generation GPU1 admission succeeds under a disposable test**, late old writes cannot affect new owner.
- Running Music holds GPU0: same test, including retained Music cleanup policy unchanged.
- Wallpaper paired GPU0+GPU1: atomically revoke both; no partial stale paired owner, no Docker stop; both new-generation resources can be admitted.
- Foreground meeting/WhisperX `HostStage` holds GPU0, routine meeting GPU1, dual meeting holds both: independent host subprocess remains alive and original FD remains held; new-generation admission works; host-side late updates fail closed/are masked by authoritative revocation.
- Jobs in waiting, running, cleanup_pending, reconciling, previously completed retained, and already free: correct outcomes; no duplicate work; no automatic backend execution from E-stop.
- Concurrent new admission during E-stop, stale ticket/binding, multiple tabs, lost response, duplicate POST, DB/IO failure before/after journal switch, host restart, old runner restart, and backend still busy: no false fresh ownership, no split generations, exactly one audit, Verify-only ambiguity.
- File-system crash windows between new file creation, directory fsync and authoritative commit; old and current lock inode identities; no unlink/rename of original held locks; operator-visible blocked state if state is uncertain.
- Actual installed Linux `flock` tests in separate processes. Synthetic mocks alone are insufficient for the active-held-lock contract. No live GPU workload required for qualification.

## Staged checkpoints — stop after each

**E0 — Contract and feasibility review.** Trace current and legacy host launchers and available journal/file-lock mechanics. Propose exact persistent schema, generation switch, compatibility/read-path, and crash proof. If a truly immediate usable lock generation cannot be obtained without an unapproved major migration, **STOP and report the smallest required change**; do not substitute logical-only release or hide the limitation.

**E1 — Offline candidate.** Implement E-stop only in source branches, using the approved E0 design. Use disposable processes and state directories; no production writes. Include deterministic tests and exact build hashes. Review here before deployment.

**E2 — Attended in-place installation.** Only after E1 and fresh state/provenance review, deploy exact minimal components. Running CPU runner and host SDK compatibility need qualification. No automatic estop or background force-release. Stop after installed readback and before any production E-stop.

**E3 — Operator confirms E-stop.** Fresh exact HA check/confirmation for current GPU0+GPU1 leases, one commit, Verify on uncertain response. Inspect authoritative journal/old physical locks/new generation and new admission without inferring physical idle. Restore recorded schedules, observe ordinary jobs.

**E4 — Stability.** Five eligible production days of ordinary Music/Wallpaper/ToDo/meeting work without coordination incidents. Separately evaluate later workload-stop behaviour. Do not let emergency override mask recurring ordinary cleanup failures in stability accounting.

## Non-goals / risk acceptance

- The operator **accepts unverified active GPU overlap** after explicit E-stop; risks include out-of-memory errors, failed jobs and competing compute. This is not automatic policy or a claim that continuing foreign work is physically harmless.
- No automatic physical stop, kill, driver reset, container restart, VM provisioning, manual cleanup proof, reconstructed historical PID, or blind journal deletion.
- No broad evidence recapture, wholesale refactor, public forensic dump, or history rewrite.


### E0 implementation ledger

The final operator E-stop decision supersedes the earlier idle-only/helper/producer-exclusion prerequisites ONLY for this new administrative action. It does not silently change ordinary Recover or enable automatic authority. The four reliability candidate commits stay frozen.

Current E0: OS-lock generation feasibility demonstrated offline; full-system design remains OPEN for revocation read/write precedence, executor handover and generation-aware host/helper/Music compatibility. See [E0 design and matrix](docs/estop-e0-design.md). The experiment is not an enabled E-stop and no runtime implementation has changed. Stop for the E0 review required above before calling the design sound or proceeding to E1. E2/E3 remain unauthorized; production readiness stays HOLD.


## Accepted E0 decision and E1 checkpoint

E0 generation feasibility at commit 5b31254ad8d8aa1714ab937d905d39e18240fb24 is accepted. The operator’s global administrative semantics are final; no idle, cleanup, historical-process or producer-exclusion condition is introduced for E-stop. Each separately confirmed operation must rotate to fresh authority even when owners are empty. Earlier no-op wording is superseded.

E1 is STOPPED at the explicit legacy-writer invariant. The worker-capacity component is implemented with atomic ThreadPoolExecutor replacement, nonblocking retirement, queued-task cancellation, retained old references and stale-context suppression; eight component tests and the ordinary 828-execution suite pass. A separate desired authority invariant FAILS: actual legacy AtomicStore can replace a committed synthetic generation/audit with cached schema1 bytes. This failure is not repair acceptance. See [E1 blocker and scope](docs/estop-e1-blocker.md).

Full E1, dedicated HA E-stop actions, authoritative revocation/read overlays, compatible host/helper/Music protocol, crash-consistent switch and release package remain incomplete. No installable system-wide E-stop is claimed. Review the required authority write fence before continuing E1. E2/E3 remain unauthorized and production readiness remains HOLD. The four published reliability candidates stay frozen; ordinary Recover and accepted Music 475 MiB/five seconds and ToDo records remain unchanged.


## Current E1 strategy — simplicity and coordinated upgrade supersede live migration

The operator accepted stopping and upgrading every native ownership writer and generation-dependent component together. Preserve the frozen reliability baselines and accepted E0 mechanism. The earlier E1 legacy-writer STOP was conditional on mixed-version survival; it is superseded by a verified all-stopped/all-updated installation contract. Do not build tombstones, privileged directories, successor journals or obsolete-runtime compatibility.

Follow AGENTS.md: use the simplest correct existing mechanism, challenge unnecessary roadmap complexity, and test actual failures and essential invariants. The existing ownership.json is the sole authority, with revision/generation validation, immutable E-stop dispositions and per-generation lock files. Retain the existing scheduler and approved executor rotation. Each explicit confirmation rotates even with zero owners; no automatic rotation on status or Verify, and no GPU/backend stop from E-stop.

See [E1 design and boundaries](docs/estop-e1.md). Current same-version tests cover stale raw writes, owner restoration, history/result overlays, running native host stages and lock holders, owner-free repetition, concurrency, lost responses and crash windows. Actual HA/Media/browser and installed-artifact qualification are recorded in the private candidate manifest. Offline counts do not complete this roadmap or establish production reliability.

E1 returns one coherent candidate for independent review. E2 remains an attended coordinated shutdown/install/readback, E3 a separately confirmed live E-stop, E4 five eligible production days. No production shutdown, deployment, journal activation or E-stop is authorized by this offline checkpoint. The unrelated privileged helper capture/perform qualification and unknown-history ordinary Recover contract do not gate administrative E-stop. Music 475 MiB/five seconds, ToDo data, original locks, historical jobs/results/audits and ordinary recovery protections remain preserved.
