"""Whole installed worker with seven-second REAL wall observations; CPU-only.

Uses the canonical regression fixture: actual native store/admission/lease and
production-size synthetic history. GPU/backend telemetry is synthetic. Never
reads production state. Run once against baseline (expected retention) and once
against the candidate (expected release). No source excerpts or GPU workloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import aihub_gpu_runner.core as core

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from tests.test_cleanup_budget import WorkerBudgetTests, REAL_MONOTONIC, REAL_SLEEP


class WallClock:
    @property
    def t(self): return REAL_MONOTONIC()
    @t.setter
    def t(self, value):
        delay = value - REAL_MONOTONIC()
        if delay > 0: REAL_SLEEP(delay)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--expect', choices=('retained', 'released'), required=True)
    args = p.parse_args()
    test = WorkerBudgetTests('runTest')
    try:
        f = test.fixture()
        f.clock = WallClock()
        test.enterContext(patch('time.monotonic', REAL_MONOTONIC))
        test.enterContext(patch('time.sleep', REAL_SLEEP))
        job = test.run_worker(f)
        released = not f.a.snapshot()['owners']
        expected = args.expect == 'released'
        assert released == expected, (job['state'], job.get('error_code'))
        assert f.publications == 1
        assert (job['state'] == 'success') == expected
        print(json.dumps({'expected': args.expect, 'released': released,
                          'state': job['state'], 'error': job.get('error_code'),
                          'wall_seconds': f.wall, 'samples': f.samples,
                          'phase_costs': f.phase_costs, 'persistence_seconds': f.persistence_seconds,
                          'authority_bytes': f.authority_bytes, 'history_jobs': 1799,
                          'inference_POSTs': 1, 'owned_unload_POSTs': 1, 'publications': 1,
                          'core_origin': core.__file__,
                          'core_sha256': hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
                          'physical_telemetry': 'synthetic', 'clock': 'real_wall',
                          'production_actions': 0}))
    finally:
        test.doCleanups()


if __name__ == '__main__': main()
