# Disposable integration qualification

These tests start local processes on loopback with temporary journals and random
fixture credentials. They never use production settings or GPU devices.
Home Assistant Core, its authentication/WebSocket server, FastAPI, Media's
`recovery_media:app`, the runner HTTP layer and native ownership journal are real.
GPU telemetry and historical process identities in the idle-recovery fixture
are **synthetic**. A passing test does not authenticate a vanished production PID.

Use Linux and Python 3.14. Create an isolated environment and install
`ha-media-requirements.lock`. The lock includes Home Assistant Core 2026.10.4.
Provide a separately checked-out dashboard-generator source tree; do not fall
back to an audit snapshot or an old release directory.

```sh
python3.14 -m venv /tmp/aihub-reliability-qualification/venv
/tmp/aihub-reliability-qualification/venv/bin/pip install -r qualification/ha-media-requirements.lock
export AIHUB_MEDIA_SOURCE=/absolute/path/to/dashboard-generator/app
export AIHUB_QUALIFICATION_RESULTS=/absolute/path/to/private-results
/tmp/aihub-reliability-qualification/venv/bin/python -B qualification/idle_ha_workflow.py
```

The two actual-HA tests cover configuration refusal, inactive-supervision
requirements, exact confirmation, stale approval, unchanged acknowledgment,
native commit followed by lost response, one audit, Verify without another
authority call, real non-administrator refusal, and a lost WebSocket response
followed by reconnect and verification of the exact original operation.
Frontend polling/storage interleavings are separately exercised by Node tests;
they are not claimed as real-browser coverage by these tests.

`browser_ha_workflow.py` adds the actual card, actual Chromium IndexedDB and
confirmation dialog to that same real HA/Media/native-authority chain. It drops
the mutation response at the browser adapter, injects an older successful status
summary, reloads the page, opens another tab, and verifies that only the original
pending operation reaches Verify. It asserts one authority call and one audit.
This is labeled transport fault injection, not a naturally occurring failure.

```sh
export PLAYWRIGHT_BROWSERS_PATH=/tmp/aihub-reliability-qualification/browsers
/tmp/aihub-reliability-qualification/venv/bin/python -m playwright install chromium
/tmp/aihub-reliability-qualification/venv/bin/python -B qualification/browser_ha_workflow.py
```

The Linux browser needs its ordinary NSS/NSPR shared libraries. On a minimal
environment they may be unpacked into a disposable prefix and supplied through
`LD_LIBRARY_PATH`; no production package installation or sudoers modification is
part of this harness. Chromium is launched with `--disable-gpu`.

For installed-package qualification, install the committed runner wheel into
the HA test environment and the separately built client SDK wheel into a Media
environment containing FastAPI/Uvicorn/requests/Pillow. Set
`AIHUB_REQUIRE_INSTALLED_RUNNER=1` and `AIHUB_MEDIA_PYTHON` to that second Python.
Run the harness with `-I -B`. The first flag refuses a runner imported from the
checkout; the Media subprocess's `PYTHONPATH` includes only the Media app source,
so it cannot silently import the CPU runner's SDK. Import origins remain part of
the private result record.

Build artifacts from an exact committed source revision, using the ordinary
`tools/build_components.py` separately for `runner`, `host-sdk`,
`privileged-helper`, `comfy-plugin` and `client-sdk`. Install each SDK variant
into its own disposable environment with `pip install --no-index --no-deps
/absolute/path/to/the/component.whl`. Do not install the CPU runner wheel into
Media or the Comfy plugin environment as a substitute for its separate SDK.
`media-requirements.lock` pins the standalone Media environment; its SDK is a
separately installed committed artifact. The HA environment uses
`ha-media-requirements.lock`; the unchanged Music Flask tests also use its
pinned Flask dependency. Host tests use the meeting repository's pinned
requirements and the separately installed host SDK.

After installing the CPU wheel into a separate Python 3.12 environment, run the
complete runner tests against its installed package with:

```sh
export MEETING_PIPELINE_TEST_ROOT=/absolute/path/to/meeting-pipeline
export AIHUB_QUALIFICATION_RESULTS=/absolute/path/to/private-results
/absolute/path/to/runner-python3.12/bin/python -I -B qualification/run_installed_suite.py
```

The runner suite records executed cases separately from unique method
definitions inherited by multiple test classes. The historical meeting launcher
test installs an explicit temporary network-denying transport and synthetic
switch fixture; no environment-provided hidden `sitecustomize` is required.
This tests real local admission and physical file-lock exclusion, not GPU
telemetry or Docker mode changes.

Import/hash verification of the fixed helper wheel on Python 3.12 and its
argument refusal are distinct from privileged configured-helper execution.
Tests using helper functions or a disposable CPU Comfy process do not prove a
root-owned `/etc/aihub/recovery-helper.json` plus real container-init capture.
If the test host has no disposable Docker/container namespace facility, record
that configured privileged path as an explicit qualification gap; do not
silently substitute mocked metadata or modify the host's sudoers.

`media-import-origins.json`, `ha-import-origin.json`, local server logs and the
sanitized fixture workflow transcript are written only beneath the explicitly
selected results directory. Keep that directory outside a public checkout.
Fixture credentials are random and are not written into test transcripts.

For existing CPU Comfy integration, obtain upstream ComfyUI commit
`48bb0bd18aa90bba0eac7b4c1a1400c4f7110046` into a disposable directory, and install
`integration-requirements.lock` into a **separate** Python environment. Its PyTorch
versions are CPU wheels from `https://download.pytorch.org/whl/cpu`. Set
`AIHUB_COMFY_SOURCE` and `AIHUB_COMFY_PYTHON`; then run
`qualification/test_media_integration.py` and
`qualification/test_real_integration.py`. These use a tiny fixture node that
writes a constant image, not a model or GPU workload. The source fallback of the
Comfy recovery plugin tests candidate source; separate installed-plugin-wheel
qualification must identify that artifact explicitly before claiming deployed
plugin compatibility. Set `AIHUB_COMFY_PLUGIN_WHEEL` to the separately built
plugin artifact to exercise the isolated `_aihub_recovery_comfy` namespace. The
fixture records its loaded module origins during actual node execution, after
all plugins have loaded; an empty origin record is not acceptance evidence.

Historical private-journal and old-installer characterization cases are not
imported into this portable suite. Their original sources and exclusion reasons
belong in the private review packet. Never satisfy a missing dependency by
copying a raw production journal into this tree.
