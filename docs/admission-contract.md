# Shared admission and recovery contract

Every participating GPU stage uses the same private ownership journal, scope
fingerprint and existing physical lock files on AI-HUB. Client/container-private
locks do not participate.

Under the admission mutex: inspect durable owners, acquire the physical resource
set without waiting while holding a subset, persist ownership, then submit.
Retain ownership through known completion, result retrieval and verified cleanup.
Uncertainty preserves reservations even if OS locks are free. Never unlink locks.

The API owns each request independently of its client. Client wait timeout or
disconnect cannot terminate supervision/release a resource. Same ID/payload
attaches; another payload conflicts. No exactly-once claim after ambiguous I/O.

The exported Admission gate is mandatory for every participating host launcher.
Descriptors are non-inheritable; lock identity/permissions must be preserved.
Production configuration requires existing physical lock files. Bootstrap records
device/inode/mode/owner identity, checked again before/after acquisition. Replaced
or missing lock files cause a hold; no alternative lock is silently created.

## Meeting boundary: R2 offline slice

The helper delegates to `host_stage` in this package, which owns one stage lease
through the existing Admission gate. Journal checking, physical acquisition and
durable claim are atomic under the shared admission mutex. There is no second
reservation system, standalone preflight or nested flock.

Local host stages retain stable job IDs and command/input/settings hashes; the
same ID reconnects to completed status or requires recovery, never replays
uncertain work. Every managed Ollama request records submit intent and known
completion under its enclosing GPU1 lease. Completion of the child does not
clear unresolved requests; owned model cleanup precedes verified handoff.
WhisperX remains a GPU0 foreground stage: process-group completion plus calibrated
physical-memory observations are required, and interruption retains ownership.
Runner API restart does not overwrite independently supervised host-stage records.

Only the helper/transport boundary changes; map/reduce, prompts, models, speaker
logic, recap, audio-deletion and HA behavior remain. All live deployment and
installed-backend/cleanup validation is still separately authorized. Other callers
remain excluded until their own reviewed integration; no global coordination claim.

## Local operator recovery

Fresh state is not-ready. Bootstrap records a matching bootstrap ID, approval
reference and verified quiescence/cleanup. It is local operator attestation,
not an automatic backend action.

Recovery requires exact job ID/request hash/lease ID/target plus operator-approved
backend recovery evidence. Active OS ownership prevents clearing it. There is no
remote force-release/bootstrap/recovery route. Local commands only record evidence;
they never restart, kill, unload or run a backend and never resubmit old work.

Deleting ownership, recreating locks, changing scope or falling back to direct
calls is not recovery. Corrupt/missing ownership with existing jobs blocks startup.

## Cleanup and limits

Ollama requires its owned non-streaming request to finish, then performs unload
under the same lease and verifies empty residency. Residency alone is not a
completion test. Warm retention is deferred; meeting configuration is unedited.

ComfyUI requires its prompt's terminal status and intended output. Foreign queue
work/unexpected GPU residency blocks submission. Backend-local free/unload is
followed by empty queue and repeated physical memory samples within a previously
operator-calibrated idle ceiling. HTTP success alone cannot release. Examples
with no ceiling cannot submit ComfyUI jobs.

This policy requires live validation against installed versions. It does not
prove custom cleanup-node execution or exclude races from unmanaged clients.
Baseline must include both separate ComfyUI contexts. Device placement is an
independent deployment prerequisite. Global coordination is not claimed while
bypass clients exist.

Linux uses flock plus file/directory fsync. Windows offline tests use byte locks,
file fsync and atomic replacement; they are not Linux/live-GPU evidence.
No FIFO, priority, preemption or power-loss/exactly-once guarantee is advertised.
