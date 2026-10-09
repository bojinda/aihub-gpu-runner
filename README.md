# AI-HUB GPU Runner — core dual-GPU meeting checkpoint

One host-side coordinator for meeting, wallpaper and music GPU stages: file-backed
jobs/ownership, existing persistent physical locks, named backend adapters and
an authenticated internal job API. No extra queue/database, business-logic rewrite,
Ollama gateway or website automation.

Status: dual-session delta implemented/tested offline for review. The operator
reports the runner installed/bootstrapped on AI-HUB, ready with no owners and
Ollama restored to GPU1-only. Exact deployed version/scope require approved
inspection. No live action was performed here. Keep callers stopped/paused.
See [dual session and deployment steps](docs/dual-meeting-session.md) for explicit
two-GPU sessions, controlled restoration and journal-preserving scope transition.
Normal API callers remain GPU1/GPU0; they cannot submit to the dual session target.

Meeting/dashboard/music slices are accepted for continued offline development.
The approved-input meeting identity and historical launcher corrections are tested
offline and await review. See the [bounded deployment proposal](docs/r2-deployment-plan.md);
no installation, live test or deployment is authorized by that document.
The music slice reuses the same job client and output guard, adding persistent
application status and atomic byte publication; this checkpoint does not deploy or modify callers.
See [music client](docs/music-client.md), [dashboard client](docs/dashboard-client.md)
and their still-open live/deployment gates.

See [roadmap.md](roadmap.md), [admission contract](docs/admission-contract.md),
[API](docs/api.md), [operations](docs/operations.md), and the unchanged shared
[SSH policy](../SSH_GUARDRAILS.md).

## Validation

Python 3.11+ standard library; no runtime dependencies.
Run: python -B -m unittest discover -s tests -v.
Tests use synthetic backends/private temporary state, not live inference. A native
CPU process test proves local OS exclusion and persistent lock identity.
Offline Linux/WSL admission checks have passed. Actual service-account, storage
and GPU/backend validation still requires separate operational authorization.

## Safety boundary

Durable ownership precedes submission. Lost responses, timeout, failed cleanup or
supervisor death block reuse even when the OS lock is free. Recovery uses local
operator evidence; the API has no force-release. Every participant must share
the admission contract.

The R2 meeting helper delegates to this package’s host-stage supervision and
shared Admission. See docs/host-stages.md for setup, idempotency and recovery.
Map/reduce, prompts, speakers and outputs are preserved. This slice is offline
and undeployed; live validation and global coordination remain pending.

Ollama cleanup is owned after known completion. ComfyUI retains GPU0 through its
prompt's terminal result, intended output retrieval and calibrated cleanup
observations; /free success alone is insufficient. Live calibration/installed-
version verification is mandatory before ComfyUI use.

## Configuration

examples/config.example.json is illustrative and not live-ready: synthetic
templates, example model allowlist, null ComfyUI idle ceilings. Do not deploy,
bootstrap live admission or submit jobs without separate approval.
Later use real existing lock overrides and privately approved models/templates.

API binding defaults to authenticated loopback. LAN exposure is a separate
deployment decision; no networking/firewall modification is performed.
Run from source if needed; no pip/network installation was performed.

## Review obligations

Review the offline R1 diff/tests, Linux/cleanup gaps and R2 admission boundary.
Keep callers paused. The operator intentionally authorized an indefinite
Docker-restart cron disablement on 2026-10-08, until explicitly requesting
restoration. Preserve the original backup/instructions; no automatic restoration
or recurring extension deadline. Evaluate wedging once coordinated work operates.
