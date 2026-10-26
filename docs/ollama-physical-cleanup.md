# Owned Ollama cleanup requires physical idle

Offline review candidate against runner checkpoint `154511f`. Not deployed.

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
