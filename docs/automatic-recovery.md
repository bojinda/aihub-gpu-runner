# Automatic recovery delta (offline, undeployed)

This delta applies **after** the pending dual-GPU wallpaper candidate and its
follow-up. It does not change workflow bytes, models, Qwen limits, GPU exposure,
CLIP loader, the scheduler, or the shared admission contract. Recovery is off
until the local deployment action below installs an approved policy. The
incremental patch is relative to the entire pre-task working tree, including
the pending untracked tests; it is not a patch against HEAD. The independent
review follow-up is supplied separately as
`recovery-delta/automatic-recovery-review-corrections.patch`, relative to the
uncommitted automatic-recovery implementation at the start of that review.

## Runtime behavior

Every new lease records `/proc/sys/kernel/random/boot_id`, including leases
acquired by independently supervised host stages and dual meeting sessions.
Missing, malformed, or unavailable boot IDs are never reboot evidence.
The runner terminalizes stale API waiting jobs without submitting them again.
With policy enabled, it also terminalizes previous-boot waiting host jobs;
same-boot host stages, including their waiting supervisors, remain untouched.

Admission itself fences **all** resources when the installed policy's last
verified boot differs from the kernel boot, including admission by host-stage
launchers before the API runner starts. Startup reconciles previous-boot owners
under the original physical locks. Nonterminal owned work becomes failed with
`guest_reboot_interrupted`; already terminal jobs and result files stay intact.
Current queues, GPU UUIDs/health, calibrated memory/utilization, and backend
readiness must pass before ownership is released. A dual meeting session also
requires fresh verification of the intended GPU1-only Ollama placement with the
existing switch controller; old restoration proof does not suffice. Owner-free
boot readiness is checked before the global boot fence opens.

The revised policy explicitly includes `boot_startup=verified_reboot_once`.
While the global boot fence is closed, the runner takes the original physical
locks and verifies that any retained owners belong to a different valid boot.
It inspects the exact approved wallpaper container, checks current GPU health,
calibrated cleanup and other expected queues, then records a per-boot start
intent before starting a stopped container. Docker restart policy remains `no`.
This is integrated into runner startup; no independent wallpaper restarter is
installed. An owner-free installation follows the same boot-only path.

The durable `boot_startups` record binds boot/policy/container/physical UUIDs and
permits at most one start command per boot. An interrupted intent is observed,
never reissued. Already-running approved containers are verified without a start.
Bounded retries cover Docker running/PID transitions, API connection failures and
initializing system statistics. Unknown queues, identity drift and health failures
fail closed. Container generation is checked after readiness, before owner
handoff, and before the final boot fence opens. Failed initialization or an
unobserved start effect retains owners and exposes a persistent operator-required
boot status. Once the current boot is verified, a same-boot runner restart never
invokes boot startup. Interrupted startup in a still-fenced new boot resumes its
existing per-boot record and cannot issue a second start.

Same-boot restarts never infer process death. Surviving native locks exclude
recovery. A wallpaper incident may read terminal history and retrieve an existing
artifact, without submitting another prompt. At most two history/cleanup
reconciliation attempts are recorded. Successful terminal history plus verified
cleanup/readiness completes normally. Otherwise, termination requires either the
persisted runner execution deadline to expire or an owned execution failure.
A client wait timeout or API connection error alone cannot trigger termination.
Missing, nonnumeric, nonfinite, boolean, nonpositive or unrepresentable persisted
execution deadlines produce `execution_deadline_missing_or_invalid`; they cannot
wait indefinitely or authorize termination. Existing valid-deadline completion,
history retrieval and successful cleanup behavior is preserved.

Native supervision is checked before creating an incident or writing recovery
metadata. Healthy jobs produce no recovery incidents or periodic recovery writes.
A stale snapshot of a lease that a worker just released is ignored, preserving
its completed job and result. Identical journal/job/operator-required outcomes
are not rewritten, including their timestamps.

Ongoing recovery runs in one managed `gpu-recovery` worker, outside the admission
scheduler and inference worker pool. Workers only notify it. The shared native
recovery lock prevents concurrent duplicate execution across invocations. Bounded
Docker/GPU/kernel-log work holds physical locks for the affected resources, with
short admission/job journal transactions. A GPU0-only incident therefore leaves
the scheduler free to admit an independent GPU1 job; dual incidents continue to
hold both resources. Initial startup reconciliation precedes scheduler startup,
and the admission boot fence also protects host-stage callers. Runner shutdown
joins the recovery worker before releasing its supervisor lock. An unexpected
worker exit exposes `recovery_worker_status=operator_required` without spawning a
replacement that could repeat an ambiguous control action.

