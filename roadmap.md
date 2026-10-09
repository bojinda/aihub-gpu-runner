# AI-HUB GPU Coordination Roadmap

**Updated:** 2026-10-09 — core dual-GPU meeting session checkpoint implemented/tested offline, ready for review
**Status:** Planning document; no implementation, deployment, or publication is authorized by this file alone.
**Immediate priority:** Get meeting summaries, wallpaper generation, and wake-up music working reliably together.
**Next priority:** Add a compatible gateway for AnythingLLM, Open WebUI, and **Continue in VS Code**.

> **First milestone:** Meeting processing, wallpaper generation, and next-song generation cooperate reliably. The operator manually reviews, corrects, and uploads the meeting summary. Gateway work begins after reliable coexistence is accepted and the operator explicitly confirms the manual upload.

**Canonical location:** This roadmap is maintained in the runner repository. The shared Automations/roadmap.md points here; do not maintain divergent copies.
**Mandatory SSH policy:** [../SSH_GUARDRAILS.md](../SSH_GUARDRAILS.md), unchanged.

## 1. Scope and decisions already made

Keep the existing map/reduce meeting workflow as the production route. Whole-meeting evidence extraction remains experimental and disabled; it must not delay this roadmap. Keep existing speaker suggestions and approved corrections, but accept manual name edits in the finished document. Do not add more speaker-identification or within-turn editing features here.

**Website scope correction:** Website integration is out of scope for all current development and verification work. The operator manually reviews and corrects the summary, copies/pastes it into WordPress, and handles website access and publication checks. Codex must not request or require the website URL, page destination, membership configuration, credentials, or WordPress integration details. Missing website configuration must not block GPU coordination, meeting-pipeline recovery, or their acceptance checks.

The manual-upload milestone is complete only when the operator explicitly confirms it; Codex does not independently verify the website. This correction supersedes earlier website-detail requests and website-verification gaps in R0 reports. Historical reports remain evidence of earlier reviews, not current website acceptance blockers.

Automatic draft upload is deferred to **W1, the final phase**, after the runner, working pipeline, gateway/client migration, and advanced GPU coordination have been proven stable. Do not design or implement website automation now. Future automation must upload drafts for operator review and never automatically publish.

Keep the two ComfyUI containers separate because of their incompatible dependencies. Do not consolidate their Python environments or redesign the workflows. Preserve Home Assistant-facing endpoints, generation prompts, output paths, and the existing wake-up-song playback/promotion behavior.

Build one reusable AI-HUB-side GPU runner, provisionally `aihub-gpu-runner`. Applications submit GPU stages; the runner handles admission, execution tracking, cleanup, and recovery. Applications retain their business logic. A small internal job API is needed first; an Ollama-compatible streaming gateway is a later interface to the **same** resource manager, not a second scheduler.

**Resource policy for the first milestone:**

| Work | Resource | Policy |
|---|---|---|
| WhisperX | GPU0 | Preserve the existing transcription behavior; participate in shared admission/recovery. |
| Wallpaper ComfyUI | GPU0 | Hold the resource through actual backend completion and required cleanup. |
| Music ComfyUI | GPU0 | Same resource lock, separate named backend/container. |
| Routine Ollama, including meeting summaries | GPU1 | Physically restrict the routine Ollama backend to GPU1. |
| Prompt preparation and file composition | Neither | Do not reserve a GPU for CPU-only work. |
| Dedicated dual-GPU meeting Ollama | GPU0 + GPU1 | Core next checkpoint R2-D: explicit session target, atomic two-resource reservation, controlled activation and verified GPU1 restoration; offline first. |

The existing 96K Q8 single-GPU benchmark is useful capacity evidence, not a requirement to allocate 96K to every request. Preserve stage-specific model/context settings. The operator reports Qwen3.8:27b at 192K across both RTX 3090s, entirely GPU-resident. This is a core intended meeting use case, not an optional post-gateway feature. Preserve existing algorithms/prompts/models/context defaults; the benchmark is capacity evidence, not live coordination acceptance. CPU work inside a selected dual summary session retains both reservations until that session ends.

### Not prerequisites for pipeline recovery or the operator's manual upload

