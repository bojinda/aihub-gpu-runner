# Successor qualification contract

The successor to f9554d61 changes only result-release recognition and sanitized failure recording. It does not add recovery, cleanup effects, polling, scheduling, policy or GPU control.

`tests/test_music_release_review.py` permanently integrates all nine adversarial probes from the sealed review. Its diagnostic probe now expects fixed causes, and its recovery probe requires the user's explicit failed-only disposition. Twenty-three new methods extend the twenty existing Music methods; the original sixteen candidate cases remain in those existing methods.

## Existing audit schemas

| Path | Authoritative persisted record | Read access condition |
|---|---|---|
| Normal Music | `music_release_receipts[lease_id]`, owner/resources/policy, proof hash and final observation | Matching normal proof/context/receipt; no matching owner or active future |
| Local `recover` CLI | Flat `recoveries[]`: job_id, request_hash, lease_id, target, approval_ref, three acknowledged recovery flags, recovered_at | Exact binding, terminal failed with `operator_recovered_no_automatic_resubmission`; optional boot/resources/policy/UUID fields checked when present. The writer does not require or add incident/provenance/boot/resource fields |
| Automatic previous-boot recovery | `recoveries[]` with `provenance=runner_observations`, incident_id, nested identity, observed_boot, proof; corresponding released `automatic_recovery` incident and job recovery proof | Exact original binding, policy/UUID/resource identity, incident digest, released phase, verified job recovery/proof and matching verified new boot; terminal failed |

The local CLI commits the flat audit and owner removal before writing failed disposition. A crash in that interval must keep a still-successful job fenced. Automatic recovery also preserves already-terminal success; without a normal receipt that success remains fenced. Result reads do not mutate its disposition, synthesize a receipt, or infer successful normal cleanup. Resolving such an intentionally fenced state requires an existing authorized operational procedure; no new recovery path is supplied here.

Regression coverage includes actual local CLI crash-before-failed-write, restart and no replay, both schema rejection matrices, GET-only repeat reads, retained artifacts and existing failure semantics, still-owned/supervised work, normal receipt binding, diagnostic propagation through backend/core/HTTP, unknown-message sanitization, real after-replace directory-fsync failure, GPU1 concurrent owner update, non-atomic sample window and old-reader rollback limits.

Missing result metadata on a recovered failed job retains the existing failed/None response and exposes no artifact. Invalid JSON and missing artifact bytes are not repaired. The existing artifact store has no persisted digest: arbitrary byte corruption is not detected by this change; GET returns retained bytes and does not invent an MP3 or promote failed execution. Qualification must independently validate and hash retrieved artifacts.

Sequential physical sampling and after-replace directory-fsync uncertainty remain explicit original limitations. Neither is corrected by relaxing thresholds or replaying effects. No normal success receipt is inferred from ready:true, empty owners, an arbitrary historical audit or recovery status alone.
