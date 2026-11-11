# Offline qualification

Fresh run against source extracted from the currently selected production wheel: **297 unittest test executions, PASS**, 7.036seconds on local WSL Python3.10.12. No GPU access, production connection, mutation endpoint or service/container operation was used by this suite. Captured source was copied unchanged before testing. Count includes inherited fixture cases; it is not a claim of297 independently designed new scenarios.

Reproduce on Linux with Python3.10+ and procps:

```sh
sh audit/2026-11-11-production-reliability/qualification/run.sh
```

`qualification/tests/test_audit.py` adds six focused methods: unsupported class remains blocked without authority; holding the real recovery coordination lock masks that class then restores it after release; unknown later compute row fails immediately; duplicate known row yields the same coarse code; real fork/zombie/procps/positive-exit transition; and real separate-process flock contention/release. GPU rows are synthetic in guard tests, explicitly distinguished from the real process/OS tests. No test asserts that the historical PID was benign.

The included unchanged suites cover prior probe attestation and identity changes, birth/namespace/cgroup refusal, disappeared/stale children, utilization settling, native/physical locks, HA boundary checks, missing prerequisites, explicit confirmation, concurrent/stale tickets, lost-response verification and exactly-once audit semantics. Fault injection happens only in disposable fixtures. A lost response cannot authorize another mutation.

Live evidence in this audit exercises **read-only** authenticated HA status/check and Media dispatch. The earlier actual HA mutation successes and full HA disposable integration qualification are preserved historical evidence, not repeated here. No new live HA recovery acceptance is claimed for the unsupported current class.

The earlier first-census real-process reproduction and procps-width tests remain private artifacts referenced in EVIDENCE-INDEX. The current audit independently tests the live-exit observation and both unknown/duplicate guard branches. It cannot reproduce a vanished historical process or create a genuine NVIDIA driver row on a CPU-only fixture.

Tests passing do not establish unattended reliability: the current retained job is direct counterevidence to that claim.

The published package was separately rerun:297 executions PASS in7.184seconds. Actual card source tests:9/9PASS under Node24.19.0, including exact confirmation, refusal, uncertain response→Verify and emergency-ticket isolation. Run `node --test qualification/tests/test_idle_card.mjs qualification/tests/test_practical_card.mjs` from this audit directory. These use disposable DOM/transport fixtures, not a live mutation.

Media standalone suite:20 core intent/cache test executions passed; the ReportingTests class could not initialize in the local WSL environment because FastAPI is absent. That current-run framework-dependent coverage remains unverified, not counted as passing. The original installation qualification and prior live ToDo/cache acceptance are separately preserved. No dependency or production image was installed to hide this limit.

After fixture locator redaction, the same published suite again passed297 executions in8.435seconds. No source or production behavior was changed for this check. The failed initial Media framework import is retained privately as `media-tests.log`;20 core cases executed successfully before that class setup failure.
