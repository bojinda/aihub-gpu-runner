# Ordinary Ollama idle-observation candidate

Offline candidate based on `0de270834c962e1d66485f89dfe9bbaaf69dbc8f`, including
the successful cleanup-budget correction and one-click E-stop. No production
mutation or historical lease retirement. Deployment is **HOLD under the present
unexcluded direct-client producer model**. Qualification establishes ordinary
cleanup behavior with competing producers excluded; it does not establish that
exclusion in production.

## Necessary evidence and removed machinery

The baseline `OllamaCleanupGuard.sample()` calls `probe_census()` before compute
observation, may call it again to correlate an unknown PID, and calls it after
physical sampling. Its correlation branch immediately raises
`ollama_cleanup_unknown_or_duplicate_gpu_process` when exact helper attestation
cannot authenticate the compute row. A separately attested PID cannot identify
the unknown one. The historical incident remains unauthenticated.

`ollama_probe.py` pins an image and executable/argv combinations, checks parent
birth, PID namespace, cgroup, and zombie transitions. These reconstruct process
identity. They cannot exclude another same-container client or prove physical
idleness. `ollama_diagnostics.py` and persisted helper attestations compound the
observation and journal cost without adding release authority.

The candidate deletes both modules and the enriched/correlation/final process
censuses. One plain census per service remains in a sample: it detects known
foreign compute and contradictory process lifetimes. One bounded in-memory
PID/birth map replaces exited, settling, pending-exit and helper-role state. Birth
changes remain hard refusals, including previously observed PIDs omitted by Docker.
The 64-lifetime bound is preserved. No helper has a trusted special role.

The pre-unload context is retained because existing result fencing, restart
preservation and manual recovery read it. The ordinary path still writes its
context, latest physical observation and sanitized failure through the existing
durable job store. Per-helper attestations, diagnostic event buffers and
service-memory attribution are removed. Service attribution is unnecessary for
acceptance when **every** leased-GPU compute row invalidates a sample. No new
journal, migration, schema, record type, recovery authority or producer service.

Container contracts/generations and independent name checks remain. Shared
`RecoveryObserver` interfaces and worker finalization are unchanged; those checks
also protect recovery operations that control containers. The existing final
fresh sample and atomic local ownership/deadline checks remain necessary.

## Lifecycle and proof

1. Acquire the existing GPU1 lease once and execute once.
2. Bind completed owned work, policy, boot, containers and resources; unload each
   owned model once through the existing backend.
3. Keep the same lease while observing within the original cleanup deadline.
   A missing child census read or any compute row, including an unidentified or
   zero-MiB row, invalidates the sample. Disappearance does not authenticate origin.
4. Require at least three consecutive complete idle samples: no leased-GPU compute
   rows, calibrated total memory/utilization, healthy physical UUID/driver, empty
   backend residency/queues, unchanged container generations and current ownership.
   Noncompute helpers do not have to disappear.
5. Publish the durable result once. Prepublication, postpublication and prerelease
   each take one fresh full sample. An inconclusive sample requires a new stable
   run within the same deadline; the healthy path remains six samples, not twelve.
6. Validate the binding and remaining deadline inside atomic ownership clear;
   release once. Until that succeeds, existing result access stays fenced.

Known foreign compute, duplicate leased-GPU rows, reused observed PID lifetimes,
container replacement, loaded competing models, invalid observations, GPU faults,
unavailable observations and uncertain persistence retain ownership. Persistent
compute/census ambiguity expires as `ollama_cleanup_process_settling_pending`;
utilization has its existing pending reason. Exhaustion before a complete
observation remains `backend_deadline`. There is no inference/unload retry,
automatic retirement, E-stop, new grace period, or ownership transition between
observations. Independent GPU0 Music may continue.

Manual retained-lease recovery eligibility is unchanged. The current failed lease
cannot be adopted or recovered automatically by installing this candidate.

## Producer limitation and smallest practical correction

The deployment exposes raw Ollama access outside runner admission. Open WebUI
connects directly to Ollama; AnythingLLM also has direct access. The runner lease
does not serialize those producers. `/api/ps` reports residency, not a complete
pending-request admission barrier. A direct request can be queued/loading without
being visible in one sample, or start after the final sample. Neither exact helper
identity nor arbitrarily many idle samples can establish exclusive handoff in that
model. No new PID is assumed to be the runner's request.

