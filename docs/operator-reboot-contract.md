# Reviewed contract recovery after a VM reboot

Development only. No deployment, service action or production recovery is
authorized by this document. Base: `5c32ca71a685ccd5a986dcb001c8116dbebfa672`.

`recover-reboot-contract` is a local operator action for the precise paired,
on-demand wallpaper case: a genuine old/new kernel boot transition, one retained
GPU0/GPU1 lease, a failed `guest_reboot_interrupted` job at `submit_intent`, the
original exact container ID and image, and the original contract refusal before
any current-boot startup record. It does not support other targets, same-boot
failures, partial leases, changed physical locks, active containers, or an
arbitrary policy file. It cannot start/stop a container or submit inference.

The unexplained fingerprint difference is **not classified as harmless**. The
operator instead explicitly approves the complete current configuration as new
authority, bound to this particular incident. A historical inspect is not needed.

## Approval requirements

Use the deliberately unusable
[approval example](../examples/reboot-contract-approval.example.json) as a private
document. All keys are required; extra keys are refused. No review flag may be
set automatically. `approval_ref` must identify the actual operator review.

The operator must privately inspect the complete Docker inspect object, including
all `Name`, `Image`, `Config`, `HostConfig` and `Mounts` fields. Review executable
command/entrypoint, environment, user, privileges/capabilities, devices, networking,
supervision, all mount sources/destinations/options, nested mounts and GPU exposure.
Do not copy private paths, environment values or credentials into public reports.
Retain the exact reviewed inspect and approval files privately with mode 0600.

This correction accepts only explicit NVIDIA DeviceRequests: one request with
`Driver=nvidia`, `Count=0`, both original physical UUIDs in GPU0/GPU1 order,
`Capabilities=[["gpu"]]`, `Options={}`, no direct `Devices`, and `Privileged=false`.
An unsupported representation stops the command for review; do not loosen this
check to accommodate it. All current executable settings and mount attributes
remain fingerprinted by the existing contract function without new normalization.

Approval binds the exact job/request hash/lease/target, old and current boot IDs,
scope, original policy ID, old/new contract hashes, container ID, image, both GPU
UUIDs and both original lock identities (`device`, `inode`, `mode`, `uid`, `gid`).
Refresh and cross-check these against the failed job, both owners, installed
policy, active configuration and real lock stats; never fill them from guesses.

`new_contract_sha256` is `RecoveryObserver.contract(reviewed_object)`.
`reviewed_metadata_sha256` is `recovery.digest(reviewed_object)`, using the entire
parsed inspect object, not the wrapper list or raw file bytes. The CLI accepts an
object or a one-object Docker inspect list. The additional digest binds the exact
private review artifact, while live comparisons use the unchanged contract's
documented runtime/hostname/mount-order normalization.

Every review flag must explicitly be true: complete configuration, GPU exposure,
mounts, executable settings, contract transition approval and caller exclusion.
The CLI performs fresh observations; operator assertions cannot replace them.

## Proposed operator procedure after separate authorization

1. Review this development commit and offline validation. Arrange an approved
   maintenance window with participating callers excluded and the runner
   supervisor absent. This command requires the existing maintenance, supervisor
   and recovery locks and refuses a running runner. No service action is included
   in the command. Any package installation/service stop/start requires separate
   production authorization. Run the reviewed build from the intended local
   environment with the original active configuration.
2. Preserve private evidence copies of current ownership, jobs, results, package
   identity and lock stats. These copies are evidence, never rollback journals.
   Capture and review the current exact container by both ID and name; prepare
   and explicitly approve the private review/evidence files as specified above.
3. Invoke once, substituting only the reviewed private filenames and active config:

   ```sh
   aihub-gpu-runner --config /path/to/active-config.json recover-reboot-contract \
     --job-id stage-797c2484bd75dec25e57734277800aa1a62b26d3f68c581c \
     --evidence-file /private/approved-transition.json \
     --container-review-file /private/reviewed-container-inspect.json
   ```

4. Inspect durable state before admitting callers. Require both owners absent,
   `verified_boot` equal to the approved current boot, boot status `ready`, exactly
   one new recovery audit with this approval ID, the original failed job/error and
   lifecycle retained, original lock identities, unchanged historical jobs/results,
   and wallpaper still stopped. No inference is replayed or required to validate
   this operation. Normal service/caller resumption requires separate approval.

