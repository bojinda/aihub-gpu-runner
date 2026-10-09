# Bounded R2 deployment proposal — 2026-10-09

**Review only. None of these operational commands or live tests has run.**
Meeting, dashboard and music adapters are accepted for offline development.
The two meeting corrections are implemented/tested offline, pending this review.
Deployment, installation, remote writes, service/container changes, GPU jobs and
caller resumption require separate approval for each affected host/window.
No gateway, dual-GPU switching, whole-meeting synthesis or website work is included.

## Binding and approvals before execution

Use the retained private R0 inventory as the baseline; refresh only facts that can
change immediately before an approved live window. R0 predates the operator's
accepted GPU1-only Ollama restoration: its older dual-GPU observation is historical.
The restoration's accepted Compose/image bindings remain private; match them before
proceeding. Do not recreate, pull/upgrade or change Ollama's GPU/Q8/Flash Attention
configuration or model volume as part of deploying the runner.

The recorded topology is **AI-HUB: runner, meeting/lesson processing and backends;
Media: dashboard deployment; WebAuth: song service**. These are retained R0 roles,
not newly observed hosts and not inferred from local development folder names.
WebAuth denotes the recorded music-service host here; no website action is involved.
Refresh host/service/image/mount/account facts under separately approved inspection.
Missing approved targets, backup references or deployment facts remain gaps.

Before each remote batch, state exact target/commands/purpose and use the configured
Windows OpenSSH client/agent and independently verified pins. Retain per-command
approval, ED25519-only strict host checking, public-key-only BatchMode, a 10-second
connection timeout and one connection attempt. No blanket rule, new aliases,
credentials, forwarded agent, SSH trust repair or permission bypass is proposed.
An approved connection or inspection does not authorize a write/restart/job.

Freeze one private deployment manifest containing:

- Reviewed source-file hashes for the canonical package, meeting scripts/prompts,
  dashboard and music adapters; current branches/commits and tracked/untracked
  deployment entries. Preserve the five previously classified meeting entries.
  No commit/push is assumed or required by this proposal: use an immutable reviewed
  working-tree snapshot and wheel SHA256, not an unreviewed moving checkout.
- Existing deployment/Compose paths, current app image IDs/digests and base image
  IDs, original environment files/mounts/ports/users and recoverable local backups.
  Do not print environment values/tokens, transcript excerpts or unrelated filenames.
- The **original physical GPU0/GPU1 lock paths** and actual device/inode/mode/UID/GID,
  plus approved runner/meeting account access. Use recorded private overrides;
  logical example `/tmp/...` names are not a replacement. Never unlink/recreate
  a lock, repair permissions automatically or choose an alternate file.
- One native AI-HUB private admission/job/result/input-snapshot directory, shared
  by the runner and meeting helper with the same trusted config/scope and an
  approved account that can access the existing locks. App containers use the
  authenticated API, not container-private GPU journals or copied lock files.
- Existing latest/new/archive song mounts, playable current song/ready replacement,
  wallpaper PNG/bin, transcripts/chunk indexes, approved inputs and accepted
  outputs, with private backups or hashes appropriate to their size/sensitivity.
  Do not delete audio/transcripts or roll outputs back silently.

Keep Open WebUI, AnythingLLM, dashboard-gen and wake-songservice stopped; HA GPU
automations, Continue and HA voice remain paused. Both ComfyUI containers stay
running without submissions. Docker-restart cron remains intentionally disabled
indefinitely until an explicit restoration request; its original backup is preserved.

## Window exclusion and initial calibration

Record the exact paused HA triggers/scheduled callers and their prior states.
Stopped client containers and disabled schedules technically exclude those paths.
Manual API/CLI/ComfyUI browsers, other PCs and unverified integrations also require
operator cooperation or separately approved existing access controls. Cooperation,
empty queues, absent residency and idle-looking memory do not enforce exclusion.
Do not invent firewall/networking changes. If exclusion cannot be established,
record the gap and do not claim a guaranteed managed window.

Under approved read-only checks, observe both named queues/history, Ollama requests
and residency, foreground meeting/lesson processes, physical GPU memory, existing
native locks and any durable ownership. Resolve active/uncertain work before a
restart, bootstrap or calibration attestation. Do not infer completed cleanup
from a client timeout, process exit, `/ps` alone or a successful `/free` response.

Retained versions are Ollama 0.35.1, wallpaper ComfyUI 0.15.1 and music ComfyUI
0.24.0; refresh them rather than assuming deployment still matches. Record the
actual installed queue/history/error/view/free behavior and fixed workflow sources.
Preserve separate ComfyUI environments and their existing cleanup nodes.

Collect repeated quiescent GPU0 memory observations with **both** ComfyUI contexts
present and no callers submitting. Select/document an operator-reviewed idle
ceiling/tolerance from measurements, not a guessed value. The same physical
baseline must be suitable for WhisperX, wallpaper and music handoff. Examples
with null ceilings cannot launch jobs. Initial idle calibration configures the
gate; actual post-work cleanup is proved during the ordered live steps below.

