# Findings and regression analysis

## Confirmed findings

1. **Observation coverage is narrower than ordinary process turnover.** The cleanup census precedes a separate nvidia-smi compute query. New PIDs appearing only in that later query are immediately refused. The guard cannot authenticate a process it never observed. This safety refusal can indefinitely retain a completed GPU1 job when no manual class supports the error. Unknown/duplicate are collapsed into one code, and this branch loses PID-specific metadata in its failure record.
2. **A separate first-observation gap remains.** The preceding01:01 incident first observed a defunct child with unavailable executable metadata. Prior disposable tests demonstrated that both an allowed executable and unrelated executable can produce the same zombie comm/argv representation. Its identity cannot safely be inferred. The installed exit correction covers a previously attested probe transitioning to exit; it does not cover missing initial proof. Current08:01 evidence does not establish the same transition.
3. **Read-only recovery checks contend with the automatic worker.** Zero-wait coordination locks turn ordinary scanner activity into local_lock_timeout. In the bounded current series3/8 checks failed this way; recovery.lock appeared in214/600 passive samples (35.7%, including successful check ownership). This is not a universal failure rate or pure worker duty-cycle benchmark. No physical lock replacement or persistent external lock holder was found. A check also holds locks and can contend with another check/worker; the audit did not flood endpoints.
4. **Source/deployment drift impaired review.** Thirty current runner module blobs were missing from fetched remote objects; Media had seven missing app blobs and four untracked recovery/dispatch/intent modules in production. Multiple legitimate SDK generations coexist. The default branch, interpreter prefix and Docker image tag each fail to identify the loaded code by themselves.
5. **Prior installation prerequisites were incompletely qualified.** Earlier deployed recovery omitted the mandatory gate; the subsequent scope transition initially missed helper/Music bindings; another deployment omitted recovery-control.lock. Historical operator evidence and current source explain those targeted repairs. They are currently corrected and are not the present blocker. The packet now retains separate installed source variants, effective settings and root-helper transcript provenance.

## Source-backed regression table

| Mechanism | Origin/evidence | Causal conclusion |
|---|---|---|
| Strict physical Ollama cleanup and unknown/duplicate PID refusal | Remote commits93b26eb and4a8b1a7, Oct26; current guard preserves branch | Predates Nov10 patches. New instrumentation exposes previously terse refusals. Not evidence that a latest patch introduced unknown compute. |
| Probe compatibility | Nov10 compatibility module added exact pinned executable/argv/parent/cgroup/namespace checks | Allows a narrowly attested real device probe; does not authenticate every helper or late compute row. |
| Second-census zombie/exit transition | Completion release modifies ollama_probe after attestation | Removes one false refusal while quarantining until positive exit/fresh idle; first-census identity absence remains. |
| Procps EXE-column truncation | Nov10 restoration sets exe:4096; real procps4.0.4 qualification | Confirmed collector bug repaired. Current missing-census PID is not evidence of truncation recurrence. |
| HA manual allowlist | ha-idle-manual, preserved through current wheel | Deliberately narrow authority; unknown/duplicate failure remains outside it. Unsupported is a policy limit, not safe-idle proof. |
| Recovery check contention | Nonblocking locks in idle/manual controller, shared with RecoveryManager.tick | Confirmed liveness/usability defect. Zero-wait protects exclusion but should not obscure a specific unsupported verdict. |
| Music450→475MiB | Separate Nov9 namespace/475 rebind approval and deployment record; current settings and policy agree | Deliberate accepted allowance, not introduced by Ollama patch. GPU0 row422MiB is below both thresholds; current failure concerns unknown GPU1 PID. No causal evidence connects475 to this failure. Five-second budget unchanged. |
| ToDo deterministic standalone ID | Old Media `_run` hashed step/payload and rebound terminal retired job | Confirmed persistent fallback mechanism, repaired with durable UUID intent and terminal receipts. Current generate path intercepts standalone requests; strict cached dates/payloads preserved. |
| Media abandoned output intent | Old OutputSlot treated absent pre-submit request as permanently busy | Corrected external dispatch reconciler uses bounded exact-ID/hash GET checks and truthful refusal; it still blocks current retained owner. |

## Timing and history

Cleanup_timeout defaults60seconds, but a RecoveryRequired predicate fails immediately. Increasing this deadline alone cannot fix unknown_pid, duplicate or invalid identity refusals. Three stable samples and positive exit evidence are required for ordinary release; logging adds durable writes for changed attestations/failures, but this audit did not measure those writes as the cause of the08:01 race.

The captured journal contains1797 jobs. Automatic worker finalization reads persistent settlement/lifecycle records while holding recovery.lock; snapshots repeatedly parse ownership state and receipts. These costs can widen contention windows. Source and observed contention support a scaling concern, **not** a measured attribution of all latency to journal size. No logs/journals were truncated or compacted.

Direct Ollama clients are installed. A current connection snapshot and empty current residency cannot exclude a historical competing request. GPU compute observations cannot alone attribute requests. Do not label3645974 a safe probe, foreign compute, PID reuse or driver bug without additional evidence.

## Safety versus liveness

Rejecting real identity change, unknown active compute, duplicate/ambiguous observations, stale approval and uncertain mutation prevents unsafe reuse. Keeping a logical lease after missing proof is intentional safety containment. The reliability problem is repeated failure to obtain sufficient ordinary-operation evidence and the absence of an approved drain path for some terminal classes. A better collector can close certain windows by collecting identity before it disappears and by repeating a bounded correlated observation; it cannot retroactively infer an unknown executable from a zombie name or idle memory. Any broader retirement policy needs independent authority review.
