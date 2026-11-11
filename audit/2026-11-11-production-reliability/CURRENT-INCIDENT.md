# Current GPU1 incident — retained, not recovered

- Job: `stage-52661113928ec71a6b02534e902870cf278adabd3677a97b`
- Lease: `c18921c93b1143be90975136a5678dc6`
- Request: `ce7432dda98f689cd9d023ce5fd39c18963c91fb3d44b6cded7950d3a368b849`
- Created November 11 08:01:31.530555 Toronto; failure persisted 08:01:48.040545. Seasonal stage, after weather succeeded.
- Native state: reconciling, execution_complete, completion_verified true, resources not released; authenticated status reports inactive supervision. Exact GPU1 owner remains.
- First audit capture: 09:30:14 Toronto. Job SHA256 `308a5a5709216a2ccf97c9a15936abf9564d305cce33c9a30a0aa2a9e0b028ff`.

## What actually failed

At 08:01:47.896, the diagnostic stream recorded GPU0 PID3171382 at422MiB, then GPU1 PID3645974 at256MiB. The two rows are not duplicates. PID3645974 had no captured birth, settling birth, positive-exit birth or probe attestation. The installed sample branch raises `ollama_cleanup_unknown_or_duplicate_gpu_process` when a leased-GPU row's PID is not in the service census or original completed-worker context. That is the supported branch attribution here; there is no evidence of a duplicate row or PID reuse.

The original cleanup context was captured at08:01:47.162498 with Ollama parent2029149/birth51373721 and model worker3645594/birth101225855. PID3645974 is different and unbound. The available failure diagnostics contain no executable, argv, birth, parent, cgroup, namespace, first/second census rows, or positive exit proof for3645974. Successful parent/census observations are not durably recorded on this branch. `failure.pid` is null because compute_apps resets the diagnostic phase without binding the unknown PID before raising.

By the first audit capture PID3645974 was absent from /proc, Docker's Ollama process list and the GPU compute list. GPU1 was1MiB/0%; only Music compute remained on GPU0 at422MiB. These establish current observations, **not the prior process's identity or safety**. A process starting between census and compute query, a short-lived missed process, or a stale GPU row is plausible. Actual foreign compute remains unexcluded. The historical process cannot be authenticated from the preserved evidence.

This is different from the earlier first-census zombie case: this job preserved no defunct row for3645974. It is also different from EXE truncation: no executable comparison was reached for this PID. The failure occurred under one second after the cleanup context capture, not at the60-second cleanup deadline.

## HA local lock timeout

The actual HA→Media→runner path reproduced `local_lock_timeout`. A separate bounded series returned that refusal3/8 times and unsupported_retained_failure_class5/8 times. Passive25ms /proc/locks samples (600 samples) show runner PID3524129 holding recovery.lock/inode22830767. During one entire observed timeout interval that was the only recovery coordination lock held; the permanent supervisor lock was also held as expected. The nonblocking IdleRecovery.exclusive chain acquires practical, recovery-control, maintenance and recovery locks; its error omits the failing path. RecoveryManager.tick holds recovery.lock across native finalization/lifecycle receipt scans and runs at least0.5 seconds apart. It can contend even though no inference is supervised.

This gives a confirmed live reproduction of recovery-worker lock contention masking the eligibility verdict, supported by an offline reproduction with the same installed lock/control code. No service restart or orphan lock was established. We cannot assign a captured thread/stack to the user's earlier08:01 UI timeout: no original traceback was retained, and the privileged07:55–08:10 journal read returned no entries. The audit distinguishes that historical limit from the later correlated reproduction.

Even without contention, this job is **unsupported**, because the manual idle recovery ERRORS allowlist excludes unknown_or_duplicate_gpu_process. No recovery approval was issued, no current recovery mutation was attempted, and no earlier operation was reused. Restoring a readable Check response alone would not authorize retirement.

See [current-incident.json](evidence/current-incident.json), [lock-correlation.json](evidence/lock-correlation.json), [operational settings](evidence/operational-settings.json) and the private evidence index.

Final readback09:58:39 Toronto: exact job hash and owner unchanged; runner/helper/Music/config hashes unchanged; Media sources and standalone receipts unchanged; original locks intact; both HA automations ON/current0 and both host paths active/waiting. No production restoration was attempted in this audit. The successful Nov11 ToDo receipt remains present; it is not currently pending.