- Whole-meeting JSON/evidence redesign, new model comparisons, or more context benchmarks.
- Perfect speaker identification, biometric matching, or automatic diarization repair.
- An Ollama-compatible gateway or interactive priority scheduling. Core dual-GPU meeting coordination is separately authorized now; live activation remains separately approved.
- A queue database, distributed scheduler, new dashboard, or website-upload integration.

If producing a usable summary is blocked only by unfinished infrastructure, use a separately approved controlled GPU window and the existing map/reduce workflow. The operator reviews and manually uploads the result. Record the manual-upload milestone only on explicit operator confirmation; the integration milestone still needs its own coexistence tests. No website configuration or verification is required from Codex.

## 2. Starting point and evidence limits

The conversation reports working map/reduce minutes, two separate ComfyUI services, existing AI-HUB-local GPU locks, successful 96K Q8 operation on GPU1, and a successful dual-GPU benchmark. The supplied integration review identified asynchronous completion, cleanup, application concurrency, and crash-recovery gaps. It was a read-only review, not proof that the proposed runner is deployed.

**Latest operator-reported baseline (2026-10-09):** Runner installed and bootstrapped on AI-HUB, journal ready with no active owners, Ollama restored to GPU1-only. Routine Ollama uses GPU1; WhisperX/wallpaper/music use GPU0. Exact installed runner version/config/scope and hardware facts require fresh approved inspection before deployment. This report supersedes earlier undeployed/dual-exposure assumptions; a GPU1 lock does not constrain Ollama's device selection. Ollama can choose among visible GPUs or spread a model across them. [1]

Record actual application hosts, container versions, deployment mounts, backend addresses, lock paths/permissions, and repository commits privately. Development folder names do not establish where a service runs. Do not copy credentials, real meeting excerpts, participant rosters, or machine-specific GPU identifiers into public examples.

**SSH safety gate:** Read `SSH_GUARDRAILS.md` before any remote command. Earlier R0 access blockers were resolved using independently verified pins and permitted public-key authentication. Per-command SSH approval remains required. The operator reports that GPU1-only Ollama restoration passed its after-check and accepted the R1 Linux/WSL checkpoint and authorizes the R2 meeting admission slice with offline/synthetic validation. Codex must not auto-trust host keys, weaken SSH checks, modify `authorized_keys`, or change live services to work around access failures. Trusted access authorizes only the separately approved read-only inventory, not remote changes. If Codex SSH remains blocked, the operator may provide sanitized output from reviewed read-only commands executed through an already trusted console/session; direct Codex SSH is not required to establish R0. Keep target/account configuration private.

Suggested development layout, retaining separate repositories:

```text
projects/
├── meeting-pipeline/
└── Automations/
    ├── dashboard-generator/
    ├── daily-wakeup-song/
    └── aihub-gpu-runner/       # create during the runner checkpoint
```

Maintain this canonical roadmap in aihub-gpu-runner. The shared Automations/roadmap.md is a reference to it. Application documentation should reference this authority rather than copying it.

## 3. Checkpoint order

| Checkpoint | Deliverable | Status / dependency |
|---|---|---|
| R0 — Recovery baseline | Known production route, verified GPU placement, deployment/recovery notes | Operator accepted GPU1 restoration after-check and authorized transition to R1; other live validation is not inferred |
| R1 — Minimal shared runner | Reusable backend adapters, job tracking, admission and safe resource handoff | Offline implementation and Linux/WSL checkpoint accepted for continued offline development; not deployed |
| R2 — Integrate existing workloads | Wallpaper, music, and meeting/lesson GPU stages cooperate | **Current: music adapter slice only; meeting/dashboard accepted offline; stop for review after music** |
| R3 — End-to-end validation and operator confirmation | Reliable coexistence and usable meeting outputs; operator confirms manual upload | **Required gateway milestone gate** |
| G1 — Gateway implementation | Compatible interactive Ollama interface using the same coordinator | After reliable coexistence is accepted and the operator explicitly confirms manual upload |
| G2 — Client migration | AnythingLLM, Open WebUI, and Continue validated individually | After G1 |
| A1 — Advanced GPU coordination stabilization | Advanced coordination proven stable under separately approved scope | After G2; operator acceptance required |
| R2-D — Core dual-GPU meeting session | Atomic two-resource session, controlled CDI activation, verified GPU1 restoration and journal-preserving scope transition | Next checkpoint; offline implementation/testing only, then operator review and separate live approval |
| W1 — Automatic website draft upload | Drafts uploaded for operator review; no automatic publication | **Final phase only**, after runner, pipeline, gateway/client migration, and advanced coordination are accepted as stable; approved D1 work must be stable or explicitly deferred |

