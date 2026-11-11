# E0 — generation feasibility and proposed transaction, not an enabled E-stop

The operator has approved the semantics: global administrative relinquishment of
GPU0/GPU1 authority, regardless of running work, without backend/process action.
Overlap is accepted. No idle, cleanup, recognised failure, historical PID,
producer-exclusion or privileged-helper qualification is an E-stop eligibility
requirement. Ordinary recovery and physical safety reporting remain distinct.

This E0 branch contains a design and a non-deployable executable experiment only.
It does not expose an E-stop API, change runtime modules or qualify E1. The attached
roadmap requires a stop after E0; unresolved compatibility/crash questions below
must be reviewed before calling the full design sound or implementing E1.

## Source reconciliation relevant to this action

| Caller | Actual path/behaviour | Required successor treatment |
|---|---|---|
| CPU Runner | `Admission.try_acquire` reads `ownership.json`, then flocks configured paths under admission.lock; `_work` writes job/results separately | Select one authoritative generation; prevent stale phases/release/result acceptance |
| Independent HostStage | Same Admission, own jobs.lock and per-stage supervisor lock; keeps lease descriptors across child execution; HostOllamaClient checks owner and held lock before generate | Generation-aware host SDK, exact epoch binding through child environment, fail closed on revoked stage; no backend stop |
| Meeting/WhisperX launcher | `with-gpu-lock.sh` uses configured Python `run_module(host_stage)`, no standalone flock fallback | Pin compatible SDK/import provenance; do not silently use old configured Python |
| Host SDK36 / client SDK18 | Actual installed variants validate journal schema==1; old raw AtomicStore still writes jobs without reading admission | Schema2 rejects old fresh claims/release, but alone does not revoke raw result writes; authoritative overlay is mandatory |
| Privileged Wallpaper helper | Fixed wheel and root config pin original paths/inodes; compatibility compares against journal lock_identity | Generation-aware validated lock selection is needed before normal Wallpaper can remain usable after E-stop; no helper capture/perform required for E-stop itself |
| Music manual cleanup hook | Requires schema1, fixed config gpu0_identity, fixed `physical-locks/gpu0.lock`; permits bind same identity | Generation-aware compatibility is needed for continued manual cleanup; preserve enabled/475 MiB/five seconds and accepted original root binding |
| Recovery/maintenance | Separate recovery-control.json plus practical_control in ownership; active attempts/permits can fence scheduling | Epoch-bound authority/read paths required; preserve old attempts/permits, never mistake an old fence for current retained ownership |
| Worker capacity | Runner uses one bounded ThreadPoolExecutor; repeated relinquishment can leave all slots occupied by old work | Executor/future bookkeeping must be generation-scoped; retiring authority cannot require joining old workers or cancelling running work |

No current deployable generation selector exists in these components. The earlier
three-target manifest is insufficient. At minimum E1 needs CPU runner, native host
SDK, necessary launcher provenance, helper/cleanup compatibility, Media fixed route
allowlist, HA bridge and card. Whether pure HTTP-only client SDK18 must be upgraded
depends on its actual use: a job HTTP client need not become a native journal writer.
The direct-host functions in that SDK must fail closed after schema transition.

## Proposed single authority schema

`ownership.json` remains the only scheduler authority, with successor schema2:

```
schema: 2
scope, verified_boot, recovery_policy: unchanged accepted bindings
admission_generation:
  id: random 128-bit lowercase hex
  sequence: monotonically increasing integer
  parent: previous generation id (legacy generation0 descriptor initially)
  paths: {gpu0: .../generations/<id>/gpu0.lock, gpu1: .../gpu1.lock}
  lock_identity: exact dev/inode/mode/uid/gid for both files
owners: {} or exact owners bound to generation id
estop:
  attempts: operation -> signed binding, confirmed actor, intent, outcome
  audits: append-only operation/old owners/new generation/NOT_INSPECTED_OR_STOPPED
  revocations: job/request/lease -> original bindings + failed/no-replay disposition
```