Wallpaper teardown persists the job/hash/lease/boot, container ID and start
generation, runtime contract, physical UUIDs, process identities, and signal
intent before acting. Only that container receives SIGTERM. A bounded grace
period may be followed by one SIGKILL if the policy permits it. This uses explicit
signals rather than Docker stop's implicit escalation. Original host PID/start
identities must disappear; a stopped Docker state alone is insufficient. The
container remains stopped until GPU cleanup checks pass, then receives at most
one start. Readiness, empty expected queues, stable GPU calibration, and unchanged
new container generation must pass before handoff. Both dual wallpaper owners
are removed together. No inference is replayed.

The wallpaper container requires its exact full Docker ID, name, image and
configuration hash. Known services may use the approved `contract` identity mode:
their current named container must match image and runtime configuration after
legitimate recreation, such as dual-meeting restoration. Docker's generated
hostname is excluded from this hash; explicit hostnames remain bound. Current
IDs/start generations are recorded in observations and never receive recovery
controls. Their host PIDs account for GPU contexts; per-service and
aggregate memory ceilings allow calibrated idle music contexts. Unknown GPU
processes, active unrelated queues/models, UUID drift, excess utilization,
uncorrected ECC, pending retired pages, high temperature, and unavailable or
invalid observations block handoff. `[N/A]`/`N/A` for unsupported ECC/retirement
counters is recorded as unsupported, not converted into a fabricated zero.
Readable current-boot kernel journal records are also required. Reported NVIDIA
Xid, fallen-device, initialization or fatal driver faults stop recovery; an
empty, inaccessible, malformed or wrong-boot kernel journal cannot establish health.

## Storage and restart behavior

The existing AtomicStore and NativeLock primitives are used. `ownership.json`
gains `recovery_policy`, `verified_boot`, `boot_startups`, `automatic_recovery`, `recovery_budget`
and boot-readiness/status fields. Existing history is retained. Job records gain
boot/deadline/container/process observations and recovery status/proof. Physical
lock files and ownership/jobs/results are never deleted or recreated by recovery.

The incident key uses job, request hash, lease, owner boot, target, physical GPUs
and policy binding. Mutable phase/status/timestamps are excluded. Container
identity is bound into the persisted incident before any teardown; a change to
the job's bound container stops the incident. Intent writes precede controls.
Restart observes pending intent rather than issuing the same signal/start again.
A lost control response can be resolved by current observations. Each incident
can reserve one teardown/restart attempt. The persistent timestamp ledger limits
attempts across incidents, boots and runner restarts; clock rollback does not
expire entries early. Reinstalling the same policy does not reset the ledger.

Proof and job finalization precede release. The unique recovery audit entry,
released incident phase and removal of all original owners share **one atomic
ownership write**. An interrupted write is reread on recovery. No whole-record
timestamp or journal hash invalidates a stable incident. A final job may still
have held owners until readiness/release completes; check ownership as well as
job state. There is no remote force-release or recovery endpoint.

## Deployment procedure (proposal only; separate production authorization)

1. Keep participating callers excluded. Resolve any existing legacy owners by
   the established reviewed recovery path; do not backfill their boot IDs.
   Require an owner-free, terminal-job installation with original physical lock
   identities. Preserve a copy of the pre-task candidate and the incremental
   patch. Deploy/review the combined candidate through the existing package
   procedure, with the runner stopped. Activate any separately approved wallpaper
   conditioning scope transition **before** authorizing this policy.
2. Review a private policy using
   [the policy schema example](../examples/recovery-policy.example.json).
   Fill its exact active config `scope`, full container IDs, image IDs and runtime
   contract hashes from read-only observations. The hash is
   `RecoveryObserver.contract(docker_inspect_record)`, SHA256 of canonical
   `{Name, Image, Config, HostConfig, Mounts}` excluding an automatically generated
   Docker hostname; no timestamps or PID enter it. Deploy the same package to
   all participating host-stage launchers before enabling the policy.
   Include each GPU-using service whose idle contexts are allowed, with calibrated
   per-GPU idle ceilings, aggregate ceilings, utilization/temperature limits and
   physical UUIDs. The dedicated `comfyui-wallpaper` container must have Docker
   restart policy `no` and no independent controller that restarts it during
   recovery. This implementation does not change restart policy. Choose a grace
   period well below `observation_timeout` (for example 10 versus 60 seconds).
   The service account must already have read access to the current kernel
   journal through the pinned `journalctl` path; this delta grants no privileges.
   Approve exclusive wallpaper access through shared admission, exact-container
   stop/start, `boot_startup=verified_reboot_once`, optional forced termination,
   and the incident/window limits once. This schema is approved before the first
   deployment; an older policy lacking the explicit boot-start contract is
   rejected rather than silently acquiring new authority.
   Policy placeholders/null ceilings are deliberately unusable.