A passing test suite is necessary, but does not replace live workflow acceptance or the operator's explicit manual-upload confirmation for the gateway gate. Codex performs no website verification for that confirmation. Website configuration cannot block R0–R3 GPU/pipeline development or verification. Do not treat an earlier review's commit hashes as the current deployment state.

## 4. R0 — Recover a safe production baseline

**Objective:** Be able to generate a usable meeting summary now, without depending on experimental synthesis.

- [ ] Follow `SSH_GUARDRAILS.md`: independently verify SSH host trust before any new direct connection, or obtain operator-supplied read-only live evidence from an already trusted session. Authorize remote inspection separately; do not treat a Codex prompt as a technical read-only account.

- [ ] Record current branches/commits and tracked local changes; preserve private configuration and existing outputs.
- [ ] Check active backend work before any container change. With explicit operator approval, restore routine Ollama to GPU1-only if the dual-GPU test configuration remains active.
- [ ] Verify both ComfyUI containers' GPU0 placement and their existing output/cleanup behavior. Retain Q8/Flash Attention and existing model volumes unless a demonstrated incompatibility requires a change.
- [ ] Establish the current meeting's source index, approved aliases/corrections, map/reduce settings, and recap preference. Do not use unapproved speaker proposals. Website destination and configuration are not R0 requirements.
- [ ] Keep audio policy unchanged: successful transcription deletes the recording when configured; the previously agreed failure-recovery behavior remains. Historical work uses transcript indexes, not unavailable audio.
- [ ] Inventory direct Ollama/ComfyUI/manual clients. Until the gateway exists, keep those callers out of managed test windows; do not claim they are already coordinated.

**Exit evidence:** Recorded baseline, verified routine device placement, a known map/reduce invocation, and a rollback configuration. No new inference design is required.

**Rollback:** Restore the recorded configuration only after current backend work has been resolved. Do not erase model volumes, transcripts, or private results.

## 5. R1 — Minimal reusable GPU runner

**Objective:** Implement resource coordination once, with small adapters in its callers.

**Current authorization:** Implement/test the bounded R2-D dual-GPU meeting
session offline. All three R2 application adapters are accepted offline; the
meeting approved-input identity and historical command remain tested software
requirements, not live acceptance. The operator reports a ready/bootstrapped
installed runner with no active owners and GPU1-only Ollama. Do not deploy this
delta, restart services, change GPU configuration or production journal, run
inference, resume callers, modify cron, commit or push. Preserve current stopped/
paused callers and both separate running ComfyUI containers. Stop for review.


Preserve the existing map/reduce pipeline, GPU stage boundaries, models, prompts, speakers, recap, audio deletion and HA behavior. The accepted meeting boundary uses shared Admission. The dashboard and music slices use this repository’s reusable job client and CPU output guard; GPU ownership remains exclusively in the existing runner. No second reservation system, preflight/flock gap or nested GPU lock is acceptable. Offline implementation is not live integration acceptance.

**Intentional cron decision (operator, 2026-10-08):** Leave the AI-HUB Docker-restart cron entry disabled indefinitely, until the operator explicitly requests restoration. This supersedes the temporary extension/deadline policy; there is no October 9 deadline or recurring extension requirement. Preserve the original crontab backup and restoration instructions. Do not modify cron or restore it automatically. Evaluate whether GPU wedging improves once coordinated workloads are operational. A future requested restoration still requires the original backup/row, intervening-edit checks and review against active/uncertain owned work.

