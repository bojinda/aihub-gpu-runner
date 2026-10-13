# Missing physical locks: bounded operator migration

Review only. No production action in this document has been executed. The local
`migrate-physical-locks` command is a development change awaiting independent
review and deployment approval. It is not an automatic startup repair.

## Architecture and trust boundary

The missing `/tmp` files cannot be inspected for their former identity. The
operation takes their recorded identities from the existing journal, verifies
both old paths are absent using `lstat`, and records that distinction explicitly.
It accepts only a prepared config whose sole change is `lock_paths`, using exactly
`STATE_DIR/physical-locks/gpu0.lock` and `gpu1.lock`. All targets, workflow bindings,
timeouts, state location, scope policy and restoration requirements remain intact.

Only one retained GPU0 Comfy owner in `reconciling` or `cleanup_pending` is supported.
The corresponding nonfinal job, lease, request hash, target, full owner and job
bytes must match the evidence; no other owner or active/uncertain job is allowed.
Additional unowned `waiting` API jobs for the same GPU0 target are permitted only
with no lease, phase, backend handle, completion or error, and exact evidence-bound
job hashes. They remain untouched by migration; existing Runner startup later marks
them `failed/restart_before_admission` without submission. Dual
sessions and host stages are refused. The operation never changes any job/result,
marks successful generation, releases a lease or invokes a backend. Existing
`recover` remains a separate operator operation with its existing requirements.

Operator approval must bind exact old/new scopes, config hashes, journal hash,
recorded old identities, provisioned new identities, owner, original job hash and
the exact set of unadmitted waiting job hashes (empty if none).
Explicit attestations and evidence references establish caller exclusion, backend
quiescence, original execution termination, original context absence and no
conflicting work. These facts cannot be inferred from acquiring a new lock:
an unlinked old inode can still have an open descriptor. The tool deliberately
does not assert that it independently inspected missing files or executed cleanup.
It uses the existing local operator-attestation trust boundary, with no API route.

The tool holds maintenance, supervisor, jobs, admission and both new native flocks.
New files must already exist, be distinct empty regular files with one link and
mode 0600, owned by the invoking account and group. State and physical-lock
directories must be mode 0700 with that account/group. Existing-file opens use
`O_NOFOLLOW`, never `O_CREAT`; path and descriptor identities are checked again
before commit. Missing files, replacements, contention and drift fail closed.

## Separate persistence boundaries

The tool never writes configuration. Keep immutable old and prepared new config
files in the active config's directory so relative template/path resolution stays
the same. Their scopes and bytes are rechecked immediately before journal commit.
One existing AtomicStore write appends an audit record and atomically changes only
the journal's scope and physical identities. Ready/bootstrap state and owners are
retained. Files and directory are fsynced by AtomicStore.

- Before journal replacement: old journal survives; missing old locks prevent start.
- After replacement, before config activation: old config cannot pass the new scope.
- If config is activated too early: new config cannot pass the old scope.
- After both: new identities govern admission; retained GPU0 still blocks work.
- Lost response after replacement: inspect the journal privately. With unchanged
  owner/job/configs/identities and the exact same evidence, an explicitly approved
  resume returns `already_recorded` and fsyncs journal/directory without duplicating
  history. Do not replace files, regenerate evidence or retry automatically.

After recovery changes the owner/job, migration replay intentionally refuses. There
is no automatic rollback. A failed/interrupted config activation leaves old or new
bytes using the atomic procedure below; resolve the recorded phase, never restore
an old journal or GPU configuration backup. SIGKILL tests model process crashes,
not VM power loss; ext4 atomic rename/fsync guarantees remain an operational premise.

## AI-HUB procedure, approval gates A–H

All writes, installation, service control, migration and recovery require explicit
operator approval. Current SSH authorization is read-only. Run as `bojinda` for
file creation, evidence and CLI operations; do not run migration as root. Stop at
any mismatch. Approval for migration does not approve recovery or inference.

```bash
RUNNER=/home/bojinda/venvs/aihub-gpu-runner/bin/aihub-gpu-runner
RUNNER_PY=/home/bojinda/venvs/aihub-gpu-runner/bin/python
CFG_DIR=/home/bojinda/.config/aihub-gpu-runner
STATE_DIR=/home/bojinda/.local/state/aihub-gpu-runner
JOB=stage-4820aaa2927510c1bc144045838c5e2da3e64a152aafdad3
OLD="$CFG_DIR/config.lock-migration-old.json"
NEW="$CFG_DIR/config.lock-migration-new.json"
```

