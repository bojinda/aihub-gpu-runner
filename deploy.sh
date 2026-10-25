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

check_state() {
  "$VENV/bin/python" - "$CONFIG" <<'PY'
from pathlib import Path
import sys
from aihub_gpu_runner.config import load_config
from aihub_gpu_runner.core import FINAL, strict_json
_, _, state_dir, _, scope = load_config(Path(sys.argv[1]))
state = strict_json((state_dir / 'ownership.json').read_bytes())
boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if (state.get('scope') != scope or state.get('ready') is not True or
        state.get('verified_boot') != boot or state.get('boot_recovery_status') != 'ready' or
        state.get('owners')):
    raise SystemExit('STOP: ownership not ready/idle for the current boot')
if any(strict_json(p.read_bytes()).get('state') not in FINAL
       for p in (state_dir / 'jobs').glob('*.json')):
    raise SystemExit('STOP: nonterminal job present')
print('PASS: runner journal ready, owner-free, and terminal-only')
PY
}

check_state

# Compare only package Python source with the currently installed venv package.
parity() {
  "$VENV/bin/python" - "$ROOT" <<'PY'
import sys
from pathlib import Path
import aihub_gpu_runner
src = Path(sys.argv[1]) / 'aihub_gpu_runner'
dst = Path(aihub_gpu_runner.__file__).resolve().parent
source = {p.name: p.read_bytes() for p in src.glob('*.py')}
installed = {p.name: p.read_bytes() for p in dst.glob('*.py')}
print('MATCH' if source == installed else 'DIFFERENT')
PY
}

VERSION=$(git -C "$ROOT" rev-parse --short HEAD)
if [[ $(parity) == MATCH ]]; then
  echo "PASS: runner code already matches Git $VERSION; no restart needed"
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
"$VENV/bin/python" -m pip wheel --quiet --no-index --no-deps --no-build-isolation --wheel-dir "$TMP" "$ROOT" || fail 'offline wheel build failed; service untouched'
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

"$VENV/bin/python" -m pip install --no-index --no-deps --force-reinstall "${WHEELS[0]}" || fail 'install failed; runner left stopped'

# Verify exact installed source before restarting; no automatic rollback.
[[ $(parity) == MATCH ]] || fail 'installed Python package differs from checked-out source; runner left stopped'
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