Deploy the runner as a supervised service on AI-HUB. Use file-backed job records/results and the existing physical-GPU `flock` files. Add authentication and configured/allowlisted backend targets. Do not accept arbitrary backend URLs, filesystem paths, or shell commands from clients.

### Minimum job contract

A request supplies a stable request ID, named target, and operation payload. The service owns backend submission and remains responsible after the client disconnects. Persist sufficient state before submission to recover an uncertain request.

The same ID and same payload reconnect to the same job/result. The same ID with a different payload is rejected. This prevents ordinary client retries from duplicating work, but does **not** establish exactly-once backend execution after an ambiguous network failure. Reconcile that failure rather than blindly submitting again.

Expose submit, status, and result retrieval. Report waiting, running, reconciling, cleanup-pending, success, failure, or cancellation distinctly. Resource acquisition, backend execution, and client waiting need separate deadlines. Queue order and priority are not guaranteed by `flock`; do not advertise FIFO, preemption, or deadline scheduling in this version.

### Backend adapters

**Ollama:** Own the request and required model cleanup within the protected stage. Keep application prompts, options, and parsing semantics intact. Remove late application-level unload calls that could affect a subsequent owner's work. Model residency and active inference are separate concerns; choose one explicit initial cleanup policy and test it. Warm-model optimization is not a prerequisite. Ollama provides model-retention controls, but request completion and safe handoff still need application-level verification. [1]

**ComfyUI:** Persist the correct backend and prompt ID; wait for that job's terminal result, not merely an HTTP submission response or the presence of an output file. Retrieve the intended image/audio output and verify required cleanup before release. Check the installed versions' queue/history/error behavior. Existing purge nodes stay, but their configuration alone is not proof of completed cleanup. [2]

### Non-negotiable recovery boundary

A local process lock disappears when its last associated descriptor closes; a remote/container job can outlive that process. [3] Therefore:

- Persist unresolved resource ownership before backend submission.
- Make a shared recovery/admission check mandatory for **all participating launchers**, including the meeting helper. A runner-only state file is insufficient.
- On restart or uncertain backend status, block admission to the affected resource until its old work and cleanup are verified, or an operator-approved backend recovery resolves them.
- Never infer safe release from a client timeout, elapsed time, an empty model list alone, or a cleanup request returning HTTP success.
- Never unlink/recreate the active lock files to clear contention. Preserve their shared identity and permissions.

This may require a small, separately reviewed meeting-helper change. Share the admission implementation or contract; do not create another recovery engine inside each automation.

**Exit evidence:** Offline tests for same-resource exclusion, different-resource overlap, duplicate requests, lost responses, client disconnect, backend failure, cleanup failure, and supervisor crash/restart. Specifically prove an unchanged-looking free OS lock cannot bypass unresolved backend ownership.

**Stop condition:** Do not integrate unattended clients until recovery behavior is explicit. A bounded operator-recovery path is acceptable; falsely reporting the resource free is not.

## 6. R2 — Integrate wallpaper, music, and meeting stages

**Objective:** Keep the useful workflows intact while replacing their GPU transport/supervision boundaries.

**Accepted first slice (offline only):** Meeting/lesson admission uses the existing shared journal and physical locks atomically. GPU0 remains WhisperX; GPU1 remains normal Ollama; map/reduce is preserved. Acceptance authorizes continued offline development, not live meeting deployment.

**Accepted dashboard slice (offline only):** Dashboard generation uses named GPU1/GPU0 runner targets and the canonical output guard, preserving HA/image/EPD behavior. Acceptance authorizes continued offline development only.

**Accepted music slice (offline only):** Lyric/tag generation uses named GPU1,
releasing it before the separate named GPU0 music target. Terminal completion,
expected MP3 and calibrated cleanup precede release. Stable client IDs, private
persistent status, archive/promotion before waits and atomic publication preserve
the existing song and HA interface. No scheduler/runner redesign is authorized.

**Current checkpoint:** R2-D core dual-GPU meeting session, implemented/tested
offline and ready for review. See [dual session and deployment steps](docs/dual-meeting-session.md).
The earlier [R2 deployment proposal](docs/r2-deployment-plan.md) is retained as
historical planning; its undeployed-runner assumption is superseded by the latest
operator report. None of this checkpoint's operational steps has executed.

