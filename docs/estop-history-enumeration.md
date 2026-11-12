# E3 targeted history-enumeration correction

Production installation and E-stop remain separate operator authorizations. This
candidate does not activate schema 2, execute E-stop, or change ownership.

## Confirmed mechanism and evidence limit

A bounded read-only scan of the installed E1 implementation enumerated 1,798 jobs
against a 1,463,844-byte authority journal. It took 9.586 seconds wall / 9.586 CPU;
1,798 authority reads consumed 8.846 seconds. The journal hash stayed unchanged.
`Estop.locked()` permits three seconds including enumeration and preparation.
`AtomicStore.jobs()` called `job()` for every record; each call reparsed the full
authority journal to apply the current revocation overlay.

A disposable Linux fixture with the same record count, approximately the same
authority size, synthetic nested history, a real native retained GPU1 lease and
three previous uncommitted intents reproduced `estop_commit_deadline`: 7.774
seconds, prepared locks, zero commits and Verify `not_committed`. The original
HTTP refusal text was not retained in the available production logs. This proves
the reproduced mechanism and production budget overrun, not a recovered log of
each historical response. Synthetic records are not copies of private history.

## Smallest correction

`AtomicStore.jobs()` collects validated raw records and then reads authority once
to overlay revocations. Reading authority last masks a revocation committed while
earlier records were being collected. The existing single-job read and bulk read
share the same failed/no-replay overlay. There is no persistent cache, new index,
new lock, caller-provided authority, error allowlist or schema change.

Individual reads/writes and result/artifact publication fences are unchanged.
E-stop still owns jobs then admission locks; final commit still checks the same
three-second transaction deadline. No HTTP timeout or signing lifetime changes.
An actually slow durable preparation must still refuse without a commit. This
optimization does not promise unbounded history fits the fixed deadline forever.

## Qualification and reproduction

Build the runner wheel using `tools/build_components.py --commit COMMIT --out OUT
--component runner` from committed source. Install into an unused Linux venv.
Run its Python with `-I -B qualification/estop_history_timing.py` by absolute path.
The JSON report records loaded module hash, synthetic fixture size, read counts,
phase timings, commit counts and Verify. The same script runs against the previous
installed wheel to characterize the broken baseline separately.

`tests/test_estop_history.py` checks production-sized successful settlement,
preservation of original history/intents/prepared files, new admission while the
old native lease still holds its flock, one audit, duplicate rejection, bulk/point
view equivalence, concurrent revocation during collection, missing/invalid records
and the unchanged deadline's no-commit behavior. Run the full installed runner
suite as well: `qualification/run_installed_suite.py`, with
`AIHUB_REQUIRE_INSTALLED_RUNNER=1` and `AIHUB_QUALIFICATION_RESULTS` set. Existing
real-process contention, stale worker, crash and lost-response tests remain required.

## Proposed deployment boundary

Only the CPU runner needs this performance correction. Native host SDK, fixed
privileged helper/pinned E1 wheel, Music hook, HTTP client SDK, Media and HA retain
their E2 installations. Their protocol, journal schema, generation fields and
runtime behavior are compatible and unchanged; no indiscriminate wheel upgrades.
Use a new unused CPU venv and one reviewed systemd ExecStart override. Preserve the
current schema/generation, retained leases, prior intents/prepared directories,
original physical inodes, all settings and job/result history. No schema activation.

An attended procedure must first verify current identities and inactive CPU
supervision, preserve backups, briefly hold only recorded ingress, then stop only
the CPU runner and switch its venv. Never stop GPU workloads as part of E-stop.
Read back imported hashes and unchanged authority before restoring ingress. Any
uncertainty means read-only investigation, no repeat or journal rollback. A later
administrator's exact HA confirmation remains necessary for a fresh E-stop; old
Verify results and uncommitted tickets authorize no retry.
