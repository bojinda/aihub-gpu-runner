# AI-HUB engineering guidance

- Simplicity is mandatory. Start with the smallest correct change that meets the operator’s actual requirements.
- Prefer existing mechanisms and coordinated shutdown/upgrade over mixed-version compatibility. Do not preserve obsolete development installations without a concrete need.
- Add architecture, abstractions, persistent state, services or protocols only when a demonstrated requirement needs them.
- Challenge requirements and earlier decisions that add unnecessary complexity; propose removal or simplification explicitly.
- Keep tests proportional: real failure mechanisms and essential invariants, without redundant artifacts/checkpoints.
- Preserve correct ownership, generation/stale-write fences, no duplicate execution, data integrity and authenticated actions.
- Follow roadmap.md and the operator’s current authorization. Offline approval never authorizes production mutation.
