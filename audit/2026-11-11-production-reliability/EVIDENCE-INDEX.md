# Evidence, provenance and confidentiality

## Public-safe review records

- `DEPLOYED-MANIFEST.json` and `evidence/all-source-manifest.json`: original SHA256, original-location placeholders, repository destination, published hash and transformation for each captured source.
- `evidence/current-incident.json`: selected native failure fields and bounded diagnostics; no prompt/result payload.
- `evidence/lock-correlation.json`: eight read-only check outcomes plus passive25ms lock observations; no credentials.
- `evidence/operational-settings.json`: selected accepted scope/policy/limits/locks and operator helper readback.
- `evidence/runtime-inventory.json`: service/SDK/container identities and topology summaries; raw Docker environment omitted.
- `evidence/source-comparison.json`, `release-module-history.json`, `release-deltas.json`, patches: exact source reconciliation.
- `evidence/job-timeline.json`: metadata-only selected native job index, not raw production journals.
- `evidence/offline-tests.log`, `qualification/`: reproducible CPU-only evidence.

## Private operator package — not committed or pushed

Operator-local location: `tmp/production-reliability-audit-20261111/private/` under the meeting-pipeline workspace. `PRIVATE-SHA256SUMS.json` pins the captured originals. It contains sensitive raw configuration/runtime/journal evidence and must not be uploaded to a public issue.

- `incident-first.json`, `hub-first.json`: first native job, ownership, process/physical observations, source bytes, lock identities, job-index hashes, read errors and runtime commands.
- `media-first.json`: actual app/client source, private durable intent/receipt contents, HA status/check, automation/dashboard/config/source captures, render result.
- `dependencies.json`, `extra.json`, `installed-history.json`: host code, immutable wheels/environments, backend node and direct-client metadata.
- `webauth-source.json`, `webauth-runtime.json`, `media-runtime.txt`, `media-deployment.json`: exact participating client/runtime originals.
- `operator-root-readback.json`: user-provided interactive-sudo helper readback and bounded journal result, explicitly operator-transcript provenance.
- `lock-window.json`, `lock-window-bounded-series.json`: original lock observations and read-only check timings.
- `local-*-branches.txt`: pre-existing branch inventories; untouched.
- `final-preservation.json`: end-of-audit exact incident/owner/source/lock/ingress preservation readback.

Earlier evidence stays in its original private directories: `tmp/ollama-recovery-restoration-20261110/` (deployment, two HA settlements, procps-width/first-census process tests,02:00 full acceptance), `tmp/helper-binding-correction-20261110/`, `tmp/ha-recovery-completion-20261110/`, `tmp/media-todo-resume-20261110/`, and `music-final-rebind-20261109.1/`. Their selected referenced artifacts are indexed by hash in the private package; raw prompts, member data, results and journals are not included in Git.

## Explicit limits

PID3645974 had exited before the first audit. Its original executable/argv/birth/parent/cgroup/namespace and the exact successful census contents were not retained. No later idle observation repairs this evidence gap. Original08:01 check stack/lock name was not logged; the later lock-contention reproduction is separately timestamped. Root-only helper bytes were not retrieved directly: the user supplied a fresh hash/field readback under interactive sudo. The privileged incident journal returned no entries.

No live mutation workflow was exercised in this audit. Third-party container images, all custom nodes and external client histories are not fully source-exported; installed IDs and relevant application code are captured. Historical direct/manual Ollama submissions cannot be excluded by current connections. Public configuration is deliberately incomplete where privacy requires omission; independent review of exact originals requires access to the private package.
