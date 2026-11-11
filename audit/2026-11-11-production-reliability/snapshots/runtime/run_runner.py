import hashlib,sys
from pathlib import Path
p=Path('/opt/aihub-recovery/ollama-recovery-restoration-20261110.1/runner.whl')
if hashlib.sha256(p.read_bytes()).hexdigest()!='cb16fc845ac054d16994eb1507344cc24b5e9cc445bf6a85e0e414c8153ef3df':raise SystemExit("reviewed_runner_wheel_changed")
sys.path.insert(0,str(p))
from aihub_gpu_runner.__main__ import main
main()