**A. Approved exclusion and preservation window.** Stop the failing runner loop:
`sudo systemctl stop aihub-gpu-runner.service`. Exclude all participating API and
host launchers, direct Comfy/Ollama callers and schedules through their separately
approved controls. A stopped runner alone does not exclude direct submissions.
Verify service inactivity and no independent runner/host launcher. Preserve private
copies of active config, ownership, exact job and job/result hashes before changes;
do not print payloads, prompts or tokens. Record old scope, old identities and full
GPU0 owner. Obtain separate approval to install one reviewed wheel in the existing
venv using `pip install --no-index --no-deps`; do not edit installed packages by hand.
Verify source/wheel hashes, service interpreter and unchanged target bindings.

**B. Refresh independent termination evidence.** Record current boot ID, physical
GPU index/UUID mapping, wallpaper container exit state and absence of the old
Python process/context. Verify music queues, Ollama active requests/residency and
meeting/other GPU work, not just memory totals. Collect at least three stable GPU0
samples within the existing calibrated 1200 MiB ceiling, identifying every context.
Verify GPU1 has no conflicting work and retains its accepted placement. An exited
wallpaper backend can establish termination/quiescence, but not successful output
or backend availability. If anything is uncertain, stop; no inference or `/free` is
part of this procedure. Record private evidence references and an operator-authored
attestation file with all six ATTESTATIONS from the module set explicitly to true
only when independently established, plus `approval_ref`, `exclusion_evidence_ref`,
`backend_evidence_ref`, `gpu_evidence_ref`. Obtain an incident-specific migration
approval covering the exact owner and file bindings. Do not reuse a recovery approval.

**C. Approved exclusive provisioning and config preparation.** Confirm both old
paths are absent (including dangling links), the existing state root is private,
and the durable filesystem/mount is correct. Run the following only under file
provisioning approval. It never touches the missing old paths or overwrites a file.
If interrupted, preserve any partial new files for review; do not delete/recreate.

```bash
"$RUNNER_PY" - "$STATE_DIR" <<'PY'
import os, sys
from pathlib import Path
os.umask(0o077)
state = Path(sys.argv[1])
directory = state / 'physical-locks'
os.mkdir(directory, 0o700)  # Deliberately fails if already present; inspect first.
for name in ('gpu0.lock', 'gpu1.lock'):
    fd = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try: os.fsync(fd)
    finally: os.close(fd)
for path in (directory, state):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
PY
"$RUNNER_PY" - "$CFG_DIR/config.json" "$OLD" "$NEW" "$STATE_DIR" <<'PY'
import os, sys
from pathlib import Path
from aihub_gpu_runner.core import strict_json, encode
active, old, new, state = map(Path, sys.argv[1:])
raw = active.read_bytes()
value = strict_json(raw)
value['lock_paths'] = {r: str(state / 'physical-locks' / (r + '.lock')) for r in ('gpu0', 'gpu1')}
for path, data in ((old, raw), (new, encode(value))):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
fd = os.open(active.parent, os.O_RDONLY | os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
PY
```

Record actual new device/inode/UID/GID/mode. Review the config diff: only two lock
paths may change. Prepare/approve the supplied systemd mount-dependency drop-in;
it must name the existing persistent state path and must not create physical files.
No target backup restoration or repeat HA target migration is involved.

At the 2026-10-13 00:28 UTC read-only inspection, the original owner was unchanged
and one additional wallpaper job was `waiting`:
`stage-1b81b5595a84beaab6aa91537235f5948da33a0ac3f756f7`. It had no lease, phase,
backend detail, completion or error; created/updated at 2026-10-12 23:01:13 UTC.
Refresh this observation and explicitly include its current bytes in the migration
approval. Do not cancel/edit it or assume it represents submitted inference.

**D. Approved migration.** Bind the independent operator-authored attestations to
current metadata with the following evidence preparation. `ATTESTATIONS_FILE` and
`MIGRATION_EVIDENCE` are private approved paths; output creation is exclusive. This
script never sets a backend/exclusion attestation itself or reads old file identities.