## Commit and interruption semantics

The command reuses `RecoveryObserver.stopped_ready()`, boot readiness/generation
checks, the original physical locks and `RecoveryManager.finish()`'s atomic
audit-and-paired-release authority. Fresh checks cover stopped PID 0 and unchanged
generation, exact ID/name/image/new contract, restart supervision, the genuine
boot transition and old process identity absence, other backend queues/readiness,
GPU UUIDs/process attribution, per-service and aggregate stopped-idle calibration,
utilization, temperature, supported health counters and current-boot driver logs.
No threshold, scope, resource mapping or control permission changes.

The candidate policy stays in memory while these checks run. The ordinary boot
worker still sees the original refusal and cannot automatically use a pending
approval. Only the final ownership transaction publishes all of the following:

- the wallpaper hash transition and resulting policy ID;
- the current-boot stopped/readiness record and verified boot;
- the existing recovery incident's released phase and one recovery audit;
- removal of both owners together.

That audit retains the complete previous recovery-policy installation, previous
boot-readiness proof, original refusal status/error/attempt boot and exact approval
bindings. The original failed job/error/lifecycle remain; normal recovery proof
fields and update timestamp may be added. Results are never written or removed.
The approval's full-inspect digest is stored; secret-bearing inspect contents are
not copied into the audit. Keep the private review artifact for future review.

A probe or binding failure leaves both owners, old policy and boot fence intact.
A crash before final commit may leave an observing/verified incident and job
recovery proof, **without** released ownership or new authority. Inspect first;
the same unchanged approval can be explicitly reinvoked to obtain entirely fresh
proof. Ordinary runner restart cannot resume that pending approval.

After a successful atomic commit, reinvoking the identical approval returns
`already_recorded=true`, with no second release/audit or workload. It confirms the
existing receipt's file/directory durability; this handles a lost final flush
response. It does not claim a new health assessment or touch any later lease.
A new kernel boot or changed authority/physical identities rejects the old approval.

Treat any persistence exception or lost CLI response as outcome-uncertain. Read
the journal under the existing authority: it must show either the complete old
state with both owners, or the complete new state with the matching audit and
current verified boot. Never assume failure means the write did not happen.
If storage flushing still fails, the command does not acknowledge completion.

## Rollback

Before final commit, the original policy/refusal/paired reservation remain; keep
them. After final commit, never restore an old ownership/job snapshot, undo the
audit, change fingerprints by hand or recreate locks. The journal still uses the
existing policy schema understood by the base runner; the explicit approved
contract is now its authority, and the old authority remains in the recovery
audit. A code rollback must retain the **current** journal and policy and follow
the separately approved normal maintenance procedure. Any request to revert the
approved container configuration requires separate review, not this command's
`--rollback` (which is refused).

This correction establishes observed recovery readiness. It does not diagnose or
fix the kernel memory-management fault or establish its recurrence risk.

## Offline validation

Local Ubuntu 22.04 / Python 3.12, with real POSIX flocks and AtomicStore writes in
private temporary Linux directories; all Docker, GPU and backend responses are
synthetic. No production host is involved.

- Focused `tests.test_reboot_contract`: **16 tests passed**.
- Full `validate_offline.py`: **464 tests, 0 failures, 0 errors, 1 existing skip**.
- The existing skip is the guarded historical meeting-launcher harness.
- Focused coverage includes incorrect/missing approval bindings, complete-config
  drift, GPU exposure, active/supervised wallpaper, queues, attribution, idle/health
  thresholds, stale/partial leases, changed boots/locks, process survival, CLI
  routing, every journal-write cut before/after its effect, reopen/ordinary runner
  startup, atomic rename failures, directory flush uncertainty, identical repeats
  and later-lease protection.

Local validation artifacts (ignored by Git):
`test-results/reboot-contract/focused-linux.log`, `full-linux.log`, `full-linux.json`.
The JSON records the tested Python source hashes. These tests establish offline
behavior; they do not constitute production approval or kernel stability evidence.
