# E1 stop: executor component qualifies; ownership authority is not fenced

E0 feasibility was accepted. The operator's global E-stop decision is final:
both resources relinquished regardless of running work, accepted GPU overlap,
no backend/process operation and no idle/history/cleanup eligibility condition.
Every separately confirmed operation must select a fresh generation, including
zero-owner cases with old held OS locks. Check/status/Verify never rotate it.

This branch is an incomplete offline component, NOT a deployable E-stop release.
The requested stop condition has been reproduced: unchanged legacy AtomicStore
can replace ownership.json after a successor generation was committed. No global
E-stop API/UI or production mutation was enabled while that invariant is false.

## Implemented worker-capacity component

Runner atomically swaps pool/pending/future/task references under jobs.lock after
checking the requested generation against committed metadata. It allocates a fresh
ThreadPoolExecutor, returns the old group, and calls shutdown(wait=False,
cancel_futures=True) outside journal mutexes. Running tasks are neither cancelled
nor joined. Their executor/future references remain until submitted work exits.
Cancelled queued wrappers close only their captured old descriptors, never clear
an owner. Normal Runner.close still joins work; it is not called by rotation.

The scheduler's generation snapshot and queue submission share jobs.lock with the
swap. Old pending completion cannot pop current entries. Running wrappers keep
their captured old public future and reject late cooperative store writes and
updates; old callbacks cannot notify current recovery or write current handoffs.
Store guards are thread-local, perform read-only generation checks and never
acquire admission recursively. There is no new scheduler or worker subsystem.

Eight desired component tests pass on actual Runner pools and Linux locks:
saturated old pool/fresh capacity, queued cancellation/FD close, old native locks
remain held, no interruption, retained references/reaping, repeated/idempotent
rotation, stale finish/write suppression, callbacks outside jobs.lock, ordinary
same-generation writes and recovery-notify isolation. Synthetic generation metadata
and queue-only leases are explicitly test setup, not authority qualification.
The ordinary runner suite also passes: 828 executions, zero failures/errors/skips.

These guards cover the current runner's worker context. They do NOT fence an
independent old SDK, a fresh AtomicStore instance or arbitrary filesystem writes.
Nor is the authority overlay/generation switch implemented by this capacity method.
It cannot be exposed as E-stop in its present state.

## Precise failing invariant

`qualification/estop_legacy_authority_invariant.py` runs the preserved installed
host SDK in a real independent Linux process. It caches the old schema1 state,
waits for a synthetic schema2 commit, then uses the original AtomicStore.write API
under the same admission mutex. AtomicStore has no compare-and-swap/epoch check:
atomic rename replaces the successor journal with the cached old bytes, removing
its generation and audit. The desired assertion that committed authority survives
FAILS. This is retained as a failing invariant, not relabeled repair acceptance.

This is demonstrated API/file-write capability, not evidence that a production
incident or a particular existing recovery call naturally used this exact sequence.
The bounded writer inventory records all literal ownership writes in CPU, host,
client and helper variants, enclosing contexts and source hashes. Many actual
callers take admission.lock and validate a fresh snapshot; schema2 can fence those
callers. That does not make the raw old write API an epoch-fenced authority. Generic
AtomicStore remains a writer sink, and the current runner's guard cannot retrofit
an independently running old interpreter.

## Smallest proposed authority fence for review, not implemented

A successor canonical authority filename and a protected legacy-path tombstone
would prevent old hardcoded writes from changing current authority. It must remain
ONE scheduler authority: the legacy path is a non-authoritative, fail-closed format
tombstone, never a parallel live registry. A root-pinned format selector would
choose the successor canonical file for all new SDK/helper/cleanup readers.

Renaming the journal alone is inadequate: an old writer could restore schema1 at
the legacy path and old launchers could admit into obsolete locks. One concrete
filesystem fence to review is a root-owned sticky state directory with appropriate
runner group access plus a root-owned non-writable legacy tombstone and format
selector; the successor journal remains runner-owned. This must be tested with
separate real UIDs: same-UID legacy temp-file rename/open/truncate must fail, new
canonical commits must work, original locks/subdirectories/history must remain
usable, and interrupted installation must fail closed without a shadow authority.
No ownership/mode/path migration has been performed by this assignment.

This changes the native persistence layout/trust boundary and needs an explicit
crash-consistent installation/rollback contract. It is proposed as a precise
technical remedy, not asserted to be the only solution or an already accepted
prerequisite. All necessary host SDK, helper, Music trust, proxy and HA changes
must be qualified against that final authority protocol. Music stays 475 MiB/five
seconds; ToDo receipts, original physical inodes and recovery records stay intact.

Per the operator's explicit instruction, full E1 stops here instead of claiming
complete system-wide E-stop. No deployment procedure is executable. The next
review must resolve/qualify the legacy write fence before continuing E1. No new
idle, historical-process, producer-exclusion or cleanup restriction is requested.