**Meeting software pre-deployment requirements (implemented/tested offline; review pending):**

- [x] Stage identity binds selected approved aliases and per-turn corrections,
  including missing/present state and content. Private snapshots preserve the
  selected inputs; changed approved inputs cannot reuse old success. Missing
  approvals remain missing. Explicit missing selections fail closed.
- [x] Document/test `summarize-existing-meeting.sh`: existing transcript chunk
  index, runner-managed GPU1 map/reduce, no WhisperX or deleted recording required,
  preserved recap/redactions, private comparison output outside accepted outputs.

These checkmarks record offline software evidence, not operator acceptance of this
correction or a live deployment. Linux: 450 full-suite tests run, 449 passed,
one optional private-identifier audit skipped; final focused 13 passed; runner
45 tests/17 subcases passed. Mocked backends/native temporary locks only.

**Pending deployment gates:** Operator review of these corrections and the proposal;
approved canonical wheel/SDK installation on AI-HUB and both application hosts;
shared private admission storage/config/scope and original physical lock identity;
persistent private client-state mounts; authenticated internal connection and named
targets; installed-version backend/cleanup and physical placement calibration;
verified caller exclusion; individually approved windows and live test inputs.
No offline count closes these gates. Execute no deployment steps until separately
approved, then stop at each gate: runner → meeting → wallpaper → music → contention.
Do not resume stopped callers or alter intentionally disabled cron automatically.

### R2-D — Core dual-GPU meeting summarization

Dedicated explicit Ollama target: both physical GPUs under one shared Admission
transaction/lease for the entire existing map/reduce/recap session. Routine GPU1
Ollama and GPU0 WhisperX/wallpaper/music remain unchanged. No per-model-call
switching, nested reservation system, new scheduler or meeting algorithm/prompt
change. The target is local-session-only; routine API callers cannot trigger it.

- [x] Explicit trusted dual target/policy, calibrated physical bindings, pinned
  image/Compose definitions and approval reference; unconfigured targets fail closed.
- [x] All-or-none original native locks and durable paired owners across every
  request, model unloading, backend readiness and verified GPU1 restoration.
- [x] Interrupted switching, unknown model work, cleanup/restoration/persistence
  failures retain both owners; no automatic replay or restoration of uncertainty.
- [x] Explicit local operator restoration retains owners; separate exact recovery
  requires stored original-placement proof. Normal single-GPU contracts preserved.
- [x] Idle additive scope transition and exact rollback preserve ready/bootstrap,
  journal/history, jobs/results and physical lock identities; no reset/deletion.
- [x] Synthetic native Linux contention/interruption/cleanup/restoration/rollback
  tests and normal meeting/wallpaper/music regressions passed (private evidence).

Checkmarks are offline software/test evidence only. Before the installed AI-HUB
runner changes, approve fresh baseline bindings, caller exclusion, measured GPU0/
GPU1 idle/cleanup ceilings with both ComfyUI contexts, versioned package/config
delta and transition evidence. Stop idle supervisors/resolve nonfinal jobs before
the explicit transition; never overwrite current ownership with an old backup.
Approve live activation/inference/restoration and coexistence tests separately.
Actual 192K GPU-resident placement must be verified in that approved deployment.
Gateway, website and experimental whole-meeting extraction do not gate this work.

The inspected integration points below are starting locations, not permission to rewrite surrounding application logic. Recheck their current signatures before editing.

| Repository | Initial code surface |
|---|---|
| `dashboard-generator` | `app/main.py`: Ollama helpers, `comfy_render_image`, render admission/status; application config and Compose deployment settings. |
| `daily-wakeup-song` | `app.py`: `ollama_make_tags_and_lyrics`, `comfy_generate_mp3`, `generate_new`, promotion/publication guards; Compose settings. |
| `meeting-pipeline` | `bin/with-gpu-lock.sh`, meeting/lesson postprocess wrappers, and existing GPU-lock tests. Change only the shared admission/supervision boundary as needed. |
| `aihub-gpu-runner` | New shared service, backend adapters, recovery policy, small client interface, and focused tests. |

