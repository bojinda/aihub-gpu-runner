# Dashboard client — offline R2 slice

Dashboard calls the authenticated internal job API through the reusable
`client.JobClient`. Ollama uses the named GPU1 target; wallpaper uses the named
GPU0 target with its distinct ComfyUI context. All GPU ownership, supervision,
results and backend cleanup remain in the existing runner. This is not a gateway,
another scheduler, queue database or application GPU lock manager.

Each positive result is used only after status confirms both durable ownership
release and supervisor completion (including physical descriptor closure).
GPU1 therefore finishes owned cleanup and release before the dashboard starts
waiting for GPU0. ComfyUI waits for the accepted prompt's terminal history and
the configured SaveImage output, then verified cleanup. Terminal history errors
with `completed=false` are treated as failure, followed by the same cleanup
gate. Partial outputs or a /free acknowledgment do not prove completion/cleanup.

The client never retries with a fresh ID after ambiguous transport. Repeating
the same ID/payload reconnects to the existing job; changed payload conflicts.
Client wait expiry/disconnect does not cancel server supervision. Reconciling or
cleanup-pending ownership fails closed. Only definitively failed/released jobs
can use application fallback paths; uncertainty does not start a later GPU stage.

## CPU output protection and HA interface

The generic `OutputSlot` reuses NativeLock and AtomicStore to protect configured
PNG/bin writers, including conversion/push test routes. It manages CPU output
operations only and cannot acquire/release/recover a GPU. The app reserves it
before scheduling an asynchronous render, so concurrent sync/async triggers and
multiple processes cannot race to write the same output group.

Existing HA routes and normal response bodies remain, including /render,
/render_async, /render_status, /render_test, /render_test_path and to-do routes.
An optional `X-Request-ID` (valid stable ID, <=64 ASCII characters) identifies
one requested operation. Reconnect to the same endpoint with that ID: while
active it reports busy/status, after completion it returns the recorded response
without new GPU generation or another EPD push. Without the header each trigger
is a new request, preserving existing HA callers; unrelated network retries
cannot be inferred to be the same request. New intentional work uses a new ID.

Randomized workflow payloads are recorded once per operation before submission.
The existing seed/text/image/size mutations, PNG RGB 1600x1200 composition,
calendar/to-do rendering, EPD rotation/conversion and soft/hard push handling
remain. The fixed public output path always represents the latest completed
render; cached responses are not immutable image snapshots. Completed old IDs
do not regenerate deleted files. Private client records/plans need persistent
0700 storage outside the public image directory.

An incomplete/error client operation after application failure is not replayed
automatically. Its recorded runner job IDs/plans and the existing status/result
API support bounded operator review; resolve uncertain backend ownership through
the existing local exact-owner recovery gate before any approved new request.
There is no application force-release or direct backend fallback. Quote/to-do
generation invoked independently also goes through the same GPU1 job client.

## Required live/deployment gates

Nothing is deployed. Install the same reviewed canonical runner SDK into the
dashboard's Python 3.11+ environment by a separately approved packaging step;
do not vendor/copy coordination code into the app. The Dockerfile/Compose and
existing service/dependencies are unchanged in this slice.

Configure a trusted runner URL, private high-entropy token, named target mappings,
separate client wait deadline and persistent private client-state volume. Review
loopback/container reachability and authentication without inventing networking
or firewall changes. Deploy compatible server/client versions; older servers
without release/supervision status fields fail closed.

The wallpaper policy example is a reviewed-source starting point, not live-ready.
Verify the mounted workflow hash, exact existing input image allowlist, seed and
size mutations, SaveImage node 36 and actual version-specific history/error/view
behavior. Preserve all fixed model paths, graph links, output prefix and cleanup
nodes. Do not assume the development graph is the current mounted deployment.

Calibrate the physical GPU0 idle ceiling with both separate ComfyUI contexts
present; examples deliberately use null and cannot launch. Validate named /free,
empty queue and repeated memory observations against installed versions. Verify
GPU0/GPU1 device exposure and preserved Ollama model/options/Q8/Flash Attention.
Exclude direct/background callers during separately approved live tests. No
integration/global coordination claim is made while bypass clients remain.

Meeting pre-deployment requirements remain open: include approved aliases/turn
correction changes in stage identity, and document/test a runner-managed
historical summarization command without rerunning WhisperX. These gates do not
block this offline dashboard slice. Wake-up-song integration requires the next
explicit approval; website, gateway, dual-GPU and synthesis work is excluded.

Rollback of a future deployment must exclude submissions and resolve uncertain
owned work first. Preserve runner state, client records, output files and physical
locks; never fall back to uncoordinated direct calls to bypass a hold.
