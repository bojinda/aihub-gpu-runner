# Owned Ollama cleanup requires physical idle

Physical cleanup was introduced in Phase A (`93b26eb`, based on `154511f`).
The process-exit follow-up described below is an offline development candidate.

The normal API and GPU1 host-stage cleanup boundaries now capture a policy-bound
Ollama container generation and process birth identities before the existing owned
model unload. Empty `/api/ps` remains necessary, but callers retain the lease until
three accepted-policy physical GPU samples also prove idle. The configured cleanup
deadline covers capture, the existing unload and API polling, and physical sampling;
no additional grace period or threshold is introduced.

`ollama_cleanup.py` validates the exact job/request/lease/target/boot/resource
binding, policy digest/scope, physical lock identities, service contracts and
generations. It records the capture and sanitized GPU measurements using the
caller's existing job-update lock and durable store. Only already-captured Ollama
processes belonging to known-complete owned work may have their memory convergence
polled. Any failed memory sample resets the consecutive sample count.

Unknown/new/reused processes, activity on an unleased GPU, changed bindings or
containers, another service's excessive residency, nonempty/unverified expected
queues, GPU activity above the accepted idle limit, driver faults, observation
timeouts and storage errors retain ownership. These failures are not globally
reclassified as retryable. Neither `RecoveryObserver.gpu_sample()` nor wallpaper's
readiness/recovery exception handling was changed.

Routine GPU1 cleanup checks only its owned physical resource: GPU0 Music may
continue running. Stopped wallpaper HTTP is skipped; a participating running
wallpaper endpoint must be verified in always-on scopes. In on-demand scopes,
wallpaper must be stopped (PID 0) at capture; paired wallpaper ownership cannot
coexist with this Ollama lease. Running wallpaper fails before unload. Empty Ollama residency and backend readiness
are rechecked on each physical sample. The existing backend issues each owned
unload once; observation retries do not resubmit inference or repeat unload.

The guard is activated only by an accepted recovery policy containing a
`services.ollama` binding. Scopes with no policy or an accepted legacy policy
without that binding retain original API cleanup. The latter policy shape is
explicitly accepted by the validator and cannot provide physical Ollama proof.
Any policy containing the binding, including current production calibration,
must pass the guard; an invalid digest, boot, scope or binding never falls back.
Dual host sessions keep
their existing separately approved two-GPU cleanup/restoration verifier, because
the routine GPU1 container contract is not the active dual-mode contract. These
cases are explicit, tested scope boundaries, not new policy authorization.

The fix does not enforce raw API network admission. A process already present
when capture occurs is trusted under the existing owned-execution and caller-
exclusion assumptions; this is not proof that an unauthorized same-container
client never existed. A disappearing PID that cannot be safely attributed still
fails closed. Gateway/direct-client isolation remains separate work.

Validation: deterministic offline tests cover a 36.442 ms handoff, delayed memory
convergence, three consecutive samples, no duplicate inference/unload after lost
acknowledgments or reconnect, retained ownership on uncertainty, scoped GPU0
coexistence, process identity changes, normal host-stage wiring, legacy policy
compatibility and wallpaper already running at capture. The independent-GPU1
review regression passes on base, fails on the original patch, and passes with
the explicit legacy contract. Full Linux results are recorded with the review
artifacts. No GPU backend is contacted by the tests.

## Process-exit correction after Phase A validation

This follow-up is based only on `93b26eb`; the Phase B pre-start recovery change
is not included. Production remains on Phase A until a separate deployment decision.

The `phase-a-gpu1-once-20261026` incident retained a pre-unload context but no
physical observation or proof. Phase A lets exceptions during Docker process
enumeration, `/proc` birth inspection, physical observation or its durable write
escape into the worker's generic `supervision_or_storage_failure` handler. An
Ollama model child can exit normally after unload, between `docker top` and its
`/proc/PID/stat` read, or between NVIDIA's compute snapshot and the final birth
check. Both interleavings were reproduced offline. Unknown GPU activity and an
observation-write error also reproduce the same recorded incident signature.
No original exception class or traceback survives in the recovered job or service
journal, and the operator confirms only the generic error was captured. The
historical exception therefore remains unconfirmed; the child's later absence
and idle GPU do not uniquely identify the earlier failure.

The shared birth reader now validates the stat identity/start time and translates
only exact `ENOENT`/`ESRCH` reads into typed missing-PID evidence. Other observer
callers remain strict. Only this cleanup guard supplies an explicit missing-child
verifier, requiring a captured non-main Ollama PID, unchanged generation and lease
bindings, completed owned work and a second exact absence check. Permission errors,
invalid data, a live/reused PID, another service's PID and unknown children remain
errors. Previously verified missing PIDs are checked again before and after each
subsequent sample, even when absent from Docker and NVIDIA output.

A raced process snapshot or stale NVIDIA row for that exact absent child is
recorded with `process_snapshot_discarded: true` and resets consecutive stability.
Remaining rows and all existing queue, driver, container, GPU health and calibrated
memory checks still run. Persistent stale rows exhaust the existing deadline.
Only three subsequent complete samples can authorize handoff; no inference,
unload, recovery or service control is retried. An unknown PID or captured Ollama
PID on an unleased GPU still rejects cleanup immediately.

Failures persist `ollama_cleanup_failure` with a fixed reason code, probe phase,
allowlisted exception type, bounded errno and optional service/PID. API-worker
cleanup exceptions also use the specific sanitized reason as `error_code`; existing
`CleanupPending`/`Uncertain` states and codes are preserved. Guard failures use
the existing durable callback in both API workers and host stages. Exception
messages, commands, paths, response bodies and credentials are never copied into
this field. A failed diagnostic write cannot replace the original exception or
authorize release. If durable storage cannot accept any writes, diagnostic
durability cannot be guaranteed; ownership remains held.

The deterministic exit-race tests exercise both guarded interleavings, stale
enumerations, sample resets/deadlines, PID reuse, foreign activity, generation
changes, host stages, error sanitization and persistence faults. Two native Linux
tests use CPU-only subprocesses to exercise real `/proc` disappearance without a
GPU, Docker daemon or network. Hardware behavior of this correction remains
unvalidated and requires a separately approved controlled validation using a new
job. The already recovered incident must never be recovered or resubmitted again.
