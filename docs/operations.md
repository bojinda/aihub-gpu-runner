# Operations boundary

Current checkpoint: core dual-GPU meeting sessions and safe configuration-scope
transition, tested offline. Stop for operator review. The operator reports an
installed/bootstrapped AI-HUB runner, ready with no owners, and GPU1-only Ollama;
these are operator facts, not fresh remote observations. The dual delta is not
installed or executed by this checkpoint. Existing callers remain stopped/paused.
See [dual session deployment steps](dual-meeting-session.md). The earlier R2 plan
is historical planning; refresh its deployment assumptions before use.

Before eventual deployment, obtain separate host/window approval and validate
Linux locking/storage, existing lock paths/inodes/permissions, service account,
named backend versions/device placement, approved workflow hashes/mutations,
cleanup calibration and direct-client exclusion. No automatic permission repair.

The meeting helper now uses shared Admission through the local host-stage adapter.
This is an offline R2 slice, not deployment or unattended coexistence acceptance.
Dashboard uses the reusable job client and output-operation guard in this slice. Music now reuses the same client/output guard and separate named backend, with private persistent status and atomic next-song publication. Nothing is deployed.
Meeting stage identities now include selected approved aliases/corrections, and
the historical GPU1 launcher uses existing chunk indexes without WhisperX/audio.
These software corrections are tested offline and await review. Installation,
private state, shared physical locks, authenticated reachability, caller exclusion
and live installed-version/cleanup calibration remain deployment prerequisites.
Do not resume callers for offline tests.

The operator intentionally authorized the Docker-restart cron row to remain
disabled indefinitely on 2026-10-08, until an explicit restoration request.
There is no October 9 deadline or recurring extension requirement. Preserve the
original crontab backup and instructions; do not edit or automatically restore cron.
Evaluate GPU wedging after coordinated workloads operate. On a future restoration
request, verify the original row/backup, intervening edits, host timezone and
active/uncertain owned work before separately approved execution. Row hashes and
backup references remain private. Historical extension proposals are superseded.

Local CLI actions (future operational approval required):
- inspect: inspect/initialize a blocked local journal; no backend request.
- bootstrap --evidence-file: record approved initial quiescence/cleanup.
- recover --job-id --evidence-file: record approved recovery; no backend action.
- serve: start the authenticated API; high-entropy token required.

These commands do not install/restart services or automate backend recovery.
Do not delete state to fix a changed scope or bypass uncertain ownership.

`migrate-physical-locks` is a separate Linux-only maintenance command for two missing
physical locks and one retained unresolved GPU0 Comfy owner. It never recreates
files, activates config, modifies jobs or releases ownership. See the reviewed
[physical-lock migration design and operator procedure](physical-lock-migration.md).
Persistent physical locks belong under the private state directory, with explicit
mount ordering; `/tmp` and runtime directories are unsuitable for inode persistence.

The local `transition-scope` action is explicitly idle-only and additive for this
checkpoint: it appends history and updates scope under original paired native
locks, preserving ready/bootstrap/jobs/results. It requires an excluded/approved
window and refuses active supervisors, owners, nonfinal jobs or lock drift. Exact
`--rollback` reverses the immediately recorded transition without restoring an old
journal backup. No automatic config migration occurs at startup.

The local `dual-restore` action needs exact operator evidence of resolved work
and one specific restoration approval. It verifies/restores the original GPU1
placement with both owners held; it never releases them. Subsequent `recover`
requires stored restoration proof and exact approved recovery evidence. No API
mode-switch/recovery endpoint, automatic uncertain restoration or force release.

## Wallpaper node-95 transition (local development; deployment approval required)

`transition-wallpaper-workflow` is separate from additive `transition-scope` and
physical-lock migration. It only removes node `95` of class
`LayerUtility: PurgeVRAM`, or rolls back the immediately preceding forward
wallpaper transition. Canonical JSON comparison preserves every remaining node,
including model/settings scalar types, and rejects remaining links to node 95.
Only the wallpaper template path/hash may change. State and physical lock paths,
all other targets (including meeting-dual-ha), and the cleanup experiment stay intact.

The command acquires maintenance -> supervisor -> jobs -> admission -> both
physical locks, nonblocking. It checks ready state, zero owners, terminal jobs,
pathname and acquired-descriptor identities, frozen configs/templates, exact
scope/hash bindings, approval, and independent caller/backend evidence. It writes
only scope plus an audit entry in ownership.json using AtomicStore. It never
activates config, resets history, releases owners, or calls a backend.

### Proposed production procedure; do not execute without separate approval

1. Approve deployment of the reviewed runner build using the established package
   procedure. Verify its source hashes and that CLI help lists the new action.
   Keep automatic wallpaper generation paused throughout; do not restart yet.
2. Exclude participating API callers and host-stage launchers, let legitimate jobs
   finish, and verify backend quiescence/cleanup independently. Stop the runner
   service (`systemctl stop aihub-gpu-runner.service` in the approved operator
   console). Confirm inactive state. Any owner, nonfinal job, busy native lock or
   uncertainty is a stop condition, not permission to recover or clear anything.
3. Preserve private config/journal review copies. In the config directory, create
   a new 0600 byte-identical frozen copy named
   `config.wallpaper-before-no-purge95.json` of current config.json. Never replace
   an existing backup without review. Keep the pending config and both workflows.
   Use frozen inputs for all retries, including after activation.
