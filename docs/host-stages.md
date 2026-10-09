# Meeting host stages — R2 offline slice

The shell helper delegates to this package with `AIHUB_GPU_RUNNER_PYTHON`
(Python >=3.11) and `AIHUB_GPU_RUNNER_CONFIG`. The same configuration supplies
the API runner and host helper's private journal, scope and pre-existing physical
lock paths. Missing configuration/package, changed scope or mismatched lock
overrides fails closed; no old-flock/direct-call fallback is attempted by the
helper. Nothing is installed or enabled by this implementation.

`HostStage` reuses `Admission`, `NativeLock`, `AtomicStore`, `Lease` and the existing
local recovery path. One per-stage supervisor lock prevents duplicate execution;
it is not another GPU reservation system. The shared admission mutex covers
journal inspection, physical acquisition and durable claim before execution.
The API supervisor cannot overwrite independently supervised host-stage records.

Meeting/lesson wrappers retain GPU0 transcription, CPU chunking, then the GPU1
map/reduce stage. Their command vectors, model/context/keep-alive settings,
prompts, speaker logic, recap, output paths, audio policy and HA interface remain.
The host child does not inherit the resource descriptor. Stage requests borrow
the enclosing lease; they do not call the API to acquire the same GPU again.

## Stable identity and reconnect

`AIHUB_GPU_STAGE_ID`, when supplied, is a stable request ID for one GPU stage,
not a shared ID for transcription and summarization. Otherwise the helper derives
an ID from command/input/private settings hashes and admission scope. Wrappers
hash the recording or chunk index and existing private .env before admission.
Only hashes and request metadata go into job records, never tokens/argv/prompts.
Private source contents are not printed. The meeting helper declares the selected
approved alias file, approved per-turn correction file and existing chunk index.
Declared input identity includes content, opaque path hash and presence/absence.
Missing optional approvals remain missing; an explicitly selected missing alias
file fails before admission. A changed approved input derives a new automatic ID
or conflicts with an explicitly reused ID; old completed results cannot substitute.

Under the same lease, present alias/correction bytes are snapshotted using existing
private AtomicStore storage (directories 0700/files 0600). The child reads these
snapshots and retains existing approval/schema/source-binding checks. No approvals
or suggestions are invented. Source changes while waiting prevent launch; changes
during execution fail the stage after known completion/cleanup rather than record
cached success. Other prompt/template/code/recap context must remain stable during
a request; reviewed semantic changes need a fresh explicit ID. Status for an old
ID remains available even if its original audio was later deleted.

The supported `meeting-pipeline/bin/summarize-existing-meeting.sh` launcher uses
an existing nonempty transcript chunk index, normal map/reduce and GPU1 only. It
does not run WhisperX, require recordings or change audio deletion/HA behavior.
See the meeting README for its narrow flags and private comparison-output command;
an explicit comparison root wins over config. Same-ID completion reconnect does
not rematerialize missing application outputs.

Same ID/hash reconnects to recorded completion or waits for its active supervisor;
a duplicate's wait expiry does not cancel the owner. A changed hash conflicts.
Interrupted/uncertain/nonfinal records are not replayed, even when flock is free.
After recovery the old ID remains failed, with no automatic execution. A new,
reviewed request uses a new stable ID. This is not exactly-once backend execution
after ambiguous network I/O.

## Completion and cleanup

For GPU1, the meeting transport borrows exact stage/lease identity and requires
an active physical owner. Each Ollama request durably records intent before HTTP
and known `done=true` completion afterward. A lost reply or failed persistence
blocks later submissions and handoff, even if application code catches its error
or exits zero. Payload/model/options remain caller-owned and validated against
the configured target. Owned models are unloaded together at stage completion,
then empty residency is checked under the same lease. No per-chunk unload changes
the stage's keep-alive behavior. Only configured/inherited coordinated contexts
use this bridge; standalone legacy/manual direct clients must remain excluded.

GPU0 requires a calibrated host policy in the same trusted runner config:

```json
"host_stages": {
  "whisperx": {
    "idle_memory_mb": null,
    "cleanup_samples": 3,
    "cleanup_timeout": 60
  }
}
```

`null` deliberately blocks launching. Obtain an operator-calibrated physical GPU0
idle ceiling with both separate ComfyUI contexts present before deployment.
Unexpected residency blocks execution. A normal foreground process-group exit
and repeated idle memory observations are required before release, including a
definitive local command failure. Process exit alone is insufficient. SIGINT,
SIGTERM, SIGKILL, lingering descendants, ambiguous Ollama state or failed cleanup
retains the durable owner. A process-group stop does not prove a remote job ended.
Supervision assumes reviewed foreground WhisperX process behavior; detached
unmanaged clients still require exclusion, not inferred quiescence.

## Recovery and deployment prerequisites

Use the existing local `recover` action with exact job/hash/lease/target plus
explicit operator-approved backend quiescence/cleanup evidence. It records
recovery; it does not kill, restart, unload or resubmit. Confirm host descendants
and remote requests are resolved before attesting recovery after a dead helper.
Do not delete journal/jobs, unlink locks, change permissions or switch to direct
HTTP to clear a hold.

Live approval remains required for package installation, service/account/storage
checks, configuration/scope establishment, device placement, cleanup calibration,
backend-version verification, caller exclusion, inference and resumption.
Existing unconfigured manual/legacy clients are outside this managed boundary.
Meeting, wallpaper and music adapters are accepted offline; deployment and
live coexistence remain pending. The two meeting corrections await operator review.
Both ComfyUI services stay separate; no gateway, dual-GPU or website work occurs.

Rollback is not permission to bypass unresolved ownership. Under a separately
approved window, exclude submissions, resolve existing owned work, preserve state
and physical lock identity, then review reverting the adapter. No live R2
deployment currently exists to roll back.

## Explicit dual-GPU meeting session

Use `gpu0+gpu1` only with a dedicated configured dual Ollama target and matching
`AIHUB_GPU_DUAL_APPROVAL`. The historical launcher selects it with
`--dual-gpu-target NAME`; ordinary historical and HA runs remain GPU1 by default.
One existing shared Admission lease spans both original physical locks across
all map/reduce/recap calls. Only known-complete requests/owned unload can reach
controlled restoration. Verified original GPU1 placement/readiness/idle memory
precedes release; ambiguity holds both owners and requires explicit local recovery.
See [dual session operations](dual-meeting-session.md) and the deliberately
non-live-ready [target example](../examples/dual-meeting-target.example.json).
No gateway or whole-meeting algorithm is introduced.