Finalize trusted configuration before bootstrap because scope includes config,
resolved locks and workflows. If a later cleanup test shows the ceiling/policy is
wrong, stop with ownership retained; resolve work through exact approved recovery.
Do not change config/reset state to make it look free. A scope transition requires
its own reviewed migration after all work/ownership is resolved, preserving old
journals/history and changing every participating launcher together.

## Prepared installation/configuration changes

These are bounded templates; fill private approved paths/image references only
after inspection and approval. Do not execute placeholders.

| Component | Proposed change | Required verification |
|---|---|---|
| Canonical runner on AI-HUB | Build one reviewed Python 3.11+ wheel; install in an approved versioned environment; prepare the supervised-service unit from the existing template | Wheel/source hashes; existing account/lock access; service interpreter and private storage; unit working directory/environment; no backend restart |
| Meeting on AI-HUB | Install the same SDK for the helper and summary interpreter; copy only reviewed helper/input-declaration/summarizer files; keep map/reduce/models/prompts/HA/transcription policy | Same config/scope/state and original locks; private approved snapshots; unchanged source/output mounts; no nested acquisition |
| Dashboard on Media | Install that exact SDK in its current Python 3.11+ app environment/image; apply reviewed adapter files and private client state | Existing app/base image IDs, port/mounts/EPD paths and settings; private state persistent outside public `/out`; matching protocol |
| Music on WebAuth | Install that exact SDK in its current Python 3.12 app environment/image; apply reviewed adapter files and private client state | Existing service UID 1000 and song/archive/seed mounts; private-state ownership; filenames/genre/workflow/duration unchanged |

Build/package actions are also approval-gated; no network dependency upgrade or
image pull is implicit. The package has no runtime dependencies. A proposed local
build/install form is:

```bash
# REVIEW ONLY — approved offline build toolchain and immutable source snapshot
python -m pip wheel --no-index --no-deps --no-build-isolation \
  --wheel-dir "$APPROVED_WHEEL_DIR" "$REVIEWED_RUNNER_SOURCE"
sha256sum "$REVIEWED_RUNNER_WHEEL"
"$APPROVED_RUNNER_PYTHON" -m pip install --no-index --no-deps "$REVIEWED_RUNNER_WHEEL"
```

For container SDK installation, prepare a separately reviewed derived image from
the **existing recorded local base image**, copying only the reviewed local wheel
and installing it with `--no-index --no-deps`. Preserve the base app dependencies,
CMD/user/ports and mounted application/output paths. A bounded build uses
`docker build --pull=false --network=none`; verify resulting image ID and pin it.
Do not apply a Dockerfile/Compose packaging diff or start/recreate a container
until that per-app change/window is approved. Any future approved Compose start
uses `up --no-build --pull never` with the reviewed image binding.

App configuration adds only the reviewed runner URL/private token, named targets,
client wait deadline and persistent private state. Prepare bind mounts for
`/var/lib/dashboard-gpu-client` and `/var/lib/wake-song-gpu-client` outside public
image/song directories; directory access is private and music's existing UID must
be able to write. Do not modify existing public output/archive mounts or permissions
as an incidental fix. Missing permitted access blocks that host's deployment.

Choose/review internal runner reachability explicitly: AI-HUB loopback binding is
not automatically reachable from containers on Media/WebAuth. Approve actual
listen address, client URL and access controls; no invented proxy, host-network
mode, tunnel or firewall change. Generate/store a private high-entropy token
through an approved operator procedure; do not copy it into examples/logs/reports.
Server/client must share the reviewed release/supervision status protocol.

Configure exactly the named routine targets:

- `ollama`: physical GPU1, existing backend/model allowlists and stage options;
  no device/sharding overrides, gateway or automatic management routes.
- `wallpaper`: physical GPU0, its existing distinct container/address and reviewed
  picture workflow hash. Keep fixed paths/links/models/prefix; allow only existing
  seed, prompt, approved input-image enum and size/color mutations. SaveImage node
  36/images/PNG must match the installed deployment.
- `music`: physical GPU0, its separate container/address and reviewed wake-up
  workflow hash. Keep existing fixed paths/quality/links/prefix, mutations only at
  tags 225, lyrics 226, duration 117 and seed 409; SaveAudioMP3 104/audio/MP3 must match.
- `host_stages.whisperx`: calibrated physical GPU0 memory policy; keep foreground
  process behavior/device index and existing transcription/audio-deletion rules.

## Sequential validation — one approved step, evidence, then stop

Do not resume HA/interactive callers for these exercises. Temporary manual
validation triggers are separately approved, use synthetic/reviewed inputs and
comparison destinations, and do not authorize unattended operation.

1. **Runner:** Verify hashes/runtime/lock identity/native storage/account first.
   `inspect` starts or reads a blocked journal; record actual bootstrap ID. After
   verified quiescence/cleanup, approved local bootstrap evidence records initial
   readiness; it performs no backend operation. Start only the approved runner
   service, then check authenticated health/status/resources and unauthorized
   rejection. Do not submit inference in this installation step.
