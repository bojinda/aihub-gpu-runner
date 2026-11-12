"""Disposable CPU history reproduction; never reads production state or uses GPUs.

Run with the desired installed wheel's Python -I -B and this absolute script path.
Synthetic record structure approximates size/count, not historical process proof.
"""
import collections
import hashlib
import inspect
import json
from pathlib import Path
import sys
import time

import aihub_gpu_runner.core as core
sys.path.append(str(Path(__file__).resolve().parents[1]))
from tests.estop_history_fixture import history_fixture


def main():
    with history_fixture() as f:
        raw=f.store.path('ownership.json').read_bytes()
        counts=collections.Counter(); original=f.store._read_raw
        def read(name,default=None):
            counts[str(name) if str(name)=='ownership.json' else 'other']+=1
            return original(name,default)
        f.store._read_raw=read
        begin=time.monotonic(); check=f.control.check('admin'); check_seconds=time.monotonic()-begin
        check_reads=dict(counts); counts.clear(); phases={}
        begin=time.monotonic()
        f.control.fault=lambda phase:phases.setdefault(phase,time.monotonic()-begin)
        try:result=f.control.execute(check['token'],'admin',True)
        except core.RecoveryRequired as exc:result={'refusal':str(exc)}
        execute_seconds=time.monotonic()-begin
        state=f.admission.snapshot()
        print(json.dumps(dict(python=sys.version,core_origin=inspect.getfile(core),
            core_sha256=hashlib.sha256(Path(inspect.getfile(core)).read_bytes()).hexdigest(),
            synthetic=True,jobs=len(list((f.root/'jobs').glob('*.json'))),authority_bytes=len(raw),
            check_seconds=check_seconds,check_reads=check_reads,execute_seconds=execute_seconds,
            execute_reads=dict(counts),phases_seconds=phases,result=result,
            generation_sequence=state['admission_generation']['sequence'],
            intents=len(state['estop_intents']),commits=len(state['estop_attempts']),
            revocations=len(state['estop_revocations']),verify=f.control.verify(check['operation'])),indent=2))


if __name__=='__main__':main()
