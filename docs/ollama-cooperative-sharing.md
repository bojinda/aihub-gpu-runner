# Ordinary Ollama cooperative release

This offline candidate extends accepted commit `0ce9cc04` and preserves its
helper-attestation deletion. It supersedes the producer-exclusion HOLD in
`ollama-idle-cleanup.md` for ordinary GPU1 Ollama only. Dual-GPU switching,
container control, historical recovery and E-stop contracts are unchanged.

The lease serializes runner jobs. It is not exclusive ownership of every Ollama
request, shared model residency, or future GPU availability. Existing WebUI,
AnythingLLM and raw API producers continue using the same Ollama scheduler.

## Release outcomes

* Unknown/incomplete inference, unacknowledged expiration, uncertain publication,
  changed ownership/container generation, contradictory telemetry, GPU faults or
  failed durable writes retain ownership. No inference or uncertain unload retry.
* Completed runner inference plus acknowledged model expiration may relinquish
  its claim despite continuing GPU activity or model residency. The existing job
  proof records `release_outcome=completed_without_idle`,
  `expiration_acknowledged=true`, `completion_verified=true` and
  `cleanup_verified=false`. It contains no physical-idle proof. Result retrieval
  exposes that outcome and remains fenced until ownership actually clears.
* Fully acceptable observations retain the normal three-consecutive-idle proof,
  followed by one observation at each publication/release boundary: six healthy
  samples. A busy observation selects completed-only handoff when expiration was
  acknowledged. It never authenticates a PID or its historical origin. This avoids
  depending on a quiet interval, even for intermittent external traffic.
* Busy preflight before submit intent returns failed/skipped with
  `gpu_busy_not_submitted`. It uses the existing `never_submitted` release;
  cleanup is vacuous and no physical-idle claim is made. No inference, expiration,
  host-child launch or cleanup context is created. A completed ordinary host stage
  with no Ollama requests also needs no model expiration or global-idle proof.

The acknowledged-expiration path stops waiting for globally empty `/api/ps` in
`OllamaBackend.cleanup_models`. Legacy and dual-session callers retain their
existing behavior. Ordinary worker and host-stage release validate the current
binding under the existing admission mutex. For completed-only release, the
durable job proof and acknowledged context must match before ownership clears.
Publication and final consistency checks share the original cleanup deadline.
Deadline exhaustion before completing these operations still retains ownership;
external residency alone no longer consumes that budget waiting for idle.

## Why expiration does not require producer exclusion

An expiration request addresses the shared model, not a client-owned instance.
The inspected Ollama v0.35.1 scheduler's `expireRunner` changes its session duration
to zero; expiration handling defers unload while the reference count is positive.
An acknowledgment is therefore neither an unload-completion nor a drain proof.
No process killing, service restart, GPU reset or unloading of other models is
authorized by this outcome. Cold reloads, contention and generation failures
remain possible for either client. Mode switching or forcibly reclaiming shared
resources still requires its separate exclusion/control contract.

Sources: [model expiration](https://github.com/ollama/ollama/blob/v0.35.1/server/sched.go#L1728),
[active-reference check](https://github.com/ollama/ollama/blob/v0.35.1/server/sched.go#L419).

## Qualification

Use `qualification/cooperative_release.py` with the accepted installed wheel
(`--expect retained`) and this wheel (`--expect released`). It exercises actual
worker, durable store, admission, native locks and result fencing with synthetic
permanent external residency. `tests/test_ollama_idle_release.py` preserves the
historical unidentified-PID pattern, now permitting honest completed-only release,
and covers busy admission, persistence failures, final-boundary competition and
real host cleanup/release. Strict idle observer tests still require fresh
consecutive acceptable samples whenever an idle proof is requested.

Run the complete installed suite and `qualification/cleanup_wall_latency.py`
against the installed candidate. These are offline CPU-only tests. They do not
establish live coexistence or authorize deployment, lease recovery or scheduling.

Final installed qualification: 766 Python executions / 741 unique methods,
zero failures, errors or skips; 34 recovery/E-stop JavaScript checks passed.
All 36 packaged production modules match both checkout bytes and installed
module hashes. The healthy worker completed in 49.35 real seconds with six
seven-second observations, actual durable persistence/native locks, 1,799
synthetic historical jobs and a 17,144,504-byte authority file. It submitted one
inference, one expiration request, published once and released once within the
original 60-second budget. The previous transient-unknown-PID strict-idle
reproduction remains covered separately, including its fresh consecutive samples.

Against accepted `0ce9cc04`, persistent external residency reproduces
`cleanup_pending/backend_deadline`, zero physical samples and retained ownership.
The sharing candidate completes with four full observations and the explicit
completed-without-idle outcome; continued residency is never asserted idle.

Production changes are +133/-19 lines (net +114), excluding CRLF-to-LF
normalization in the touched cleanup module. The earlier -433-line simplification
is preserved; combined production size is 319 lines below its original baseline.
The additional code connects existing admission, release and result contracts;
there is no coordinator, request ledger, state migration or new worker.

Wheel: `0.2.0+ollama.sharing.20261113.1`, SHA-256
`c2e48831a72e58c458ffbc902d6928a47bf51662d1cc9dde4d8868e4ec21243a`.

Recommendation: offline source acceptance, followed by separately authorized
attended acceptance before unattended use. Readiness is a point-in-time check;
later contention remains possible. This correction prevents ordinary Ollama
residency/activity from stranding its completed lease. It does not relax the
separate paired-GPU Wallpaper container-control or dual-session safeguards.
No production inspection, mutation, lease recovery or scheduling change occurred
during this implementation and qualification.

## Read-only preflight failure correction

The bounded follow-up to `d302882` changes `submission_ready` to
`submission_failure`: the read-only helper returns a sanitized failure code or
None. Both ordinary worker and host-stage callers use their existing failed/skipped
and `never_submitted` release blocks for API/observation failures as well as busy
observations. All observation setup remains inside this read-only exception
boundary; failed-job persistence and ownership release remain outside it. No
submit intent, inference, expiration or host-child launch precedes this exit.
No physical-idle proof is produced. After submit intent, existing uncertainty and
retention handling is unchanged.

The accepted installed artifact reproduces worker `reconciling` and a raised host
preflight exception on transient API failure, retaining the unused claims. Ten
focused regressions exercise real worker/HostStage.run, native leases and durable
stores with synthetic observations: API loss and restoration, OS/parse errors,
failed failure-record persistence and uncertain ownership writes in both paths.
The correction releases only after successful failure-record persistence and
confirmed native release; it never retries release after a persistence exception.

Production delta: +19/-19 lines, net zero across three modules. The combined
319-line production reduction and all cooperative release semantics are preserved.

Installed follow-up qualification: 776 Python executions / 751 unique methods,
zero failures, errors or skips; all 34 JavaScript checks passed. All 36 production
modules match checkout, wheel and installed hashes. The real-time healthy worker
completed in 49.54 seconds with six observations and one inference, expiration,
publication and release, using the same full synthetic history and 60-second budget.
Artifact: `0.2.0+ollama.sharing.20261113.2`, wheel SHA-256
`bf9673c0ffba862416c78ebedbfdce79f4ea044870b0115807e95269cd38bd9d`.
No production or historical-lease action accompanied this qualification.
