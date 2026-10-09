# Dual-GPU meeting session checkpoint — 2026-10-09

**Offline implementation for review. No commands below have run on AI-HUB.**
The operator reports the runner installed/bootstrapped with a ready, owner-free
journal and Ollama restored to GPU1-only. This supersedes the older undeployed
runner assumption; it is not a fresh Codex inspection. The operator's completed
Qwen3.8:27b 192K benchmark across both RTX 3090s, fully GPU-resident, establishes
the intended core use case. It was not repeated here. Existing map/reduce,
prompts, approved-input identity, recap/redactions and audio policy remain intact.

Dual meeting summarization is now the next core checkpoint, independent of gateway
or website work. Routine Ollama remains GPU1; WhisperX/wallpaper/music remain GPU0.

## Contract and bounded switching

A separately named `ollama` target declares `resources: [gpu0, gpu1]` and a complete
trusted `mode_switch` policy. It requires exactly one routine GPU1 Ollama target
at the same backend URL. Without the explicit policy, dual resources are rejected.
Its allowlist must include every unchanged configured map/reduce/recap model;
the example model entry does not authorize changing the existing map model.
The HTTP API cannot submit to it: only an explicitly selected local meeting
HostStage uses this mode. Existing single-GPU payloads/defaults do not change.

One existing Admission transaction obtains both native locks, checks durable
ownership and writes one lease identity to both GPU owners before switching.
Failure to obtain either lock leaves neither partially reserved. The session
keeps both locks across every map, reduce and optional recap call. Host requests
borrow the enclosing lease and never acquire a second reservation. CPU work
inside that selected summary session also retains its two-GPU reservation.

The local Compose controller supports the **recorded CDI deployment mechanism**.
Prepare two complete private Compose definitions with identical project/service/
container/image/environment/volumes/ports/restart configuration, differing only
in `services.ollama.devices`:

```diff
 devices:
+  - nvidia.com/gpu=0
   - nvidia.com/gpu=1
```

The ordinary GPU1 file is never rewritten. The dual definition is a separate,
hash-bound file. Both are rendered with the same project directory; the controller
checks a normalized configuration hash with only the device field removed. It
also checks hashes of both raw files, the existing digest-pinned/local image ID,
installed Docker/Compose/Ollama versions, physical GPU index→UUID mapping and
running model-volume/port/restart/Q8/Flash Attention metadata. Restrictive CUDA/
HIP device environment overrides require separate review rather than silent edits.
Docker/runtime permissions must already be approved; no sudo or account repair.