2. **Meeting:** Validate approved-source identity and a historical command against
   existing transcript/chunk indexes in a private comparison root. No recordings
   or WhisperX rerun are required. Hold one GPU1 stage through existing map/reduce
   requests/unload; check changed aliases/corrections cannot reuse cached results,
   recap/redactions/output quality and exact stage cleanup. Separately approve a
   bounded synthetic/reviewed GPU0 transcription exercise only if needed to prove
   actual WhisperX cleanup/audio policy; do not rerun historical transcription.
3. **Wallpaper:** Approve its image/config/mount diff and a single manual trigger
   through the new client, keeping normal HA automations paused. Verify GPU1 is
   released before waiting for GPU0; prompt ID/terminal history, selected output,
   cleanup and calibrated memory hold. Check PNG/composition/bin/paths/fallbacks;
   EPD transmission needs explicit approval if included. Test stable-ID reconnect
   and busy triggers without duplicate generation or writer races.
4. **Music:** Approve its independent image/config/mount diff. Back up/verify
   current playable latest, ready next and archive contents. One manual trigger
   archives/promotes before waiting and returns promptly. Verify vocals/genre/
   lyric/duration/workflow preservation, GPU1 release before GPU0, exact audio
   output, cleanup/memory and actual MP3 decode/playability. New publication is
   atomic and failures keep the current playable file. No repeated swap/promotion
   merely to test status: use stable IDs and read `/job/<id>`.
5. **Contention/recovery:** Only after individual acceptance, approve a bounded
   meeting→wallpaper/music contention exercise. GPU0 stages serialize; independent
   GPU0/GPU1 work can overlap. Test disconnect, retry, cleanup failure and controlled
   supervisor interruption without replay or premature handoff. Resolve every
   uncertain owner through exact job/hash/lease/target and explicit backend
   quiescence/cleanup evidence; no blind restart/unload/cancel/force release.

Supported historical command template (execution requires its own live approval):

```bash
MEETING_CONFIG_FILE="$APPROVED_MEETING_CONFIG" \
MEETING_SUMMARIES_ROOT="$PRIVATE_COMPARISON_ROOT" \
bash "$REVIEWED_MEETING_ROOT/bin/summarize-existing-meeting.sh" \
  "$APPROVED_EXISTING_TRANSCRIPT_DIR" --keep-recap
# Optional --speaker-aliases "$EXISTING_APPROVED_ALIASES";
# model/context/keep-alive overrides use the existing approved settings only.
```

The explicit comparison root takes precedence over the config file. Do not use
the recording postprocess/start/stop/HA pipeline for a historical summary. The
command is map/reduce-only and rejects whole-mode options. Missing optional
approved inputs remain missing; missing explicitly selected aliases fail closed.
Unresolved names can be manually corrected under the existing policy.

At each live gate record a sanitized private evidence row: approved host/window,
source/image/config hashes, expected outputs, device placement, before/after
queue/residency/memory/lock/journal state, result locations and operator decision.
Empty snapshots alone never close a cleanup/exclusion requirement. Stop on drift,
access changes, unknown work, malformed output, failed cleanup or ownership.

## Independent rollback and preservation

Each application can be reverted independently **only after** its submissions
are excluded and its active/uncertain runner work is resolved. Other accepted
adapters/configuration and backend containers remain unchanged.

| Component | Rollback proposal | Preserve / stop condition |
|---|---|---|
| Meeting | Restore its private backed-up code/config snapshot | Keep indexes, approved aliases/corrections, transcripts, comparison/accepted outputs and runner ownership records. Never rerun deleted recordings or silently enable a legacy bare-flock/direct path |
| Dashboard | Restore its recorded app image/code/environment/Compose delta | Keep current wallpaper/bin and private client records; keep service/callers stopped if the restored version bypasses admission; do not touch music or meeting |
| Music | Restore its recorded app image/code/environment/Compose delta | Keep latest/new/archive/seed files and client records as they are; no automatic re-swap, overwrite or regeneration; keep a legacy direct caller stopped; do not touch dashboard or meeting |
| Runner | Stop new submissions, resolve all owned work, then stop/revert its approved service/package/config | Preserve current journal/jobs/results/input snapshots and original lock inodes. Do not restore an old journal over current state or start an incompatible scope; app callers stay paused until reviewed |

Do not interpret an image rollback as authorization to run uncoordinated legacy
callers. If coordinated rollback is unavailable, keep that caller stopped and
review a separately approved exclusive recovery window. Playback/file delivery
can continue from already accepted outputs without initiating generation.

Normal caller resumption is a separate final decision after all gates and
preserved output/rollback evidence are accepted. Cron remains disabled by the
intentional operator decision; its restoration is not a deployment step.

## Current review boundary

No wheel was built, SDK installed on a live host, service/container started,
GPU job submitted, connection/access configuration changed, backup restored,
caller resumed, cron edited, commit created or push performed. Exact private
deployment bindings and fresh live facts remain pending the approved inspections.
This proposal is ready for review, not an execution authorization or live acceptance.
