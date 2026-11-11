# Repository versus deployed source

Fresh SSH reads captured the service-selected wheel, Media container source, WebAuth source, host SDK, fixed helper launcher, Music hook, HA integration/card and retained immutable environments. SHA-256 manifests distinguish original bytes from public copies. No existing working tree or production source was overwritten.

| Component | Deployed source files compared | Different/missing on remote default | Exact blob absent from all fetched remote objects |
|---|---:|---:|---:|
| runner | 37 | 32 | 30 |
| meeting | 100 | 84 | 0 |
| media | 13 | 10 | 7 |
| music | 2 | 2 | 0 |

The last column measures absence in freshly cloned remote objects, not a claim that no private local candidate or inaccessible commit ever contained the bytes. See [source-comparison.json](evidence/source-comparison.json) and per-file patches. Existing local development branches and untracked candidates were preserved. Git hash-object here did not write objects into existing repositories.

## Active installation

- CPU runner: `ollama-recovery-restoration-20261110.1`; 37 modules; wheel SHA256 `cb16fc845ac054d16994eb1507344cc24b5e9cc445bf6a85e0e414c8153ef3df`. Systemd selects `run_runner.py` using the earlier completion release's Python 3.12 interpreter; it imports the new wheel. Interpreter prefix alone is not the loaded SDK version.
- Host launcher SDK: 36 modules from `ha-recovery-completion-20261110.1`, Python 3.13. Intentionally distinct from the runner's later 37-module wheel.
- Fixed privileged helper imports the pinned **helper-binding-correction** wheel, SHA256 `e40f4cd12b2832ec765abfb695e6b43d7d73fd9a7397d6f7e3f8c24b5e7861cf`, not the runner's latest wheel.
- Comfy plugin imports the isolated `_aihub_recovery_comfy` package from its own captured wheel. Its namespace prevents accidental substitution by a generic host SDK.
- Media and WebAuth retain their installed client SDKs; app-layer Media changes are captured separately. Media Git HEAD is `ced36be80205eec7559d3621fbc1fb386274e35d`, with modified tracked app files and untracked recovery/dispatch/standalone modules. A Git HEAD alone was not its runtime baseline.
- Host meeting tree HEAD is `f00b33ea1430339b0c4b0262c42a87b8ea8a1998`; the default GitHub branch is not this deployed branch. All100 included host source blobs exist in fetched remote objects. Seven ignored diagnostic proposals were captured privately but excluded from this active-source publication; they are not deployed entrypoints or missing production code.
- Runner default remote branch remains `codex/r1-gpu-runner`. These audit branches neither update that default nor claim its root code is production.

## Release succession

Captured immutable environments/wheels cover integrated recovery (Nov9), probe compatibility, manual idle recovery, completion/gate/exit fix, helper-binding correction and restoration/EXE-width correction (Nov10). [Module history](evidence/release-module-history.json) and [release deltas](evidence/release-deltas.json) identify changed, added and removed modules without inferring that a retained directory was still active.

The known Nov10 restoration changed only `ollama_probe.py`, `idle_recovery.py`, and `integrated_runtime.py` relative to helper-binding. The captured module comparison independently checks this. Earlier source is under `snapshots/history`; it is not silently merged into current code.

Configuration snapshots are allowlisted in [operational-settings.json](evidence/operational-settings.json); full originals remain private. Nulls in the initial configured-target view denote omitted values, not unlimited timeouts; the effective Target defaults are acquisition 3600 seconds, execution 1800 seconds, cleanup 60 seconds unless explicitly configured. Additional runtime metadata is in `evidence/runtime-inventory.json`.

Current deployment-related code is represented for review, with the explicit limits in EVIDENCE-INDEX. This is not an exhaustive export of third-party images, all Comfy custom nodes, private meeting content, unrelated HA configuration or kernel/driver source.

The prior18-module reliability.20261101.2 host SDK is included from the sealed pre-completion archive, checked against every HOST-ROLLBACK module hash. It is historical captured source with prior interactive backup verification, not a new read from the running service. This fills the pre-integration code comparison without claiming a statistically reliable old deployment or silently equating host and CPU-runner variants.