Paths are derived from a trusted journal root and strict generation ID, never a
caller-supplied path. Original generation0 paths, metadata and inodes remain
immutable baseline evidence. Old files are never renamed/unlinked/replaced to
break flock. Each owner and queued request carries generation authority; E-stop
also retires pre-commit waiting requests so an old queued operation cannot execute
automatically in the new generation. Signed check binds the complete owner AND
waiting-authority set under jobs+admission, not merely a count.

The revocation overlay must outrank raw job/result/artifact files on every current
read: AtomicStore.job/jobs, Runner.status/result/artifact, scheduler/startup,
HostStage cached exit/generate, recovery summaries/finalizers/handoffs and proxy
reporting. Revoked jobs are failed/administratively abandoned with original data
preserved. No accepted success/value/artifact may escape through an old result.
New writers reject revoked lease/job authority. Legacy raw writes are retained as
untrusted late evidence and masked, not allowed to erase a revocation. The overlay
is in the same authoritative commit, not a separately authoritative registry.

Executor/future registries must also be generation-bound: create successor worker
capacity without waiting for old tasks, retain old executor references as historical
work and prevent their callbacks from altering current pending/future entries.
Never call a wait=True shutdown or cancel a running workload as an E-stop effect.
Repeated deliberate E-stops can accumulate continuing work; disclose resource cost,
do not turn an old executor's saturation into a false retained-lease report.

## Transaction and crash ordering proposed for E1

1. Dedicated E-stop GET Check/status only; authenticated active HA administrator,
   separate operate capability, explicit overlap warning and fresh one-shot signed
   ticket for action, actor, scope, boot, policy, generation, both current lock
   identities, complete owners and queued-authority binding. No backend reads.
2. Bound acquisition budget across the coordination chain shared with recovery,
   then jobs.lock before admission.lock. No physical flock acquisition, no worker
   wait/handoff, no GPU/Docker/helper call. Contended journal returns named busy.
   The exact complete lock order must be checked against every existing writer;
   see the open E0 proof below. A Python deadline is not a hard-real-time guarantee.
3. Revalidate ticket under mutexes. Persist durable intent with exact operation.
   If genuinely no current owners and no queued authority to retire, record a
   truthful no-op receipt without creating a new generation. Do not fabricate a
   freed GPU or rotate generations gratuitously.
4. Prepare a unique namespace using exclusive creation; fsync both new lock files,
   generation directory, parent and journal root. Hash/bind identities to intent.
   No old physical file is touched. Orphan prepared namespaces are inert evidence.
5. Atomic authoritative write switches schema/generation, clears both resources,
   retires all old leases/waiting authority and stores one audit/revocation per
   operation atomically. Paired leases have one disposition covering both resources.
   Former recovery fences/permits remain historical under their old generation;
   no current admission fence is silently inherited from a revoked operation.
6. Flush/validate authority durability before ANY successor admission or positive
   Verify. Atomic rename visibility alone is not the crash guarantee. Failure after
   rename/directory fsync uncertainty must not permit new inference until a reader
   under admission.lock has successfully made/verified the authoritative journal
   and namespace durable. No rollback/second E-stop is inferred from an I/O error.
7. Verify exact operation only after lost response. Persisted intent without a
   committed switch is unresolved/failed-before-commit, not permission to resubmit.
   A committed switch/audit is final even if old physical work continues. No
   automatic retries, second operation or old-success redirection in HA.

HA uses a dedicated named E-stop action, ticket mode and pending-operation record;
it must not repurpose the Wallpaper emergency control or an old Recover operation.
Check and Verify must remain read-only with respect to authority. A frozen old
Recover pending reference cannot be silently redirected to E-stop, nor can its
historical success clear the E-stop reference. E1 must specify how an explicit
global E-stop supersedes old fenced recovery authority without requiring the old
workload to become idle. Display logical ownership/generation and NOT ASSERTED
physical state independently, with the accepted overlap warning in confirmation.

