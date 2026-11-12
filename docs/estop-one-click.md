# One-click administrative E-stop

One active, human HA administrator clicks **E-STOP**. HA forwards one authenticated
server request through Media. No Check, ticket, confirmation dialog, intent write,
browser pending record or required Verify. The response is a compact timestamp,
SUCCESS/FAILURE/UNKNOWN, released-lease count and actionable error code. A transport
loss stays UNKNOWN and never retries. Optional read-only status describes the last
record; it cannot replace the current browser outcome or rotate generation.

## Minimal authority

The existing `ownership.json` remains the only ownership authority. Schema stays 2;
the E-stop record format becomes 2. Current owners, policy/scope, original lock
identities and unrelated history remain in that journal. A stop prepares and fsyncs
two new generation lock files, then atomically writes the new generation, empty
owners and one `estop_last` record. There is no preliminary journal intent or second
commit. A crash before the authority rename leaves old ownership; after it leaves
the new generation. Orphan prepared files are harmless and are not automatically
deleted. No old physical `flock` is unlocked or replaced.

New-generation executor capacity replaces the old pool nonblocking. Queued old
tasks cancel; running tasks continue. Old callbacks cannot change new authority,
publish results or reclaim ownership. External observation/cleanup is never an
E-stop effect. Jobs/admission locks keep the established order; their five-second
contention bound reports a definite busy refusal. There is no three-second overall
transaction deadline. Slow/ambiguous filesystem commit produces UNKNOWN, not retry.

Old **nonterminal** jobs are failed/no-replay by current generation on reads and
writes, without enumerating historical jobs at stop time. Accepted settled history
remains readable. Writes to an existing older-generation record/result cannot
relabel it into the current generation. A terminal-but-still-owned (or missing or
untagged) owner needs a sparse revocation exception; otherwise it could look like
accepted success after forced relinquishment. That exception stores only binding
metadata, never payloads. At most two such entries can be needed per stop. Existing
exceptions are retained. This small necessary metadata can grow with exceptional
terminal lease revocations, **not** with ordinary history or queued/running jobs;
removing it would require rewriting originals or losing revocation precedence.
The operator-facing last-result record is replaced, not appended.

## One separately authorized compaction

`tools/compact_estop.py` defaults to dry-run; apply requires an explicit flag,
verified exact rollback copy/hash, stopped CPU supervisor and inactive native host
writer locks. The operator also verifies all helper/cleanup/native writer launch
paths are stopped and upgraded together. No mixed-version machinery is added.

Compaction drops only E-stop ticket/intent/snapshot history, retaining a compact
last result and sparse existing revocation bindings/hash. Every other authority
field, current lease/generation, job/result and original lock is preserved. It
reads only explicitly revoked originals, not all history. A narrowly checked
authority write accepts exactly this transformation and increments the existing
revision. No new journal, privilege policy, maintenance initialization or startup
migration is introduced. The separately verified rollback archive is not an
operational dependency. `recovery-control.json` is not rewritten, copied into new
authority or read by E-stop. Its independent ordinary-recovery behavior is unchanged.

After an uncertain conversion, inspect the authoritative format and exact bindings;
never repeat apply. Rollback to the archived authority is only admissible before
any subsequent native authority mutation/E-stop/new work, while all writers remain
stopped. After that point never restore old owners/generation; obtain review for a
forward correction.

## Runtime compatibility / deployment boundary

Update the CPU, native host SDK and fixed helper's pinned wheel together. They share
the maintained native package but have separate installed entrypoints. The published
helper template changes only reviewed wheel path/hash. Old incapable SDKs fail
closed against the new compact format; no old native writer may survive/relaunch.
The guarded meeting launcher and Music hook remain unchanged; Music keeps its
475 MiB/five-second policy and generation/original-lock trust. Preserved component
snapshots are historical source, not installation wheels. HTTP-only Media client,
ToDo sources/data/cache and ordinary cleanup correction remain unchanged.

Deploy the two HA bridge/card files and Media `recovery_routes.py` in the same
attended window. Ordinary Recover/Emergency confirmation/pending/Verify paths are
unchanged. Removed E-stop actions return unsupported rather than keeping obsolete
compatibility. No HA settings/credentials or GPU container operation is required.

## Reproducible qualification

- `test_estop.py`, `test_estop_one_click.py`, `test_estop_history.py` and existing
  worker-capacity tests: real journals, OS locks, running host/CPU processes,
  late raw writes, terminal publication, contention and process exits at atomic
  boundaries; no GPU activity or inference.
- `qualification/estop_history_timing.py`: production-shaped oversized E-stop
  maintenance snapshot, compaction, 4 versus 5,000 history records, two stops,
  unchanged originals, no history scan/gate read, immediate native admission and
  flat active size across normal repeated stops. Timings use real fsync/locks.
- `qualification/estop_ha_workflow.py`: actual disposable HA Core/auth/WebSocket,
  Media ASGI, runner and Chromium; no dialog/pre-check, real non-admin denial,
  two clicks, lost response, reload, no retry, original lock holder still alive.
- Existing Recover/Emergency/browser, process cleanup, generation, Music, host
  stage and Media/cache regressions remain applicable. Record installed import
  origins, errors and skips honestly. Synthetic observations are labeled.

Stop for independent review after builds/tests/publication. No production action is
authorized by this document. Live E-stop readiness and unattended ordinary cleanup
are distinct claims; this operation still does not stop physical GPU activity.
