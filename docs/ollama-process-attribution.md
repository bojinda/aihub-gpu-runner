# Ollama cleanup process identity investigation (2026-10-26)

Development base: `923e2a8ae8ef3572afa797fa338054aa82585045`.
Phase B is excluded. This change improves diagnostics only; it does not permit
new processes, change cleanup deadlines, or add backend requests.

## Incident and limits of the evidence

The operator supplied these facts for dashboard operation
`dashboard-reliability-20261026-01`, runner job
`stage-edc1128979f182a01d7b81ca286e22b5427fdf7c1171cb9c`:

- Inference completed. Context capture: `2026-10-26T18:55:31.136164Z`.
- Cleanup failed at `18:55:31.407031Z`, phase `service_processes`, with
  `ollama_cleanup_new_or_reused_process`; no completed physical observation.
- Main PID 2540 survived; captured model-runner PID 643865 subsequently exited.
- Second generation finished around `18:55:31.004Z`; apparent unload returned
  around `18:55:31.139Z`. Filtered logs show no obvious third generation.
- Subsequent GPU1 measurements were 1 MiB, 0% utilization, no resident models.
- The lease has already been separately recovered. Never resubmit/recover it.

No production inspection or action was performed for this investigation. The
supplied chronology is evidence, not a complete ingress or process-event audit.
The old failure record lacks the rejected PID and birth pair. It therefore
cannot identify the historical offending process or prove its purpose. Later
idle telemetry and absence of a model-load log cannot establish that purpose.

## Exact rejection mechanism

`prepare()` captures container generations and the Ollama PID/start-time map
before `backend.cleanup()` sends the owned model's keep-alive-zero request.
`RecoveryObserver.processes()` runs Docker `top <id> -eo pid` and reads each
host PID's `/proc/<pid>/stat` field 22 (start ticks). These are separate reads.

`sample()` checks lease/policy/boot bindings, container identities, empty backend
residency/readiness and driver health, then enumerates service processes. It
rejects any Ollama PID whose start ticks differ from the captured value,
including a PID absent from capture. This happens before compute-app/GPU
sampling and explains the missing physical observation. The reason combines
two cases: new PID and same PID/different birth. An unchanged main PID does not
distinguish them. `verify()` reports the failure; `Runner._work()` persists the
specific reason and marks the job reconciling without release or result proof.
The same diagnostic callback is used by host stages.

Previously verified captured-child disappearance is different: exact missing
PID evidence may discard a raced sample, followed by fresh complete idle
samples. It cannot authorize a child that was never captured. That protection,
physical-memory checks, utilization settling and three-sample proof are intact.

## Can normal cleanup spawn children?

Yes, in the upstream version recorded in the repository's deployment notes,
Ollama v0.35.1. Its tag resolves to
`b0c1ca4f7549d7acdfa52a7dcffc934bc63a43ce`. This is source-level evidence, not a
fresh attestation of the production binary/image.