```bash
"$RUNNER_PY" - "$OLD" "$NEW" "$JOB" "$ATTESTATIONS_FILE" "$MIGRATION_EVIDENCE" <<'PY'
import hashlib, os, sys
from pathlib import Path
from aihub_gpu_runner.config import load_config
from aihub_gpu_runner.core import strict_json, encode, lock_identity, FINAL
from aihub_gpu_runner.lock_migration import ATTESTATIONS
old, new, job_id, attestation, output = sys.argv[1:]
_, _, state, _, old_scope = load_config(old)
_, _, _, paths, new_scope = load_config(new)
raw = (state / 'ownership.json').read_bytes()
journal = strict_json(raw)
job_raw = (state / 'jobs' / (job_id + '.json')).read_bytes()
e = strict_json(Path(attestation).read_bytes())
assert all(e.get(k) is True for k in ATTESTATIONS), 'independent attestations required'
sha = lambda data: hashlib.sha256(data).hexdigest()
pending = {}
for path in sorted((state / 'jobs').glob('*.json')):
    raw_job = path.read_bytes()
    job = strict_json(raw_job)
    if job.get('state') not in FINAL and job.get('request_id') != job_id:
        pending[path.stem] = sha(raw_job)
e.update(old_scope=old_scope, new_scope=new_scope, old_config_sha256=sha(Path(old).read_bytes()),
    new_config_sha256=sha(Path(new).read_bytes()), ownership_sha256=sha(raw), job_sha256=sha(job_raw),
    pending_job_sha256=pending,
    owner=journal['owners']['gpu0'], old_lock_identity=journal['lock_identity'],
    new_lock_identity={r: lock_identity(p.lstat()) for r, p in paths.items()})
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as stream:
    stream.write(encode(e)); stream.flush(); os.fsync(stream.fileno())
PY
"$RUNNER" --config "$OLD" migrate-physical-locks --new-config "$NEW" \
  --job-id "$JOB" --evidence-file "$MIGRATION_EVIDENCE"
```

Immediately before this command, verify GPU0 lease
`6e628d9d9d29456caaf874470a99ee5d`, request hash
`5eb379a8491db0338f8ea32d2a2dc74b9d056f7ad5d6360026deba9fdc3735d1`, exact job, scope,
approval, exclusions and fresh physical observations. Run once. An error/uncertain
response requires phase inspection and separate approval before an explicit resume.

**E. Verify retained ownership, then approved config activation.** Inspect using
`"$RUNNER" --config "$NEW" inspect`. Compare owner to the preserved full owner;
check ready/bootstrap state retained, original job/result hashes unchanged, one audit
entry and both new identities correct. GPU0 admission is blocked by that owner;
do not test by submitting a production job. The old config must fail startup.
Activate prepared configuration only after these checks, using atomic replacement:

```bash
"$RUNNER_PY" - "$CFG_DIR/config.json" "$OLD" "$NEW" <<'PY'
import os, sys
from pathlib import Path
from aihub_gpu_runner.config import admission_from_config
active, old, new = map(Path, sys.argv[1:])
admission_from_config(new)  # Verify committed journal and new physical identities.
data, current = new.read_bytes(), active.read_bytes()
if current not in (old.read_bytes(), data):
    raise RuntimeError('active config changed; stop')
if current != data:
    temporary = active.with_name('.config.lock-migration-activate')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, active)
fd = os.open(active, os.O_RDONLY)
try: os.fsync(fd)
finally: os.close(fd)
fd = os.open(active.parent, os.O_RDONLY | os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
admission_from_config(active)
PY
```

Keep the service stopped. On interruption, compare active bytes with frozen old/new
and inspect journal with new config. Never overwrite an unexpected config or resume
with mutated evidence. Partial activation temporary files require operator review.

**F. Separate original-job recovery approval.** Refresh termination, queues,
exclusion, GPU context and at least three stable cleanup observations. Operator
authors a distinct recovery evidence file with `approval_ref`,
`operator_approved_backend_recovery: true`, `backend_quiescent: true`,
`cleanup_verified: true`, and exact `job_id`, `lease_id`, `request_hash`, `target`.
Do not claim terminal history or successful generation. Migration evidence is not
recovery evidence. Invoke the existing operation once:

```bash
"$RUNNER" --config "$CFG_DIR/config.json" recover --job-id "$JOB" \
  --evidence-file "$RECOVERY_EVIDENCE"
```

Verify original job `failed/operator_recovered_no_automatic_resubmission`, exact
lease removed, GPU1 still unowned and recovery record correct. Existing recovery
clears ownership and updates the job in separate writes: if interrupted between
them, stop for independent review; do not repeat recover or edit the job manually.

**G. Approved service restoration.** Install the reviewed mount drop-in, run
`sudo systemctl daemon-reload`, then `sudo systemctl start aihub-gpu-runner.service`.
Verify active service, stable identities/scopes, empty owners and API health through
approved read-only status checks. Verify any evidence-bound unadmitted jobs became
`failed/restart_before_admission` through normal startup, with no backend submission.
Keep callers excluded; submit no job. An exited
wallpaper container is not made available by this procedure and needs separate
backend authorization. Do not alter Ollama, music or GPU restoration configuration.

**H. Later separately approved real VM reboot test.** During an excluded, quiescent
window, record lock identities, scope, owners, filesystem and service state; approve
one controlled guest reboot and compare after startup. Verify mounts, identical
identities, retained journal and healthy service without inference. WSL SIGKILL and
simulated service tests do not certify a real guest reboot or hardware remapping.
