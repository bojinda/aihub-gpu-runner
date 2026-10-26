# Never-submitted wallpaper failure recovery (review candidate)

This optional category fails an acknowledged, never-started request without
replaying it. It does not authorize any Docker action, backend submission,
unload, restart, termination, or successful artwork publication. It is separate
from normal lifecycle output receipts and existing execution recovery.

## Authorization and eligibility

Old policies remain valid and grant no new authority. A newly authorized policy
must explicitly include:

```json
"never_submitted_prestart_recovery": {
  "enabled": true,
  "ingress_isolation_verified": true,
  "caller_exclusion_ref": "REVIEWED-ISOLATION-EVIDENCE-REFERENCE"
}
```

The validator requires on-demand paired wallpaper, routine GPU0 Music and GPU1
Ollama targets, all three approved service bindings and both GPU calibrations.
The gate changes the accepted policy digest. It is an operator authorization
bound to a scope, not a network control or an automated isolation detector.
Raw ComfyUI/Ollama ingress and all non-admission callers must be independently
excluded before enabling it. Empty API queues do not prove this exclusion: a
foreign request in an already-captured process may evade observation.

Only newly prepared records capture the gate's policy/scope, physical lock file
identities and hashes of core/backend/recovery/lifecycle/prestart source. The
prepared lifecycle must be durable before stopped readiness. If that readiness
raises `CleanupPending` or `Uncertain`, the worker durably records a hash-bound
failure handoff before returning, still before any start intent. Recovery checks
the worker's matching error code and native lock exit. A crash without this
acknowledged handoff remains unsafe. General `RecoveryRequired`, failed/ambiguous
writes and historical operator-latched incidents are never converted to this
category. A failure marker whose write acknowledgement is lost cannot qualify
through the generic worker error path.

The exact request hash is recomputed from target/operation/payload. Lease, kernel
boot, verified boot, scope, policy digest, both device UUIDs, native lock identities,
source bytes and complete lifecycle identity must match. No running generation,
start/submission intent (even null), backend handle, execution metadata,
completion/cleanup flag, output/result file or normal receipt is allowed.
The stopped container must retain its exact ID, image/config contract, start
generation, PID 0 and restart policy `no` (Docker's equivalent empty policy is
accepted by the existing observer). Reboot/boot ambiguity fences this category.

## Observation and atomic outcome

The existing RecoveryManager takes its recovery mutex and both existing native
GPU locks after checking their physical identities. A supervising worker or busy
native lock causes no recovery writes or probes. One ownership transaction
reserves the existing persistent recovery rate budget and records one observation
deadline. There is no budget renewal after interruption. The original startup
deadline may already have expired: no start is authorized and a separate, freshly
bounded accepted-policy observation is required. An invalid startup deadline or
expired recovery observation deadline cannot grant release.

Each sample verifies empty expected Music queues/Ollama model residency plus
valid backend readiness, approved service generations/process birth identities,
current-boot driver health, physical UUIDs, per-service memory, aggregate stopped
idle memory, utilization, temperature, ECC and retired pages on both GPUs.
`RecoveryObserver.gpu_sample()` is unchanged: unknown/foreign processes and
per-service excess fail immediately. Aggregate high memory or transient uncertain
observations reset stability and may consume only the one bounded deadline.
All configured consecutive successful samples and the PID births are retained in
the proof. Identities and exact source/job/journal bindings are rechecked before
the final transaction while the native locks remain held.

One atomic `ownership.json` write adds:

* `wallpaper_prestart_failure_receipts[lease_id]`: schema 1,
  kind `never-submitted-prestart-failure`, lifecycle identity (request hash,
  lease, boot, resources, policy ID, container ID, GPU UUIDs), scope, incident ID,
  prepared-record digest, request, captured source/lock binding, physical proof,
  error code `wallpaper_prestart_failed_no_submission`, release timestamp;
* the corresponding released automatic incident and exactly one recovery audit
  with that distinct kind and `runner_observations` provenance;
* removal of both paired owners in the same write.

Only afterward does a separate job write finalize `failed` with that error and
`prestart_failure_receipt=lease_id`. It creates no result JSON, artwork or normal
output receipt and does not claim successful execution. Startup/tick reconciliation
checks the prior failure receipt, exact job/record hashes and unique released
audit; it performs no observer calls, no GPU lock acquisition and no second
release. A committed receipt remains authoritative after a lost fsync reply.
Without a committed receipt, detected storage uncertainty latches the job for an
operator. Interrupted observation intent is also operator-required; it never
starts a new observation window.

## Staged deployment and rollback gates (not executed)

1. Review the cleanup compatibility commit independently, then this separate
   stacked commit. Run complete Linux discovery and inspect wheel source parity.
   Reproduce delayed physical convergence with approved hardware access in a
   later maintenance window; offline fake probes do not validate the driver.
2. Keep all callers excluded and resolve the unfinished paused Media render
   before any resume decision. Verify actual ingress isolation on AI-HUB, Media,
   WebAuth and Home Assistant; no time-expired attestation substitutes for it.
3. Deploying cleanup alone does not enable this category. Keep the new policy
   field absent until physical calibration and ingress evidence are approved.
4. Enabling requires a separately reviewed owner-free policy migration with the
   supervisor stopped, both native locks held, resolved jobs, preserved journals,
   source/config/contract/UUID checks and explicit old/new digest approval.
   `authorize_policy` deliberately rejects replacing an already accepted policy;
   this change provides no migration command. Existing policy/scope migration
   support is a deployment blocker, never permission to edit journals manually.
5. Roll back only from a reviewed owner-free state, preserving failure receipts,
   recovery budgets and audits. Reconcile any committed receipt with compatible
   code before removing its reader. No rollback clears live owners or replays a
   request. Disabling the gate needs the same separately approved migration.

Follow-up, outside this change: report retained operator-required owners promptly
to waiting clients. Acquisition timing and waiting-client behavior are unchanged.