Each switch uses the captured normalized configuration through stdin:
`compose up -d --no-deps --no-build --pull never --force-recreate --wait` for the
single named Ollama service. No image pull, build, dependency restart, ComfyUI
restart, model-volume replacement or live Compose-file edit is implicit. These
flags and CDI are documented by [Docker Compose](https://docs.docker.com/reference/cli/docker/compose/up/)
and [Docker CDI](https://docs.docker.com/engine/containers/run/#device-access-with-cdi).
Installed-version behavior is still an operational acceptance gate.

Before activation: verify GPU1-only physical exposure, pinned image/runtime,
backend version/readiness, empty residency and repeated calibrated idle memory
on both physical GPUs with both ComfyUI contexts present. Before launching the
summary: persist switching intent, recreate once for dual exposure, then verify
both physical UUIDs/readiness/idle state. A child cannot submit during activation.

After **known completion of every recorded model request**, unload all models
owned by the session together, verify empty residency and calibrated memory, then
restore GPU1-only once. Verify original physical exposure, unchanged runtime/
configuration, backend readiness and stable memory before durable release.
[Ollama's running-model endpoint](https://docs.ollama.com/api/ps) is one observation;
empty `/ps` or a successful unload alone is insufficient proof.

Expected interruption: Ollama is recreated once on activation and once on normal
restoration. Its direct API is unavailable briefly at each transition and models
load afresh. All routine GPU1 and GPU0 reservations wait for the entire session.
No running job is preempted. ComfyUI containers remain separate and running.

## Failure, reconnect and operator restoration

Same stage ID/hash reconnects; a changed target/input/context conflicts or derives
a new automatic ID. A lost switch response, interrupted supervisor/child, unknown
model completion, failed unload, placement/readiness drift, persistence error or
restoration failure retains **both durable owners**, even if flock becomes free.
There is no automatic retry, rollback, restoration or replay of uncertain work.

An explicit local `dual-restore` action requires exact job/hash/lease/target and
switch-binding evidence, a specific restoration approval, operator confirmation
that backend work is resolved, and the observed current mode (`dual` or `gpu1`).
It reacquires both original physical locks under the retained owners, checks
quiescence/placement and performs at most one approved restoration. If already in
GPU1 mode, it verifies without recreating. It records restoration proof but **does
not clear owners**. Separately approved `recover` requires that stored proof and
exact restoration evidence; the old stage becomes failed and is never replayed.
If a command times out, inspect its outcome under retained exclusion; never repeat
a recreation blindly. Active native owners block operator restoration/recovery.

## Operator deployment sequence — proposed only

1. **Approve an AI-HUB window and fresh bindings.** Keep current applications stopped,
   HA/Continue/voice paused and cron intentionally disabled. Exclude direct/manual
   Ollama callers and GPU0 callers: dual mode consumes GPU0, so WhisperX and both
   ComfyUI submission paths are dependencies here. Stopped/disabled paths are
   technical controls; operator cooperation alone is not enforcement. Do not
   invent firewall rules. Drain and resolve work; ready/no-owner snapshots alone
   do not prove no new work can arrive. Use existing verified SSH pins and bounded
   per-command app approvals if remote inspection is separately authorized.
2. **Bind/back up the delta.** Record installed runner wheel/source hashes, current
   config and scope, original lock path/inode/access, ready/bootstrap/history,
   complete private state backup and current callers' configuration. Prepare the
   dual Compose definition, both raw hashes, preserved-config hash and selected
   runtime-contract hash; verify the digest/image against the accepted restoration.
   Refresh versions/UUIDs and measure both idle ceilings with the existing ComfyUI
   contexts. Fill the deliberately non-live-ready example. Do not change model,
   prompts, Q8, Flash Attention, volumes, ports or services incidentally.
3. **Install the reviewed delta under separate approval.** Use an immutable reviewed
   package/wheel with no dependency upgrade or image pull. Keep the old runtime
   and config for rollback. Install the same SDK for the meeting helper/summary
   interpreter. Stop only the idle runner service in the approved window; no
   backend restart is needed for installation or the scope transition itself.
4. **Transition the existing scope, never bootstrap/reset.** Add only the dedicated
   dual target to a new private config in the same config directory. Existing
   targets/settings/state/lock paths must remain identical. Resolve every nonfinal
   job, including waiters. The explicit local transition locks the old supervisor,
   jobs/admission mutexes and both original GPUs, requires a ready/no-owner journal,
   and changes only its scope plus an append-only transition record. Bootstrap ID,
   ready state, physical lock identities, jobs/results/snapshots and recovery history
   remain. Old-config launchers fail closed after the change. Update all participating
   launchers to the same approved new config before starting the reviewed runner.
5. **Validate sequentially with separate live approvals.** Inspect/authenticate the
   new runner first, proving no owner/reset and normal target policy. Then one
   bounded dual historical meeting comparison using existing indexes; validate
   exposure, actual requested context and GPU-resident placement against the
   operator's benchmark, all outputs/recap/redactions, unloading and GPU1 restoration.
   Exposure alone does not prove inference placement. Record both physical UUIDs,
   model residency/processor placement and memory during approved inference.
   Next check normal GPU1 and independent GPU0 workloads one at a time, then bounded
   contention/reconnect/recovery. Do not resume callers until operator acceptance.

Read-only calculation of scopes uses `load_config` and does not initialize the
journal. Operational command templates (execute only under separate approval):

```bash
# Evidence: approval_ref, exact old_scope/new_scope; callers_excluded,
# backend_quiescent, cleanup_verified, gpu1_restored, backend_ready,
# placement_verified must be true based on actual operator-approved checks.
"$REVIEWED_PYTHON" -m aihub_gpu_runner --config "$OLD_CONFIG" transition-scope \
  --new-config "$NEW_CONFIG" --evidence-file "$TRANSITION_EVIDENCE"

# First approved dual session; keep the existing algorithms/models/prompts.
# Use the benchmark's reviewed numeric context if an override is needed.
AIHUB_GPU_DUAL_APPROVAL="$APPROVED_WINDOW_REFERENCE" \
MEETING_CONFIG_FILE="$APPROVED_MEETING_CONFIG" \
MEETING_SUMMARIES_ROOT="$PRIVATE_NEW_COMPARISON_ROOT" \
bash "$MEETING_ROOT/bin/summarize-existing-meeting.sh" \
  "$EXISTING_TRANSCRIPT_DIRECTORY" --dual-gpu-target meeting-dual --keep-recap

# Only after explicit resolution/inspection of uncertain work, with exact evidence:
"$REVIEWED_PYTHON" -m aihub_gpu_runner --config "$NEW_CONFIG" dual-restore \
  --job-id "$HELD_JOB" --evidence-file "$RESTORATION_EVIDENCE"
# Separately reviewed recovery evidence includes stored proof's switch_binding
# and true gpu1_restored/backend_ready/placement_verified, plus existing exact
# job/hash/lease/target and operator-approved quiescence/cleanup fields.
"$REVIEWED_PYTHON" -m aihub_gpu_runner --config "$NEW_CONFIG" recover \
  --job-id "$HELD_JOB" --evidence-file "$RECOVERY_EVIDENCE"
```

`AIHUB_GPU_DUAL_APPROVAL` must equal the policy's reviewed activation reference.
This is a procedural local gate, not network enforcement or a substitute for
actual operational approval. The default historical command and HA pipeline remain
normal GPU1-only. Explicit dual selection wraps the same entire summarizer session.

## Rollback decision points

- Before the scope transition, revert only the reviewed package/config delta while
  no work is admitted. Keep the original journal and hardware untouched.
- After a switch or uncertain work, keep both owners and exclusion. Resolve backend
  work, verify/restore GPU1 through the approved local path, then separately recover.
  Never install an older recovery implementation over unresolved dual owners.
- To remove the dual target after transition, stop the idle runner, resolve every
  nonfinal job/owner and use `transition-scope --rollback` with the new config as
  `--config` and original config as `--new-config`. Evidence must reverse the exact
  scopes and include `rollback_of` referencing the immediately previous transition
  approval. This appends a rollback record without restoring an old state backup.
- Verify original GPU1 placement/readiness, journal/lock continuity and normal
  single-GPU operation, then restore the approved prior runtime/config. Preserve
  outputs and all new historical records. Dashboard/music adapters and their private
  client states require no change. Cron restoration remains an explicit separate
  operator decision, not a rollback step.

Missing deployment hashes/UUIDs/calibration/permissions/approvals remain blockers.
No automatic fallbacks, permission repair, live inference or deployment occurred
in this checkpoint. Gateway, website and experimental whole-meeting extraction
remain outside its scope.