### Dashboard generator

Route all Ollama helpers, including separately invoked quote/to-do operations, through the shared coordination path. Route ComfyUI submission and completion handling through its adapter. Preserve workflow mutation, seeds, overlays, composition, EPD delivery, fallbacks, and output destinations.

Close the reported async admission race: reserve the active render slot before launching background work. Apply equivalent output-generation protection to the synchronous route. Preserve the existing HA-facing endpoints, including `/render`, `/render_async`, and `/render_status`.

### Wake-up song

Keep the existing song playable and promote/archive it before waiting for the next generation. `/swap_and_generate` must remain prompt; the background generation stages may wait for GPUs.

Route lyrics/tags and ComfyUI through the runner. Prevent overlapping jobs from overwriting the same next-song destination. Publish the new MP3 atomically only after successful retrieval and validation. Preserve genre selection, duration, vocal choices, lyrics fallback, and the separate music container.

### Meeting and lesson compatibility

Keep HA start/stop interfaces, transcription, chunking, prompts, speaker logic, map/reduce, and export rules unchanged. Integrate GPU stages with the **same admission/recovery policy**, using the existing host wrapper where practical.

Preserve GPU0 transcription followed by GPU1 summarization, with CPU-only work outside GPU reservations. There must be exactly one lock owner for each stage: do not nest a runner acquisition inside an existing wrapper that already owns the same resource. Define and test the handoff before adapting call sites.

For multi-request meeting summarization, retain the existing stage-level behavior unless a small shared adapter is required. Do not rewrite the summarizer into a durable workflow engine. Preserve lesson behavior and model settings while including lesson stages in contention tests.

**Exit evidence:** Each application passes its existing tests plus adapter tests; HA contracts and output paths are unchanged; GPU1 is released before wallpaper/music wait for GPU0. No duplicated supervisors, workflow-node rewrites, or dependency consolidation.

**Rollback:** Revert the affected application adapter and pause conflicting automated GPU triggers. Never silently bypass the runner after an uncertain submission; manual recovery is required first.

## 7. R3 — Integrated run, human review, and operator confirmation

**Objective:** Prove reliable meeting, wallpaper, and music coexistence and produce usable meeting artifacts before beginning gateway work.

Run one controlled integration exercise using approved live-test inputs. Use the existing October transcript for historical summarization; audio is not needed. Use a separate comparison output destination until the operator accepts the draft. Website integration and website verification remain outside Codex's current scope.

### Acceptance checklist

- [ ] Meeting map/reduce produces usable summary, minutes draft, action items, and QA with normal configured models.
- [ ] Wallpaper and next-song jobs can be triggered while meeting processing is active; they wait only for resources they need and complete without duplicate backend submissions.
- [ ] GPU0 jobs serialize; GPU0 and GPU1 stages overlap when compatible. Both automated ComfyUI services remain separately deployed.
- [ ] Current wake-up audio remains available while generation waits or fails; wallpaper retains its established failure/fallback behavior.
- [ ] A disconnect/restart exercise demonstrates that uncertain backend work does not cause premature resource reuse. No stranded reservation remains after verified recovery.
- [ ] The operator reviews substantive accuracy, names, motion outcomes, action ownership, recap separation, and redaction. Manual document edits are acceptable.
- [ ] Prepare only reviewed member-facing artifacts for operator handoff. Keep private transcripts, QA, suggestions, corrections, redactions, model responses, recordings, and job records out of that handoff.
- [ ] Record accepted commits/configuration, private test-result locations, and known non-blocking limitations.

### Operator-owned manual-upload milestone

The operator manually reviews, corrects, and copies/pastes the approved summary into WordPress, and handles all website access and publication checks. Codex needs no website URL, page destination, membership settings, credentials, integration details, screenshots, or independent access-test evidence. Mark this milestone complete only when the operator explicitly confirms the manual upload.

### Hard milestone gate

**Proceed to G1 for Open WebUI, AnythingLLM, and VS Code Continue only after BOTH:**

1. Meeting summarization, wallpaper generation, and wake-up music cooperate reliably under the first-stage resource policy, with operator acceptance of the integration results.
2. The operator explicitly confirms the manual website upload.

