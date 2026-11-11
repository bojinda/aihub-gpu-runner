# Offline qualification and remaining release gates

This is an offline candidate, not a production rollout. The source reconstruction
and behavioral correction are separate commits on clean legitimate ancestry;
the umbrella audit commits were not merged. Operational originals, manifests,
raw logs and exact deployment identities remain in the private review packet.

## Reproduction and provenance

Use [qualification/README.md](../qualification/README.md), its pinned dependency
locks, the ordinary `tests/` directories and `tools/build_components.py`.
The builder reads a specified Git commit, emits deterministic wheels and records
each module digest and source commit in the wheel. Repeat builds and wheel RECORD
verification passed. The CPU runner, host SDK, fixed helper, Comfy plugin and
client SDK are separate components; they are not interchangeable deployment targets.

Installed module checks cover CPU runner 37 and helper 37 modules on Python 3.12,
host SDK 36 on Python 3.13, and client SDK 18 in a separate Media environment.
The 33-module Comfy plugin has its own isolated namespace and wheel. Actual Media
loads FastAPI's `recovery_media:app`; actual HA Core uses its required Python 3.14
environment. Exact versions/import origins and raw results are private manifest
entries. Follow-up test/documentation commits do not change the pinned runtime
build commit.

## Accepted integration coverage

| Suite | Accepted result and boundary |
|---|---|
| Full runner against committed installed wheel, Python 3.12 | 820 passing executions / 748 unique method definitions; zero skips, errors, failures or discovery initialization errors; loaded origins verified after execution |
| Media ordinary suite | 170 passing executions, 170 method definitions; includes 25-reference durable progress, 5,202 real records, tamper/mismatch/current-owner/late-submission refusals and unchanged ToDo/cache behavior |
| Meeting ordinary suite | 697 executions: 687 pass and 10 explicit optional private-fixture skips; real guarded launcher uses a network-denying disposable transport |
| Music application compatibility | 3 passes through actual Flask and separate SDK; busy/failed generation preserves existing promoted output |
| Card Node suites | 28 passes; baseline broken-behavior assertions are preserved separately and are not repair acceptance |
| Actual HA Core through Media to native runner | 2 passes using committed installed CPU/client artifacts; authenticated administrator/non-admin, exact confirmation, stale prerequisites and lost-response Verify |
| Actual Chromium card/IndexedDB through that chain | 1 combined scenario passes: older success during lost POST response, reload and another tab retain exact operation; one authority call/audit, no persisted credential |
| Actual Media through disposable CPU Comfy | 4 passes with installed separate client/plugin artifacts; no model or GPU inference |
| Actual disposable CPU Comfy/native lifecycle | 16 passes with separately installed plugin; tiny constant-image fixture, synthetic GPU observations |

These integration counts describe scenarios, not independent repeated evidence
of physical GPU safety. The browser uses a test page served by real HA, not a
complete installed production Lovelace dashboard. Real process/parser tests
exercise process lifetime, zombie/reaping, executable replacement and birth
conflict. They do not identify a vanished production PID.

## Honest limitations

- The helper's installed package/import hashes and CLI argument refusal qualify.
  Separate recovery-capture predicate tests do not execute the helper's capture or
  perform operations. Fresh root-owned configuration with real Docker container-init/cgroup
  capture and stop was not qualified in this disposable environment, which has no
  Docker executable. Generic helpers and synthetic rows do not replace that test.
- Initial discovery, fixture dependency, import bootstrap, browser library and
  recursive lock failures were retained privately, corrected where within scope
  and rerun. No initialization error is counted as a passing case. Historical
  raw-journal and old privileged-installer tests are private characterizations,
  explicitly excluded rather than imported as public fixtures.
- Linux settled-history fixtures fit the existing three-second Media budget;
  the largest Windows filesystem fixture exceeded it. Arbitrary archive size,
  stuck I/O and hard real-time latency are not claimed. Positive terminal-release
  receipts are revalidated against originals; negative 404 evidence is never a
  permanent exemption for a request that may appear later.
- Unknown/sparse historical process identity remains unsupported for manual
  retirement. No error-string allowlist expansion or automatic authority was
  enabled. The current class receives an explicit unsupported verdict, not an
  unexplained internal error and not a recovery ticket.

## Gate decision

G0 and G1 pass for source reconstruction and reviewed contracts. G2 passes for
the CPU/synthetic observation envelope; G4 passes for the qualified Linux and
actual browser/HA/Media path. G3 is partial: existing manual authority is improved
and qualified, but new authority for incomplete historical evidence is disabled.
G5 is HOLD pending that decision and the configured helper/container qualification
limit. Independent authority review accepts the changed source and retained-state
preservation design; it does not grant recovery, deployment or producer exclusion.

One private manifest and one attended procedure describe the bounded candidate.
That procedure stops before mutation while these prerequisites are unresolved.
No production restart, release, replay, schedule change or inference occurred in
this assignment. Installation, exact HA recovery and five eligible production days
remain later, separately authorized acceptance gates.
