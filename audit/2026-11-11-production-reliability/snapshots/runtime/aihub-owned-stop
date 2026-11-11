#!/usr/bin/python3 -I
import hashlib,sys
from pathlib import Path
p=Path('/opt/aihub-recovery/helper-binding-correction-20261110.1/runner.whl')
if hashlib.sha256(p.read_bytes()).hexdigest()!='e40f4cd12b2832ec765abfb695e6b43d7d73fd9a7397d6f7e3f8c24b5e7861cf':raise SystemExit('reviewed_runner_wheel_changed')
sys.path.insert(0,str(p))
from aihub_gpu_runner.owned_stop_helper import main
main()