Tests, a local artifact, or a simulated upload cannot supply the operator's confirmation. Website configuration and Codex website verification are not acceptance requirements. GPU coordination and meeting-pipeline recovery continue independently of website configuration; only the gateway transition waits for the two confirmations above.

**Rollback:** Restore the last working controlled workflow, preserve operator-reviewed meeting artifacts and the previously playable song, and resolve backend ownership before releasing resources. Website actions remain the operator's responsibility.

## 8. G1/G2 — Interactive gateway, after coexistence and manual-upload confirmation

**Objective:** Bring interactive clients into the same coordinator without modifying their source code or replacing the automation job API.

Add an Ollama-compatible interface in front of the existing resource manager. Inventory the actual calls used by the installed clients before fixing the endpoint scope. Preserve streaming/non-streaming response formats, errors, request cancellation, model options, and any tool/image payloads actually used. Native Ollama streaming uses newline-delimited JSON; a custom job-status payload is not a compatible replacement. [4]

Do not expose unrestricted model-management operations simply because an API path exists. Bound request size, context, wait time, and concurrency; use authenticated clients and allowlisted model/backend policies. Metadata reads should not reserve a GPU.

**Client migration order: one at a time, each with rollback.**

| Client | Verification scope |
|---|---|
| Open WebUI | Chat, streaming/cancellation, model discovery, and separate local embedding or background-generation connections if configured. [5] |
| AnythingLLM | Ollama chat plus its independently configured embedding path if it uses AI-HUB; check background document indexing. [6] |
| Continue in VS Code | Audit each configured local model role: chat, edit/apply, autocomplete, and embeddings where present. Point only AI-HUB-backed roles at the gateway; do not assume all roles use one endpoint. [7] |

Continue autocomplete needs a short waiting policy and cancellation of obsolete requests. Background generation may wait longer. Interactive priority is a later explicit policy, not a promise to interrupt an active GPU task. Retain warm models only when safe for the next admitted workload; an open chat session does not own the GPU indefinitely.

On disconnect, cancel queued work where appropriate. Once backend inference has begun, resolve its state before handing resources to an incompatible request. Preserve existing client timeouts or document deliberate changes.

**Gateway acceptance:** All configured local generation/embedding paths use the coordinator; streaming and cancellations behave correctly; meeting, wallpaper, and music regression tests still pass. Restrict direct backend access only after migrated paths and administrative recovery access are verified. Do not call the system globally coordinated while bypass paths remain active.

## 9. A1 — Later advanced GPU coordination

Core dual-GPU meeting sessions are now R2-D, before gateway work, and must become
stable with the normal meeting/wallpaper/music workflows under separately approved
live tests. They are not an optional feature deferred until gateway stabilization.

Other advanced policies remain later and separately scoped. After the gateway and
interactive migrations stabilize, record acceptance of any approved advanced work
without changing the accepted core session/admission/recovery behavior. Do not
reopen experimental whole-meeting extraction or expand this checkpoint.

## 10. W1 — Automatic website draft upload, final phase only

**Deferred final phase:** Begin only after the GPU runner, working meeting/wallpaper/music pipeline, Ollama gateway and Open WebUI/AnythingLLM/Continue migration, and advanced GPU coordination have been proven stable and accepted by the operator. Core R2-D dual-GPU meeting operation must also have accepted live stability.

Future automation must upload **drafts for operator review only** and must **never automatically publish**. The operator retains review, correction, redaction, and publication control.

Do not design, implement, gather website integration requirements, request credentials/access, or perform website verification for W1 now. Its details and validation scope require a separate authorization when this final phase is reached. Earlier manual-upload confirmation authorizes gateway progression, not website automation.

## 11. Codex execution and scope-control rules

At each checkpoint, read this roadmap, report the current repository/branch/status, state the specific files and acceptance tests in scope, and implement only that checkpoint. Stop on unrelated local changes or missing deployment facts; never infer live state from development layout.

For **all SSH or remote actions**, follow `SSH_GUARDRAILS.md` as a mandatory companion policy. Never resolve access errors by relaxing host-key verification or using new credentials/accounts without explicit operator authorization. Remote inspection and live modification are separate approvals.

