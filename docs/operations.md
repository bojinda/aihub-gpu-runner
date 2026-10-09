# Operations boundary

R2 meeting, dashboard and music integrations are accepted for offline development.
Approved-input meeting identity and the historical summary launcher are tested
offline, pending review. The bounded R2 deployment plan is a proposal only; stop
for review before any live step. See [deployment proposal](r2-deployment-plan.md).
No service is deployed. Keep the operator's stopped/paused callers and running
ComfyUI containers; do not trigger jobs or change GPU configuration.

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