Crash windows to qualify in E1: before/after intent fsync; each file creation/file
fsync; generation/parent fsync; ownership temporary write/file fsync/rename/root
fsync; audit readback; lost HTTP response; process restart at each boundary. Every
restart must select exactly the old or new committed generation, never a mixture.
Any unavailable/ambiguous authority blocks admission truthfully pending Verify;
physical execution remains NOT ASSERTED. No cleanup eligibility gate is added.

## Legacy, helper and cleanup compatibility

Schema2 is intentionally rejected by legacy Admission._validate, including live
objects rereading state and old binaries restarting. This was executed against
CPU37, host36 and client18. An installed old daemon must not be allowed to ignore
that error and initialize new state. Startup must never downgrade schema or
bootstrap a replacement journal. New claims only derive the current generation
while holding the common admission mutex; owner comparison includes generation.

This does NOT protect direct old AtomicStore.put_job/write_bytes calls. The E0 test
demonstrates that a late raw success is writable after revocation. Nor does schema2
by itself prove that every legacy ownership writer rereads/validates state inside
the common mutex. An older writer using a cached schema1 snapshot after the switch
could erase authority unless ruled out or fenced. That writer proof is mandatory.

The unchanged helper's root config cannot simply be repointed to every new inode.
The successor helper should validate its original trusted root/generation0 binding,
then derive strict current-generation paths/identities from the single journal and
recheck under the same mutex at effect time. Its fixed executable/wheel must be
separately pinned; it must reject schema/epoch combinations it cannot validate.
Likewise Music cleanup must derive the current GPU0 inode while preserving config
trust, per-generation permit checks, 475 MiB and five-second budget. Old hooks
should refuse rather than clean under an obsolete authority. Changing these
compatibility components is necessary scope, not an E-stop idle prerequisite.

## Executed feasibility evidence

`qualification/estop_generation_feasibility.py` runs in TemporaryDirectory only.
It imports the actual installed Admission, HostStage, NativeLock and AtomicStore.
The schema2 reader/transaction is an explicitly experimental adapter; it is NOT
installed in runtime and has no auth/API/UI. Synthetic setup uses no GPU, no
backend I/O and a fake dual-mode switch. Real CPU child processes, Linux flock,
atomic journal writes, file fsync and actual HostStage control flow are exercised.

Ten unique scenarios pass against three separately installed variants: CPU37 on
Python3.12, host36 on Python3.13 and client18 on Python3.14. These are 30 executions,
not 30 independent scenarios. For GPU0, GPU1 and paired resources, old holders
remain alive and original lock contention/inodes remain intact while successor
Admission acquires the new locks. Actual independent HostStage paths also pass for
all three resource shapes; their late marks fail schema validation without changing
fresh owners. Other tests show inert preparation, committed lost-response readback
and the raw-job late-write hole requiring the overlay. A saturated old executor
also keeps new tasks pending despite the journal switch; separate successor
capacity works without stopping old tasks. An initial paired fixture
lacked its required synthetic approval field; corrected fixture results are
identified rather than counting that failure as a pass.

These tests do NOT qualify signed tickets, audit exactly-once under concurrency,
every crash window, all current writers/readers, HA/Media/browser behavior, helper
or Music generation compatibility, or hardware/backend safety. There is no E1
feature implementation or ready deployment artifact yet.

## E0 stop and smallest required next change

The OS-held-lock obstacle is solved in principle without stopping the holder.
Full system E0 is still OPEN: schema2 plus a new inode is insufficient until the
legacy cached-write proof, complete revocation read/write precedence, executor
handover and the helper/Music generation trust contract are settled together. A three-file/API-only
patch would falsely claim new Wallpaper/Music compatibility and durable authority.

Review this expanded, source-backed compatibility scope and transaction before
E1. Next work is a bounded completion of those exact writer/read-path invariants
and generation-aware native/helper/cleanup protocol, followed by implementation
and the E1 matrix. No new idle/process-history/producer-exclusion condition is
proposed. The operator's E-stop decision is accepted; this stop concerns proving
its implementation, not reconsidering that authority.