The missing guarantee is **no competing accepted, queued, loading or running
request, and no new direct submission from before coordinated execution through
ownership release**. Unattended acceptance under unrestricted direct access is
not demonstrated and is not recommended.

The smallest operational correction is a controlled exclusive window using
existing client/service and host access controls: finish/drain outstanding direct
requests, pause direct producers and prevent raw API bypass for the window, run
the coordinated workload, then restore the recorded access/client states after
successful handoff. Stopping the UI alone does not drain already accepted Ollama
work or block other raw callers. Empty VRAM is not a drain guarantee. Preserve
direct-client functionality outside that window. Automating or changing these
controls requires a separate concrete approval; none is implemented here. No
Aurora Gateway, orchestration daemon, or permanent network migration is proposed.

## Qualification and acceptance boundary

`tests/test_ollama_idle_release.py` uses the actual worker, Admission, Lease,
AtomicStore and native locks, with synthetic GPU/backend observations. It covers
the unidentified-row/different-diagnostic-child interleaving, persistent unknown
compute, foreign activity, competing residency, duplicate rows, PID reuse,
container replacement, contradictory health and turnover at every final boundary.
The test keeps the original lease and rejects a competing admission throughout.

Exact helper-role/zombie attestation tests and noncompute-child retirement tests
are retired with their implementation. Existing process-parser, native `/proc`,
driver/queue/health, persistence, ownership/result, host, Music, recovery, E-stop and
cleanup-budget regressions remain. Assertions that required immediate unknown-PID
failure or helper-exit records are updated to the new observation contract.

Reproduce the failure against the base by copying only the new test file and
running its `test_unknown_compute_then_disappears_requires_fresh_idle_and_one_release`
method. It fails after one sample with the old unknown/duplicate error and retains
the real lease; the candidate obtains fresh idle proof and releases normally.

Use the existing `qualification/run_installed_suite.py` and
`qualification/cleanup_wall_latency.py --expect released` against the committed
wheel. The latter uses seven-second real wall observations, 1,799 disposable jobs,
approximately 17 MB of synthetic authority, real persistence and locks. It proves
the full worker fits its 60-second budget without a twelve-sample regression;
hardware and backend observations remain synthetic.

Qualified implementation commit: `e5bab62b17d55f7077f1657d1d53a118153f05fe`.
Runner wheel version: `0.2.0+ollama.idle.20261113.1`; SHA-256:
`b33a6107e3aef286224f1f77c44216eb1300f97887f5a83cca1bef36a27aabfa`.

| Check | Result |
| --- | --- |
| Unchanged base source suite | 870 executions; zero errors/failures; one optional meeting-harness skip |
| Candidate source suite | 758 executions; zero errors/failures; same optional skip |
| Installed wheel, Python 3.12.12, guarded meeting harness enabled | 758 executions / 733 unique methods; zero errors/failures/skips/discovery errors; all 36 loaded native module origins checked |
| Recovery and one-click E-stop cards | 34 passing; zero errors/skips |
| Exact failure reproduction on base | One sample; unknown/duplicate error; original lease retained; zero publications |
| Same reproduction on candidate | Seven samples: invalid + three fresh idle + three final checks; one inference/unload/publication/release |
| Installed whole worker, real wall clock | 48.860 seconds, six seven-second observations, 1,799 jobs, 17,144,480-byte synthetic authority; 0.097 seconds measured persistence; success and ordinary release within 60 seconds |

Runtime production delta: **56 lines added, 489 removed, net −433** in
`aihub_gpu_runner/`. Including the two package/build version substitutions:
58 added, 491 removed, net −433. Core worker, lease/authority, shared recovery
observer and one-click E-stop source bytes are unchanged. The reduced suite count
reflects removal of obsolete helper-attestation tests and their imported duplicate
executions; it is not a subset run of the candidate suite. The first card attempt
lacked the pinned offline `fake-indexeddb` dependency; reusing version 6.2.5 resolved
test initialization without source changes. No GPU hardware or live inference was
used in any qualification.

Before any separately authorized deployment: review this producer limitation,
establish the exclusive-window guarantee, preserve failed lease/evidence/history,
install only the qualified artifact in the relevant maintained runner environments,
and read back hashes/settings/ownership without recovery. Keep both HA automations
OFF. Fresh ordinary Ollama and full Wallpaper acceptance must prove one generation,
one unload, publication and automatic release without E-stop. Any recovery of the
already retained lease is a separate exact operation; stop before doing it here.
Physical GPU behavior and unattended production stability remain unqualified.