3. In that approved deployment window, run once with the deployed venv:

   ```sh
   aihub-gpu-runner --config /path/to/active-config.json authorize-recovery \
     --policy-file /path/to/reviewed-private-policy.json \
     --approval-ref APPROVED-DEPLOYMENT-REFERENCE
   ```

   No per-incident evidence file is needed. This action acquires maintenance,
   supervisor, jobs, admission and original physical locks, validates clean state,
   observes current queues/GPU readiness, and verifies GPU1 Ollama placement if
   dual meeting is configured. It writes policy and current verified boot into
   the existing journal without changing scope, legacy jobs, results or history.
   Repeating the identical policy is idempotent; replacing it is refused.
4. Start the runner in the approved window and inspect its journal/status through
   the existing mechanisms. Verify the policy ID, boot-readiness status and no
   owners. Resume callers only under the separately approved deployment plan.
   An offline test pass is not production calibration or permission for inference.

Boot reconciliation runs inside the runner under its existing `Type=simple`
service. Deployment must arrange and verify appropriate Docker/storage/device
ordering and access, along with shutdown timeouts, in the approved unit.
Transient backend initialization is retried within
the approved observation deadline; unavailable Docker/device infrastructure that
cannot be verified safely leaves the boot fence closed for operator review.
No unit, restart policy or production service is changed by this source delta.

## Rollback

Exclude callers, stop the runner, and require terminal jobs, no owners, verified
backend readiness and intended GPU1 Ollama placement. If recovery is incomplete,
resolve it first; never roll back to bypass a reservation. Restore the saved
pre-task source/package (which includes the pending wallpaper candidate), using
the incremental patch in reverse only against its exact matching source.
Keep current config/workflow, physical lock files, jobs/results and the **current**
journal with all recovery history and budget entries. Never restore an old
ownership snapshot. Older candidate code tolerates the extra journal fields and
retains its manual recovery behavior. Keep callers excluded until its normal
deployment checks pass. Re-enabling the same recovery build preserves the policy
and rate ledger; a config scope/policy change requires a separate reviewed
migration, not edits that erase history.

## Operator-required cases and exact limits

Automatic actions stop with job `recovery_status=operator_required` and a
`recovery_error`, or `boot_recovery_status=operator_required` for owner-free boot
readiness. The incident phase/error and budget are visible in `inspect` output.
Persistent failures do not get a fresh attempt on service restart.

* Legacy/unverifiable boot identity, mismatched request/lease/resource identity,
  missing pre-submission container metadata, lock drift, unexplained activity,
  unverified cleanup/readiness, GPU/driver probe/health failure, or exhausted
  incident budget require operator review. Recovery never fabricates proof.
* Same-boot Ollama and independently supervised host stages are preserved. Their
  uncertain execution recovery remains the existing local operator path.
* A previous-boot dual meeting with Ollama still in dual placement is failed and
  **keeps both owners**. This delta verifies intended placement but does not
  automatically switch Ollama. Use the existing approved `dual-restore` path and
  manual recovery; wallpaper recovery never stops or switches Ollama/music.
* Docker auto-restarting wallpaper, external container recreation, renamed or
  changed containers, and policy changes are refused. The deployment must first
  establish the approved dedicated-container control contract. No Docker daemon
  restart, VM reboot, GPU reset, exposure change or unrelated process kill exists
  in this implementation.
* If a start intent was persisted but the runner died before the start took
  effect, a later runner cannot distinguish that from an unsuccessful start.
  It retains ownership and requests review instead of issuing another start.
  A pending SIGTERM intent is observed; after grace, only the one separately
  permitted SIGKILL may be used. If that intent is also ambiguous and the process
  survives, recovery stops. These are deliberate exactly-once control limits.
  The same rule applies to a per-boot start intent: a stopped container after the
  bounded observation window requires review, not another start command.

## Delivery helper scope

`meeting-pipeline/deliver_aihub_recovery.py` is a separate, previously created
local copy helper. It checked captured source hashes and copied the first tested
delta from a writable scratch directory into this repository. It is not imported
by the runner, is not used by tests or the deployment procedure, and is not
required to apply either patch. It remains outside this repository and outside
the supplied recovery diffs; this review follow-up does not modify or use it.

## Offline verification

The focused suite uses temporary journals and actual Linux flock, fake Docker,
fake backend transports and synthetic GPU observations. It covers reboot/waiting
jobs, same-boot host/backend survival, result/history preservation, container
generation checks, graceful/forced control failures, idle music, either GPU,
meeting placement, missing metadata, persistence/control interruption boundaries,
idempotent audit/release, concurrent admission and durable rate limits. The
combined suite also covers existing successful inference/cleanup and all pending
wallpaper conditioning/UUID/scope-transition tests. See the delivered Linux test
log for the exact command, interpreter and result.
The review suite adds stopped-container reboot cases with/without owners,
transient initialization, boot-readiness generation races, zero-write healthy
workers and unchanged failures, stale cleanup snapshots, slow GPU0 recovery with
concurrent GPU1 admission, managed worker shutdown, invalid deadlines, and 14
per-boot persistence interruption positions. No test uses production services.
