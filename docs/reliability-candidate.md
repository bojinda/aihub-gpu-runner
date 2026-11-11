# Offline reliability candidate

This branch is an implementation candidate for independent review. No production
lease, service, scope, configuration, schedule or journal is changed by this work.
The [canonical roadmap](../roadmap.md) defines the gates; the
[contracts](reliability-contracts.md) distinguish supported behavior from proposed
additional authority. Installation, recovery and unattended acceptance are separate.

| Finding | Candidate change | Desired qualification |
|---|---|---|
| F01 | Correlate a new compute row with a second complete existing probe attestation; conservative boot-clock lifetime check; discard contaminated sample | Second census, unavailable clock, late birth/PID reuse, unknown/first zombie, fresh exit/stability; real CPU observer/parser tests |
| F02 | Persist PID, query timing and separate unknown/duplicate predicates in bounded diagnostics | Duplicate rows and unknown missing identity retain the precise PID; diagnostic truncation never grants authority |
| F03 | Existing manual class retains its authority; final proof moves inside native physical locks; unsupported class returns an explicit refusal | Existing eligible class, confirmation, active/foreign/stale refusal, exact failed disposition; broader authority disabled |
| F04 | Aggregate bounded coordination wait, whole-prefix release on miss/error, named inner busy outcomes | Real subprocess contention, missing late lock, expired budget, one native call after lost response |
| F05 | Same-budget stable re-observation at each publication/release boundary; precise cleanup cause survives terminal publication | Three final boundaries, no repeated inference/unload, owned result remains fenced |
| F06 | Media validates durable reconciliation receipts against exact originals; only unverified references consume native-GET budget; remove arbitrary history-count rejection | 25 references progress across restarts; 5,202 real files; mismatch/tamper/current owner/persistence failure still block |
| F07 | IndexedDB transactional pending operation, confirmation reservation and matching final disposition | Stale status during/lost POST, reload, second tab, no second POST, exact Verify; real browser through HA and Media |
| F08 | Canonical source reconstructed from verified originals on clean ancestry | Per-file private map; actual RFC1918 bridge policy; no transformed audit import |
| F09 | Idle checks/hash scans consume a shared request budget; unsupported classification avoids contended proof path | Real lock contention and 6,001 records; no timestamp-only trust cache; nominal deadlines are not hard OS-I/O preemption |
| F10 | Separate committed component builds and actual entrypoint qualification | Real HA Core, FastAPI `recovery_media:app`, isolated client/runner/plugin import origins; configuration omission fails recovery specifically |
| F11 | Cooperative protocol and unchanged SDK variants represented separately | Existing caller/meeting/song tests and direct-client boundary documentation |
| F12 | ToDo identity/cache source and accepted Music limits unchanged | Existing scheduled-operation, uncertainty, date/input/cache tests; Music 475 MiB/five-second invariants |

## Qualification interpretation

The private release packet contains exact commits, logs, source/artifact hashes,
import origins, unique scenario accounting, initialization failures and explicit
exclusions. Historical tests that require private journals or obsolete privileged
installers remain private baseline characterizations, not passing candidate tests.
Synthetic GPU observations are not evidence about historical physical processes.

The real-browser test uses Chromium and native IndexedDB with Home Assistant Core
authentication/WebSocket transport, actual Media ASGI and the native runner. It
injects a lost response and an older status at the browser boundary, verifies the
pending operation survives reload/another tab, and checks one authority call/audit.
The tiny CPU Comfy fixture uses no model/GPU and a separately built plugin.

Linux history fixtures fit the existing Media three-second budget. The same large
fixture exceeded that budget on the Windows development filesystem; no mocked-clock
pass replaces that result. Arbitrarily large archives and stuck filesystem calls
are not claimed to have bounded success. Unsafe, unidentified or active work remains
blocked even when this costs liveness.

## Remaining authority and live gates

Unknown/sparse historical process evidence is not made safe by adding a failure
string to an allowlist. New manual-retirement authority remains disabled, and direct
unmanaged producers remain a separate exclusion obligation. The current retained
incident therefore has no newly granted release route in this candidate. The private
operator procedure must stop at that prerequisite until the exact proposed contract
and recovery are independently authorized. CPU handover can preserve evidence without
releasing the owner; it is not permission to run a production restart now.

No automatic same-boot Ollama retirement, new permanent firewall/gateway/queue,
Music limit change, ToDo intent deletion or historical replay is included. Five
eligible production days remain a later gate, even after installation and one
successful recovery.
