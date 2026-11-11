# November 11 production reliability audit

**Read-only investigation. No repair, recovery or deployment is included or authorized.**

Start with [CURRENT-INCIDENT.md](CURRENT-INCIDENT.md), then [FAILURE-ANALYSIS.md](FAILURE-ANALYSIS.md) and [NEXT-DECISIONS.md](NEXT-DECISIONS.md). The current seasonal Ollama job still owns GPU1. Previous successful HA operation `bc16c875a05a0bfbd4022a82222ae12cf7f4601a7976eb93` belongs to a different job and supplies no current authority.

## Review map

- [DEPLOYED-MANIFEST.json](DEPLOYED-MANIFEST.json): original source hashes, redacted-copy hashes, paths and provenance.
- [SOURCE-DIFF.md](SOURCE-DIFF.md): remote/default divergence, current and historical runtime snapshots.
- [ARCHITECTURE.md](ARCHITECTURE.md): actual admission, scheduled workflow and recovery boundaries.
- [INCIDENT-TIMELINE.md](INCIDENT-TIMELINE.md): successful and failed cycles, with limits on historical inference.
- [TEST-RESULTS.md](TEST-RESULTS.md): reproducible offline tests using captured source.
- [EVIDENCE-INDEX.md](EVIDENCE-INDEX.md): public-safe extracts and separate private originals.

Current production source is under `snapshots/current`, not the older repository-root package. Host SDK, privileged helper wheel, Comfy plugin, Music hook and HA integration are deliberately separate snapshots. Nothing here should be copied over a live installation.

## Companion audit branches

All four use `codex/audit-production-reliability-20261111`:

- [meeting-pipeline](https://github.com/bojinda/meeting-pipeline/tree/codex/audit-production-reliability-20261111/audit/2026-11-11-production-reliability): deployed host launchers and processing sources.
- [dashboard-generator](https://github.com/bojinda/dashboard-generator/tree/codex/audit-production-reliability-20261111/audit/2026-11-11-production-reliability): actual Media app and SDK.
- [daily-wakeup-song](https://github.com/bojinda/daily-wakeup-song/tree/codex/audit-production-reliability-20261111/audit/2026-11-11-production-reliability): actual WebAuth Music client and SDK.

Private addresses, home-account names, unrelated HA automations, member content, prompts, results, tokens and raw journals are excluded. Operational IDs and content hashes used to bind the incident are retained. Some deployment/configuration records are selective summaries, not byte-for-byte configuration files. Original hashes and this distinction are explicit.

No production mutation endpoint, helper action, inference, recovery, restart, schedule pause, cleanup, force release or policy change was executed. Existing authenticated read-only Check calls used transient locks; they issued no usable ticket for this unsupported class and performed no recovery mutation.
