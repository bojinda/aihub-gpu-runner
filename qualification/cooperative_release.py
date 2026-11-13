"""CPU-only reproduction: real worker/store/lease, persistent synthetic residency."""
import argparse
import json
from pathlib import Path
import sys

import aihub_gpu_runner.core as core

sys.path.append(str(Path(__file__).resolve().parents[1]))
from tests.test_cleanup_budget import WorkerBudgetTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect', choices=('retained', 'released'), required=True)
    args = parser.parse_args()
    test = WorkerBudgetTests('runTest')
    try:
        f = test.fixture(seconds=.1, history=20, authority_bytes=30_000)
        f.models = [{'name': 'synthetic-direct-client'}]
        job = test.run_worker(f)
        released = not f.a.snapshot()['owners']
        assert released == (args.expect == 'released'), job
        if released:
            assert job['cleanup_verified'] is False
            assert 'physical_ollama_idle' not in job['cleanup_proof']
            assert f.runner.result('owned-job')['release_outcome'] == 'completed_without_idle'
        print(json.dumps(dict(state=job['state'], reason=job.get('error_code'),
            released=released, cleanup_verified=job.get('cleanup_verified'),
            proof=job.get('cleanup_proof'), samples=f.samples,
            runner_origin=core.__file__, production_actions=0)))
    finally:
        test.doCleanups()


if __name__ == '__main__':
    main()
