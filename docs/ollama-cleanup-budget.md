# Ordinary cleanup budget correction

Base: `c7c772b1d3c1d10fb436bd6a40382192a8b671b2`. This candidate changes the
CPU runner only. No protocol/schema/settings/policy change or automatic recovery.
Native host/helper/plugin/client snapshots remain separately represented and are
not installation targets. Music's accepted 475 MiB/five-second policy is unchanged.

## Mechanism and correction

The worker shares one target cleanup deadline across capture, owned unload,
stabilization, publication and native release. F05 changed three final checks to
unconditionally repeat a complete stabilization. With three required samples the
healthy path needs twelve rather than six full observations. At seven seconds per
observation twelve cannot fit a 60-second budget, even with entirely healthy data.

`commit_check()` now takes one fresh complete sample. Pending memory, utilization
or correlated process turnover invalidates the earlier stable proof and enters the
existing bounded stabilization loop. Identity/unknown compute/driver/persistence
errors remain refusals, never retries. No inference, unload or publication repeats.
The final external sample remains outside the admission mutex; local binding and
deadline validation still occur inside native clear. No lock ordering changes.

`RecoveryObserver.command()` preserves expired shared budget as `backend_deadline`.
Other failures distinguish timeout, nonzero return and OS error. Existing bounded
cleanup records retain only fixed command kind/service, bounded return/errno,
elapsed and remaining budget and the worker's verification boundary. No argv,
stderr, environment, payload or credential is recorded. Recovery authority and
eligibility lists are unchanged.

## Qualification

`tests/test_cleanup_budget.py` runs actual `Runner._work`, cleanup factory/guard,
native Admission/Lease/AtomicStore, disposable persistence and OS locks. Telemetry
and HTTP backend are **synthetic**. Its full-size case has 1,799 jobs and a nested
approximately 17 MB authority journal, nonzero command/HTTP/driver/persistence/read
costs and a seven-second full-observation cadence. Final pending cases cover all
three boundaries; unsafe compute and exhausted budget retain ownership/results
fences. Existing process, generation, publication, E-stop and host tests still apply.

`tests/test_observation_command.py` distinguishes no-launch expiry, real disposable
process timeout/nonzero/success and OS errors, and checks safe failure propagation.

For a real-wall installed-package reproduction (CPU only), run:

```sh
AIHUB_REQUIRE_INSTALLED_RUNNER=1 /path/to/baseline-venv/bin/python -I -B \
  /absolute/source/qualification/cleanup_wall_latency.py --expect retained
AIHUB_REQUIRE_INSTALLED_RUNNER=1 /path/to/candidate-venv/bin/python -I -B \
  /absolute/source/qualification/cleanup_wall_latency.py --expect released
```

This uses real wall-clock observation latency, native disposable history/locks and
persistence. It does not authenticate historical GPU processes or prove GPU idleness.
The baseline assertion expects retention; it is separate from desired-outcome tests.
Run the complete canonical installed suite with `qualification/run_installed_suite.py`
in a dependency-correct Linux environment, retaining import origins and unique counts.

## Evidence limits and production boundary

Saved incident evidence localizes failure after result publication without terminal
cleanup proof. The last saved observation satisfies captured GPU/service thresholds;
that does not prove every earlier sample passed. The old wrapper discarded the exact
failed command, remaining budget and boundary. Fresh read-only component timings do
not reconstruct historical lock waits/persistence. Detailed operational evidence
belongs only in the private packet, never in the public repository.

Stop for independent review. A later attended CPU-only replacement must preserve
the retained lease, all original jobs/results/receipts, generations, lock identities,
settings and ingress states. No schema activation, GPU container operation or replay.
Any current-lease E-stop is separately confirmed in HA after evidence capture and
does not count as cleanup acceptance. Schedules remain OFF until separately authorized
ordinary Ollama and fresh complete Wallpaper success/automatic release plus later
unchanged ToDo cache reuse. Five eligible production days remain pending.
