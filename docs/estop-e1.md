# E1: global administrative E-stop, coordinated upgrade

Offline candidate only. No production action is authorized by this source.

## Simplest supported deployment contract

Stop and verify every ownership writer, upgrade together, then restart compatible
components. Keep `ownership.json` and ordinary directory permissions. No successor
journal filename, tombstone, mixed-version shim, new scheduler or recovery service.
The former legacy-write blocker applies only if an obsolete interpreter survives
the maintenance window; that deployment is explicitly unsupported. Guarded host
launchers require protocol2 and the upgrade checks every actual interpreter/entrypoint.

The simplicity policy is canonical in AGENTS.md. The earlier requirements for a
live old-SDK handover and a privileged compatibility filesystem are removed. The
privileged Docker-helper qualification project is not an E-stop prerequisite:
E-stop never calls that helper. Ordinary Recover retains its separate safeguards.

## Authority and durability

The existing native journal gains schema2, a current lock-generation descriptor,
an authority revision and E-stop intent/audit/revocation maps. These fields are
necessary for crash recovery, exact Verify and late-write precedence; no second
authority or database is added. The explicit local activation action runs only
during a confirmed coordinated shutdown and checks supervisor/host/resource locks.
It preserves existing lease IDs, scope/policy, job/result files and original lock
inodes. Startup and HTTP never activate/migrate the journal automatically.

All updated ownership writes share admission.lock, including raw AtomicStore
writes. A cached revision or generation is rejected. Lock ownership is tracked
only to avoid reentering a mutex already held by the same process/thread. Jobs and
results serialize through that same authority boundary; revoked jobs cannot be
written again. Reads overlay their immutable failed/no-replay disposition while
the original bytes remain available as historical evidence. Runner result/artifact
APIs cannot publish revoked success. Revoked API status reports logical settlement
independently of unrelated backend readiness and warns old work may continue.
Result and artifact validation/data reads share admission.lock, so a read cannot
combine pre-revocation success with post-revocation data. A read completed before
the switch remains a historical response; already delivered bytes cannot be recalled.

E-stop takes jobs.lock then admission.lock with one bounded wait. It takes no GPU
resource flock and makes no GPU, Docker, unload, cleanup, reset or signal call.
Check signs the exact generation, boot, scope/policy, original/current lock IDs,
owner identities and affected requests. Ordinary phase progress alone does not
invalidate an approval. Confirmation persists an intent, exclusively creates and
fsyncs the next lock namespace, then atomically commits new authority, all lease/
queued-request revocations and one signed audit. Original lock files remain intact.
Each confirmed operation rotates, even if zero owners are recorded. Repeated POST
is Verify-only; GET/Verify never rotate. Before new admission, authority/directory
flush resolves an earlier rename acknowledgement failure. Unknown responses keep
the exact operation; an unknown operation is not declared no-effect until its
monotonic ticket deadline prevents a delayed original POST from executing.

Native revision checks preserve existing audit/revocation entries and reject
restoration of revoked owners even through a fresh snapshot. Process crash before
commit leaves inert preparation; crash after commit leaves the one committed
record. Filesystem fault injection and process exits qualify these boundaries;
they do not simulate a physical storage controller ignoring fsync.

## Existing execution and integrations

Runner swaps its ThreadPoolExecutor/maps under jobs.lock. Shutdown of the old pool
is wait=False/cancel_futures=True outside journal locks. Running work is untouched;
references remain until it exits, while fresh work has independent capacity.
Old callbacks, phases, updates and publication cannot change current authority.
This uses the existing scheduler, not a worker-management subsystem.

The same canonical wheel is used in CPU, native-host and fixed-helper environments.
Preserved old SDK/helper source variants remain historical provenance, not runtime
compatibility targets. The guarded meeting/WhisperX launcher checks protocol2 and
propagates failure exit codes. Configured original lock paths stay semantic resource
aliases; actual acquisition always uses the current generation.

The fixed helper validates the original trusted configuration and derives current
generation locks from the native journal. The Music hook does the same through its
directory bind and retains enabled state, 475 MiB and the five-second budget. Its
normal cleanup can still occur when a prompt finishes; E-stop itself never invokes
cleanup. The Wallpaper plugin and HTTP-only client SDKs need no protocol migration.

HA has dedicated E-stop Check, confirmation, status and Verify actions using the
existing administrator check, separate read/operate credentials and fixed Media
allowlists. Its pending reference uses the existing IndexedDB store with a separate
E-stop key; credentials/tickets are not persisted. Old Recovery polling/success
cannot redirect it. The UI separates logical ownership from NOT ASSERTED physical
GPU activity and warns that old and new GPU execution may overlap.

## Review and installation boundaries

The private release manifest binds commits/artifacts/import origins, actual launch
paths and the single attended shutdown/upgrade sequence. All native writers must
be absent before activation and remain stopped until every compatible component
is installed/read back. No obsolete native writer may be relaunched. A surviving
old raw writer is a failed maintenance prerequisite, not a supported runtime mode.
No special file-permission migration is proposed to support it.

After offline qualification, stop for independent review. Installation, HA Core/
container/service restarts and any live E-stop need the subsequent exact attended
authorization. Preserve backup/history/locks and original ingress states. A schema2
rollback cannot start obsolete code over the active journal or restore old journals.
Five eligible ordinary production days remain a later acceptance gate; emergency
use does not erase ordinary cleanup failures from that accounting.

## Reproducing the offline checkpoint

Build the native wheel from the committed runtime source identified in the private
manifest with `tools/build_components.py --commit COMMIT --out OUTPUT`. Install
that single wheel into unused dependency-correct Python 3.12/3.13/3.14 test venvs.
`qualification/run_installed_suite.py` records exact import origins, executions,
unique methods and initialization errors; set AIHUB_QUALIFICATION_RESULTS to a
private disposable directory and AIHUB_REQUIRE_INSTALLED_RUNNER=1. The real Linux
process/flock tests are tests/test_estop.py and test_estop_worker_capacity.py.

Run qualification/estop_ha_workflow.py in the pinned HA Core/browser environment,
with AIHUB_MEDIA_SOURCE pointing at the companion app and AIHUB_MEDIA_PYTHON at
its isolated ASGI environment. This exercises actual HA authentication/WebSocket,
Media TLS proxy, runner and Chromium/IndexedDB. Run existing test_real_integration.py
and test_media_integration.py with AIHUB_QUALIFY_GENERATIONS=1, pinned CPU ComfyUI
and unchanged plugin wheel. Telemetry is synthetic; no test authenticates historical
GPU processes or establishes physical GPU idleness. Existing 450 MiB scenarios
test historical refusal fixtures, not a change to the accepted 475 MiB policy.

All five `tests/test_*card.mjs` suites run with Node's `--test`. Companion Media
tests require FastAPI's complete test-client dependencies and MEETING_PIPELINE_SOURCE;
their host-stage subprocess cases require the current native SDK. Actual Media
ASGI integration separately preserves its HTTP-only SDK. Initialization failures
and corrected fixture runs remain in the private packet, separate from final passes.