4. Recheck workflow SHA256 values:
   - original: e22a71e66f267f9bb4ed3133ad201b9004b45e74ea07831622a1e9f61a55abfa
   - no-purge95: 7c523e807b014d76eb2468f11c2d7ec4859cd390103c6a045e261b205577bec5
   The October 21 read-only observation found old scope
   `4f193f35584a85bb2e65c8ad610a303a36cbdcea869a921ca88b2ddb88530500`
   and candidate scope
   `1e29c6c1b22a305eca2a5a885f7fd09bd83d8a2e63e056a53b56f40d58579660`.
   Recompute with load_config on the frozen files; refuse unexpected drift.
5. Write a private operator-reviewed evidence JSON file. Required string fields:
   `approval_ref`, `exclusion_evidence_ref`, `backend_evidence_ref`, `old_scope`,
   `new_scope`, `old_config_sha256`, `new_config_sha256`, `old_workflow_sha256`,
   `new_workflow_sha256`, `ownership_sha256` (fresh pre-transition journal bytes).
   The config hashes observed October 21 were respectively
   `5857ed4673763766cfbc8d3166735076bf47884d5ed67aca3e028b92e62aac04` and
   `da91eab9e882378cdd69ce409e0cda30e0ff44805598fb8371bd08783d895fcc`.
   Required booleans, set true only after documented verification:
   `operator_approved_wallpaper_workflow_transition`, `callers_excluded`,
   `supervisor_stopped`, `automatic_wallpaper_paused`, `backend_quiescent`,
   `cleanup_verified`. This task's development approval is not a production
   approval reference, and these field names are not evidence by themselves.
6. Execute the approved transition once, retaining the same evidence for any
   outcome-uncertain retry:

```sh
wallpaper_config_dir=/home/bojinda/.config/aihub-gpu-runner
wallpaper_runner=/home/bojinda/venvs/aihub-gpu-runner/bin/aihub-gpu-runner
"$wallpaper_runner" --config "$wallpaper_config_dir/config.wallpaper-before-no-purge95.json" \
  transition-wallpaper-workflow \
  --new-config "$wallpaper_config_dir/config.no_purge95.pending.json" \
  --evidence-file /path/to/approved-wallpaper-transition.json
```

7. Inspect the journal read-only: one new `kind=wallpaper_remove_purge95` audit
   row, candidate scope, zero owners, unchanged lock identities/bootstrap/recovery
   history and byte-identical jobs/results. Do not restore an old journal backup.
8. Atomically activate a copy of the pending config as config.json, keeping both
   frozen inputs. The following operator snippet works for forward activation and
   rollback by changing the source and evidence arguments. Run with the approved
   runner venv Python, from the same config directory; it checks the committed
   destination scope and the approved new config hash before replacing anything:

```sh
/home/bojinda/venvs/aihub-gpu-runner/bin/python - \
  "$wallpaper_config_dir/config.no_purge95.pending.json" \
  "$wallpaper_config_dir/config.json" /path/to/approved-wallpaper-transition.json <<'PY'
import hashlib, os, sys, tempfile
from pathlib import Path
from aihub_gpu_runner.config import load_config
from aihub_gpu_runner.core import strict_json
source, destination, evidence_path = map(Path, sys.argv[1:])
evidence = strict_json(evidence_path.read_bytes())
raw = source.read_bytes()
if (source.resolve().parent != destination.resolve().parent or
        hashlib.sha256(raw).hexdigest() != evidence['new_config_sha256']):
    raise SystemExit('activation_input_binding_mismatch')
_, _, state_dir, _, scope = load_config(source)
state = strict_json((state_dir / 'ownership.json').read_bytes())
if (scope != evidence['new_scope'] or scope != state['scope'] or state['owners'] or
        state['scope_transitions'][-1]['approval_ref'] != evidence['approval_ref']):
    raise SystemExit('activation_committed_scope_or_approval_mismatch')
fd, temporary = tempfile.mkstemp(prefix='.config.activate-', dir=destination.parent)
with os.fdopen(fd, 'wb') as stream:
    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
os.replace(temporary, destination)
fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
```

9. Run `"$wallpaper_runner" --config "$wallpaper_config_dir/config.json" inspect`
   and verify the expected scope/identities, no owners and preserved history.
   Start the runner only in the approved window, verify healthy startup, and keep
   automatic wallpaper generation paused. No image-generation test is authorized
   by this procedure.

### Guarded rollback

Repeat idle/exclusion/backend checks and stop the supervisor. Use the frozen
pending config as OLD and frozen original backup as NEW; generate fresh evidence
with reversed scopes/config/workflow hashes, a fresh ownership SHA256 and approval
reference, plus `rollback_of` equal to the immediately preceding forward approval.
The command refuses intervening scope transitions, rollback-of-rollback, drift,
active work or reused approvals.

```sh
"$wallpaper_runner" --config "$wallpaper_config_dir/config.no_purge95.pending.json" \
  transition-wallpaper-workflow --rollback \
  --new-config "$wallpaper_config_dir/config.wallpaper-before-no-purge95.json" \
  --evidence-file /path/to/approved-wallpaper-rollback.json
```

Verify the appended rollback row and restored scope. Use the activation snippet
with the original backup as source and rollback evidence; inspect and start only
under approval, still keeping automatic wallpaper paused. Coordinate Media's
original workflow restoration before any later live wallpaper test; this command
never changes Media. Both workflow scopes remain auditable in the same journal.

### Interruption/retry boundary

Journal and config are separate persisted objects. Before journal replacement,
old scope remains authoritative. After replacement but before config activation,
old-config startup refuses the scope mismatch. Identical evidence/inputs retry
verifies all idle/identity checks, fsyncs the journal/directory and returns
`already_recorded=true` without a second row. This is not a globally atomic
journal/config swap. On any ambiguous result keep service/callers stopped, inspect
both objects and use the frozen inputs; never delete state or bypass a guard.