The [generate handler](https://github.com/ollama/ollama/blob/b0c1ca4f7549d7acdfa52a7dcffc934bc63a43ce/server/routes.go)
requests expiration and returns an unload response. In the
[scheduler](https://github.com/ollama/ollama/blob/b0c1ca4f7549d7acdfa52a7dcffc934bc63a43ce/server/sched.go),
expiration closes/removes the model runner and waits for background VRAM recovery.
That recovery polls GPU discovery at a 250 ms cadence. Consequently an HTTP
unload acknowledgment and empty model list are not a child-process barrier.

[GPU discovery](https://github.com/ollama/ollama/blob/b0c1ca4f7549d7acdfa52a7dcffc934bc63a43ce/discover/runner.go)
tries existing runners and falls back to bootstrap discovery when necessary.
The [discovery implementation](https://github.com/ollama/ollama/blob/b0c1ca4f7549d7acdfa52a7dcffc934bc63a43ce/discover/llama_server.go)
starts short-lived model-free llama-server subprocesses, including device
listing. No third inference is required for this path. The roughly 268 ms
between reported unload and rejection is consistent with it, but does not prove
that this incident took that path.

The reviewed public sources are retained locally with SHA-256 fingerprints in
`review/upstream/`. Nothing from upstream was executed or imported into runner
runtime code.

## Why bounded convergence is not enabled

The currently available evidence binds a process to a container and kernel
lifetime, not to an authenticated request. A direct client can trigger discovery
or a new model runner under the same Ollama parent, executable and cgroup. A
direct request may start after an empty `/api/ps` check, or finish between polls.
Even parent/birth/executable evidence would not identify the triggering caller.
The deterministic fixtures reproduce both benign turnover and direct-client
interference with the same observable new PID/birth pair.

Waiting for all new children to exit, or adding a fixed grace sleep before
capture, would hide the distinction. Eventual idle hardware cannot retrospectively
prove that work belonged to the held lease. No new-child allowlist, retry,
recapture or convergence window is introduced. Unknown/reused processes remain
fatal even when models are empty and the GPU is idle. Existing sampling also
cannot guarantee detection of activity entirely between samples; this is an
existing direct-ingress risk, not an assertion that all interference is detected.

Before reconsidering convergence, a separately reviewed design must establish:

1. Enforced backend ingress isolation: all generation/load/unload requests pass
   through the authenticated lease-aware gateway, including host, container,
   LAN, IPv6 and alternate API paths. A voluntary caller hold is insufficient.
2. Positive drain/exclusion of previously accepted or queued direct requests,
   plus fail-closed behavior if the gateway or its durable request accounting
   restarts or loses evidence. Isolation alone does not authenticate old work.
3. Version-bound subprocess provenance: an authenticated backend lifecycle
   signal or a trusted process monitor correlating spawn/exit identity to the
   isolated cleanup epoch. Names, argv patterns or parent PID alone are not proof.
4. Offline and separately authorized physical validation of that contract. Any
   allowed child turnover would still use the existing deadline, discard
   incomplete observations, require fresh complete idle observations, and reject
   PID reuse, unknown compute, changed containers, residency/driver/storage faults.

These are recommendations only. No ingress/network/policy change is in this patch.

## Durable diagnostics

`ollama_cleanup_failure` retains schema 1, its existing reason and phase, and now
records the offending `pid` and optional `process_identity` comparison:

```json
{
  "pid": "305",
  "process_identity": {
    "comparison": "new_pid",
    "basis": "captured_ollama",
    "expected_birth": null,
    "observed_birth": "50"
  }
}
```

This example is synthetic. Birth values are kernel start ticks, not wall-clock
timestamps; interpret them with the existing context's boot/container bindings.
Comparisons distinguish `new_pid`, `birth_changed`, `duplicate_pid`,
`uncaptured_pid_disappeared`, `still_present` and `unknown_compute_pid`.
`basis` identifies the captured Ollama map, current service snapshot or duplicate
compute snapshot. Null means no available validated numeric identity; no extra
read is performed to guess one. `birth_changed` is a mismatch, not a claim about
why the kernel identity changed.

PID and birth values are bounded ASCII decimals; enums are fixed. No command,
argv, executable path, model, prompt, environment, token or raw exception text is
added. Phase changes clear stale comparison details. Both the guard's durable
callback and the worker's final failure update retain the comparison. If all
storage writes fail, durability cannot be promised and ownership remains held.
The authenticated HTTP status response is unchanged; inspect the job's existing
private durable record for the diagnostic.

## Offline validation and operational limit

New regressions use the actual Docker-top parser and birth reader with simulated
process and backend observations. They cover new child creation during unload,
overlap, disappearance before stat, main/child PID reuse, reuse after compute
sampling, ambiguous service identity, unknown GPU processes, direct-client
interference/residency, sanitized values, partial/total storage failure, reconnect
and disk-reopen/restart without replay. The complete Linux suite also retains
native CPU-only `/proc` exit and lock tests, container/driver/memory checks and
GPU0 Music coexistence. Tests never require a GPU or production backend.

Complete Linux command: `python3 -B -m unittest discover -s tests -v`.
Untouched baseline: 431 run, 430 passed, one existing skip (35.972 seconds).
Final: 447 run, 446 passed, the same one skip (35.295 seconds). All 16 new
regressions passed. The skip is the historical dual-launcher test requiring the
explicit guarded meeting harness; no new skip or baseline failure was introduced.
Full baseline and final output is retained in `review/attribution/` locally.

This diagnostic correction does not prevent a benign discovery child from
causing retained ownership. Recurrence remains possible until stronger
attribution is designed, reviewed and validated. Existing owners require their
normal separately authorized operator process; this patch cannot recover them.
