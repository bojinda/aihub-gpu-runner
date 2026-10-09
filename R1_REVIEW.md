# R1 offline review

45 test methods / 17 parameterized subcases passed on Windows.
This is local implementation evidence, not deployment or live-GPU acceptance.

Implemented: private atomic job/result records, native persistent OS locks and
recorded physical identity, mandatory shared admission, admission scheduler,
same-ID reconnect/conflict rules, known-completion/cleanup gating, retained
uncertain ownership, local operator-evidence recovery, named Ollama/separate
ComfyUI adapters, authenticated status/result/artifact API and constrained payloads.

Tests include: same-resource exclusion, different-resource overlap even with
GPU1 waiters, submission-time acquisition deadline, duplicate/concurrent retries,
client wait/disconnect, queued cancellation, lost response, backend error,
cleanup failure, supervisor death/restart, free OS lock with unresolved ownership,
storage write/clear failure before/after effect, replaced lock identity, native
second-process exclusion, workflow/output/path restrictions, API authentication
and request limits/invalid envelopes.

The prior mock restoration package and current meeting code are unchanged.
Operator-reported R0 restoration and paused-state details are retained privately.
No live SSH, deployment, inference, cleanup, caller resumption or cron edit occurred.

## Remaining acceptance boundary

- Run Linux flock/storage/service checks under separately approved scope.
- Validate calibrated ComfyUI cleanup against installed versions; examples
  intentionally have null cleanup ceilings.
- Review the R2 admission/transport boundaries. The legacy meeting helper only
  uses flock and does not yet honor the runner journal. Do not nest acquisitions
  or use process exit/timeout/residency as proof of backend completion.
- Preserve application business logic and map/reduce settings.
- Keep callers paused until the operator authorizes their next state.
- Review cron restore-or-extend at this handoff. Original row/backup is preserved
  by the operator; do not treat a temporary pause as permanent. Extended pause
  needs a dated follow-up, reason and owner.

## Deployment and rollback — no execution authorized

A service template is included for review only. Resolve permitted account and
existing lock access, private state storage, target/model/template constraints,
calibrated cleanup and fresh caller exclusion before deployment approval.
Bootstrap starts blocked and records operator evidence; it performs no backend
recovery. No automatic alternate lock, permission repair or direct-call fallback.

After uncertain submitted work, rollback cannot bypass the journal or silently
revert callers to direct transport. Keep affected resources blocked until approved
recovery establishes backend quiescence/cleanup, preserve state/results, then
review reverting the affected boundary. No service or application adapter has
been installed, so there is no live R1 deployment to roll back now.

This checkpoint does not accept live coexistence, R3, gateway/client migration,
advanced GPU switching or website automation. The manual-upload milestone remains
operator confirmation only; website details are not development prerequisites.