Run focused tests during implementation and full affected suites at the checkpoint boundary. Use synthetic public fixtures and mocked backends; perform live HA, Ollama, ComfyUI, SSH, and container operations only with explicit operator authorization. Website development, integration, access/publication checks, and configuration discovery are outside all current phases; the operator handles the website. Preserve actual runtime failure evidence privately before proposing another change.

Return a reviewable diff, test totals, unresolved risks, deployment/rollback steps, and the next gate. Do not commit, push, deploy, rewrite history, or publish merely because a checkpoint has been implemented. Test counts alone are not acceptance evidence for a live workflow.

**Change-control rule:** Before R3 is accepted, a proposed addition must directly support reliable meeting/ComfyUI coexistence or usable meeting outputs for operator review. Otherwise add it to the deferred list. Website work cannot be introduced as a prerequisite or as a current deliverable. Permit safety corrections, not unrelated architecture expansion.

### Acceptance record — update at real checkpoints

| Field | Record |
|---|---|
| Current checkpoint | R2-D — core dual-GPU meeting session and journal-preserving scope transition tested offline; stop for review |
| Last accepted checkpoint | R2 music adapter accepted for continued offline development; meeting/dashboard accepted offline; no live authorization |
| Runner deployed version | Operator reports installed/bootstrapped AI-HUB runner, ready with no owners; exact version/scope pending approved inspection; dual checkpoint delta not deployed |
| Application/meeting deployed versions | Verify and record privately |
| Integration test evidence | R1 Windows 45/17 and Linux 45/17 plus native probes 7/2 passed; R2 Linux 438 run / 437 passed / 1 optional private audit skipped; runner 45/17 passed; dashboard Linux 24 passed; music Linux 24 passed with preserved dashboard 24, meeting-admission 17 and runner 45/17 regressions; meeting corrections Linux full 450 run / 449 passed / 1 optional private audit skipped, final focused 13 passed, runner 45/17 passed; R2-D final Linux runner 73/19 passed (28 new dual/scope cases), meeting full 451 run / 450 passed / 1 optional audit skipped plus final focused 30 passed, wallpaper 24 and music 24 passed; live integration pending |
| Reviewed meeting artifact | Pending |
| Operator manual-upload confirmation | Pending — completed only on explicit operator confirmation; no Codex website check |
| R3 operator acceptance | Pending — gateway requires accepted coexistence and explicit manual-upload confirmation |
| Advanced GPU coordination acceptance | Pending — after gateway/client stabilization |
| Automatic website upload | W1 final phase, deferred; drafts only, never automatic publication |
| Caller state | Four application/client containers stopped; HA GPU automations, Continue and HA voice paused; both ComfyUI backends running with no jobs authorized |
| Docker-restart cron | Intentionally disabled indefinitely by operator decision 2026-10-08; original backup retained; restore only on explicit operator request |
| Next authorized action | Operator review of R2-D offline diff/tests/scope transition and deployment steps; separate approval for live inspection, installation, service changes, switching and inference; no current live work |

## 12. References and provenance

This roadmap combines the user's decisions in this conversation with the supplied **Pasted markdown.md** integration review. Application call-site observations and the proposed runner layout come from that review and must be rechecked against the actual checkout before editing. Phases and acceptance gates above are planned work, not completion claims.

Primary technical references checked 2026-10-08; verify installed-version behavior during implementation:

[1]: https://docs.ollama.com/faq "Ollama: GPU placement, model retention, concurrency, and KV-cache configuration"
[2]: https://docs.comfy.org/development/comfyui-server/comms_routes "ComfyUI: submission, history, queue, and execution interfaces"
[3]: https://man7.org/linux/man-pages/man2/flock.2.html "Linux flock: lock ownership and release semantics"
[4]: https://docs.ollama.com/api/streaming "Ollama native streaming API"
[5]: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/ "Open WebUI provider connections"
[6]: https://docs.anythingllm.com/setup/llm-configuration/local/ollama "AnythingLLM Ollama configuration"
[7]: https://docs.continue.dev/reference "Continue model roles and apiBase configuration"
