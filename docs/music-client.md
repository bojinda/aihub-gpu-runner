# Music client — R2 offline integration

The wake-up service uses the existing JobClient, OutputSlot, Admission and named
backend adapters. Lyric/tag `/api/chat` payloads use the configured GPU1 target.
The client waits for completion, owned cleanup, journal release and descriptor
closure before submitting the music workflow to the separate named GPU0 target.
No new scheduler, GPU lock manager, backend switching or nested acquisition exists.

The two ComfyUI contexts remain separate. Music requires its prompt's terminal
history, SaveAudioMP3 node 104's `audio` output, expected output namespace/filename,
MP3 signature and MIME, then backend-local cleanup and calibrated physical GPU0
observations. Partial outputs, a missing/wrong audio artifact or /free acknowledgment
cannot publish or release unresolved ownership. Signature checks do not establish
audio quality/playability; validate actual decoder compatibility in approved live tests.

## Existing wake-up sequence

`/swap_and_generate` reserves the existing generic CPU output guard before any
rotation, archives current `wakeup-latest.mp3`, then atomically promotes an already
ready `wakeup-new.mp3`. It starts the replacement worker only after rotation,
returns the existing started response fields, and never waits for GPU resources
in the request handler. Current playback remains available throughout generation.
If no replacement is ready the current latest song remains in place. Initial
playable-song provisioning remains an operator/deployment prerequisite; this
slice does not invent a seed-song bootstrap.

The guard spans rotation and the replacement worker, preventing parallel
generations/publication across threads and processes. Busy triggers return the
active job ID/current song paths without another rotation or generation. Normal
HA paths, timestamps, filenames, archive directory/name pattern, genre/daily
selection, vocal profiles, lyric prompts, duration and four workflow mutations
remain. Archive collisions select the next free timestamp in the same naming
pattern rather than overwriting an earlier archived song.

Only validated successful runner audio is published as the next MP3. The generic
byte publisher writes a same-directory temporary file, sets 0644, flushes/fsyncs,
atomically replaces, and on Linux fsyncs the directory. Failures before replacement
remove only the temporary file and preserve the existing destination. No partial
file is exposed. A failure after publication can leave synchronization uncertain;
the current playable latest remains intact and no automatic GPU replay occurs.

## Stable identity, status and recovery

Existing callers without `X-Request-ID` receive the original timestamp job ID.
For a retry, use the same optional stable header. An active operation reports busy;
a completed ID returns its recorded original response without re-archiving,
promoting again or generating twice. Intentional new work uses a new ID.
`/job/<id>` reads persistent private details, keeping queued/running/done/error,
genre/vocal previews, prompt ID and MP3 URL metadata. It also reports runner stage
ID/state. `/health` and the read-only debug-history route remain; that debug route
never submits generation or changes a backend.

Genre/vocal choices and randomized workflow payloads are private snapshots.
The job client provides status/result reads and optional progress callbacks;
application details are persisted by the existing output operation/store.
Client disconnect or wait timeout does not cancel runner supervision. Reconnect
to recorded runner IDs/payloads rather than starting a fresh backend job. Incomplete
or errored client operations after restart are not replayed or republished blindly.
They expose error status; operator review resolves existing runner ownership first.
No app endpoint can force-release a GPU or bypass the shared journal.

## Pending gates — no deployment authorized

Install/configure the reviewed canonical SDK in the music Python environment and
approved shared runner; no vendoring or service/image change has been performed.
Provide persistent private client state owned by the existing service UID (currently
configured 1000), outside public song mounts, with trusted runner URL/authentication
and named targets. Review API reachability without inventing networking changes.

Verify the mounted music workflow/hash, fixed paths/links/audio quality settings,
SaveAudioMP3 output and installed music ComfyUI version. The policy example has
null idle-memory ceiling and is deliberately not live-ready. Calibrate GPU0 with
both separate contexts present; validate terminal success/error, output metadata,
backend-local free/unload, empty queue and repeated memory observations. Verify
physical GPU placement, unchanged GPU1-only Ollama/Q8/Flash Attention/models and
controlled exclusion of direct/background callers in an approved live window.

The two meeting gates remain open: validate stage identity for changed approved
aliases/turn corrections, and document/test supported runner-managed historical
summarization without WhisperX. Keep gateway, dual-GPU, synthesis and website work
deferred. Cron remains intentionally disabled until an explicit restoration request.

Future rollback requires excluding submissions, resolving owned/uncertain work
and preserving journals, client records, latest/new/archive audio and lock identity
before reverting adapters. Nothing is currently deployed to roll back.
