#!/usr/bin/env bash
# AI-HUB GPU runner: source-only, guarded in-place venv deployment.
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
VENV=${AIHUB_RUNNER_VENV:-"$HOME/venvs/aihub-gpu-runner"}
CONFIG=${AIHUB_RUNNER_CONFIG:-"$HOME/.config/aihub-gpu-runner/config.json"}
SERVICE=aihub-gpu-runner.service
BRANCH=codex/r1-gpu-runner
MODE=${1:---check}

if [[ $# -gt 1 || ( $MODE != --check && $MODE != --apply ) ]]; then
  echo "Usage: $0 [--check|--apply]" >&2; exit 2
fi
fail() { echo "STOP: $*" >&2; exit 1; }

[[ $(git -C "$ROOT" branch --show-current) == "$BRANCH" ]] || fail "unexpected Git branch"
git -C "$ROOT" diff --quiet || fail "uncommitted tracked changes"
git -C "$ROOT" diff --cached --quiet || fail "staged changes not committed"
git -C "$ROOT" ls-files --error-unmatch deploy.sh >/dev/null 2>&1 || fail "commit deploy.sh before using it"
[[ $(git -C "$ROOT" rev-parse HEAD) == $(git -C "$ROOT" rev-parse '@{upstream}') ]] || fail "checkout differs from fetched upstream"
[[ -x "$VENV/bin/python" && -f "$CONFIG" ]] || fail "runner venv or active config missing"
systemctl is-active --quiet "$SERVICE" || fail "runner service is not active"
[[ $(docker inspect -f '{{.State.Running}}|{{.State.Pid}}|{{.HostConfig.RestartPolicy.Name}}' comfyui-wallpaper) == 'false|0|no' ]] || fail "wallpaper not stopped-idle"

# Both probes use the installed environment, even when called from this checkout.
# -I ignores cwd/PYTHONPATH/user-site imports; -B prevents probe bytecode writes.
installed_check() {
  "$VENV/bin/python" -I -B - "$ROOT" "$VENV" "$CONFIG" "$1" <<'PY'
import importlib
import importlib.util
from pathlib import Path
import sys
import sysconfig

root, venv, config = (Path(p).resolve() for p in sys.argv[1:4])
action = sys.argv[4]
name = 'aihub_gpu_runner'
src = root / name

def stop(message):
    raise SystemExit('STOP: ' + message)

if Path(sys.prefix).resolve() != venv or sys.prefix == sys.base_prefix:
    stop('interpreter prefix does not identify the configured runner venv')

# Check origin before executing any runner package code. Editable installations
# and package symlinks into the checkout are not installed-wheel evidence.
allowed = set()
for key in ('purelib', 'platlib'):
    library = Path(sysconfig.get_path(key)).resolve()
    if library.is_relative_to(venv):
        allowed.add(library / name / '__init__.py')
spec = importlib.util.find_spec(name)
if spec is None or spec.origin is None:
    stop('runner package is not installed in the configured venv')
origin = Path(spec.origin).resolve()
if (origin not in allowed or not origin.is_file() or
        origin.is_relative_to(root)):
    stop('runner package origin is outside the intended venv installation')
dst = origin.parent
if ([Path(p).resolve() for p in (spec.submodule_search_locations or ())]
        != [dst]):
    stop('runner package search path is not the intended venv installation')
package = importlib.import_module(name)
if (Path(package.__file__).resolve() != origin or
        [Path(p).resolve() for p in package.__path__] != [dst]):
    stop('runner package origin changed during import')

def manifest(directory):
    # Compare the complete Python source tree, including extra installed files.
    # No dependency/metadata/native-extension or running-process claim is made.
    if directory.is_symlink() or not directory.is_dir():
        stop('package source directory is missing or symlinked')
    files = {}
    for path in directory.rglob('*'):
        if path.is_symlink():
            stop('package source contains a symlink; review installation')
        if path.is_file() and path.suffix == '.py':
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    if '__init__.py' not in files:
        stop('package Python source manifest is incomplete')
    return files

if action == 'state':
    # Validate installed source paths before importing its config/core modules.
    manifest(dst)
    from aihub_gpu_runner.config import load_config
    from aihub_gpu_runner.core import FINAL, strict_json
    _, _, state_dir, _, scope = load_config(config)
    state = strict_json((state_dir / 'ownership.json').read_bytes())
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if (state.get('scope') != scope or state.get('ready') is not True or
            state.get('verified_boot') != boot or state.get('boot_recovery_status') != 'ready' or
            state.get('owners')):
        stop('ownership not ready/idle for the current boot')
    if not (state_dir / 'jobs').is_dir():
        stop('runner jobs directory is missing')
    if any(strict_json(p.read_bytes()).get('state') not in FINAL
           for p in (state_dir / 'jobs').glob('*.json')):
        stop('nonterminal job present')
    print(f'PASS: installed package origin: {origin}')
    print('PASS: runner journal ready, owner-free, and terminal-only')
elif action == 'parity':
    print('MATCH' if manifest(src) == manifest(dst) else 'DIFFERENT')
else:
    stop('unknown installed-package check')
PY
}

check_state() { installed_check state; }
parity() { installed_check parity; }

check_state || fail 'installed-package state check failed'

VERSION=$(git -C "$ROOT" rev-parse --short HEAD)
PARITY=$(parity) || fail 'unable to verify installed-package source'
[[ $PARITY == MATCH || $PARITY == DIFFERENT ]] || fail 'unexpected parity result'
if [[ $PARITY == MATCH ]]; then
  echo "PASS: installed venv package Python source matches Git $VERSION; no package update indicated"
  exit 0
fi

echo "Runner source $VERSION differs from the installed package."
if [[ $MODE == --check ]]; then
  echo 'CHECK ONLY. During an approved idle/caller-excluded window, run ./deploy.sh --apply.'
  exit 0
fi

cat <<'TXT'
Before proceeding: exclude new GPU job submissions (HA/dashboard/music/meeting
and any direct clients). This is an operator-controlled maintenance window;
the script cannot prevent a foreign caller from submitting in the next instant.
No durable scope transition or journal reset will be performed.
TXT
read -r -p 'Type DEPLOY RUNNER to continue: ' CONFIRM
[[ $CONFIRM == 'DEPLOY RUNNER' ]] || fail 'not approved'

# Build *before* stopping the healthy runner; do not download dependencies.
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
"$VENV/bin/python" -I -B -m pip wheel --quiet --no-index --no-deps --no-build-isolation --wheel-dir "$TMP" "$ROOT" || fail 'offline wheel build failed; service untouched'
WHEELS=("$TMP"/*.whl)
[[ ${#WHEELS[@]} -eq 1 && -f ${WHEELS[0]} ]] || fail 'unexpected wheel output'

# Check again immediately before the controlled stop.
check_state
systemctl is-active --quiet "$SERVICE" || fail 'runner no longer active'
[[ $(docker inspect -f '{{.State.Running}}|{{.State.Pid}}' comfyui-wallpaper) == 'false|0' ]] || fail 'wallpaper changed state'

echo 'Stopping runner for upgrade. On failure after this point it stays stopped for review.'
sudo systemctl stop "$SERVICE"
! systemctl is-active --quiet "$SERVICE" || fail 'runner did not stop'
check_state || fail 'journal changed during stop; runner left stopped'

"$VENV/bin/python" -I -B -m pip install --no-index --no-deps --force-reinstall "${WHEELS[0]}" || fail 'install failed; runner left stopped'

# Verify exact installed source before restarting; no automatic rollback.
PARITY=$(parity) || fail 'unable to verify installed package; runner left stopped'
[[ $PARITY == MATCH ]] || fail 'installed Python package differs from checked-out source; runner left stopped'
sudo systemctl start "$SERVICE" || fail 'runner start failed; review required'
# Type=simple may report active before the journal's startup check completes.
for attempt in {1..10}; do
  if systemctl is-active --quiet "$SERVICE" && check_state; then
    echo "PASS: runner deployed at Git $VERSION, active, journal preserved"
    exit 0
  fi
  sleep 1
done
sudo systemctl stop "$SERVICE" || true
fail 'runner did not reach verified readiness; stopped for review'
