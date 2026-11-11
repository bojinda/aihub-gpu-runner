"""Explicit CPU-only switch fixture for the real historical meeting launcher."""
import json
import os
from pathlib import Path


def install():
    from aihub_gpu_runner.dual_gpu import ComposeOllamaSwitch, switch_binding
    root = Path(os.environ['MEETING_TRANSPORT_FIXTURE_ROOT']).resolve()
    log = Path(os.environ['DUAL_OFFLINE_SWITCH_LOG']).resolve()
    if not log.is_relative_to(root):
        raise RuntimeError('synthetic_switch_log_required')

    def proof(self, mode):
        return {'mode': mode, 'backend_ready': True, 'placement_verified': True,
                'physical_gpus': ['gpu1'] if mode == 'gpu1' else ['gpu0', 'gpu1'],
                'gpu1_restored': mode == 'gpu1', 'switch_binding': switch_binding(self.target.mode_switch)}

    def change(self, action, mode):
        with log.open('a') as stream:
            stream.write(json.dumps({'action': action}) + '\n')
        return proof(self, mode)

    ComposeOllamaSwitch.activate = lambda self: change(self, 'activate', 'dual')
    ComposeOllamaSwitch.restore = lambda self: change(self, 'restore', 'gpu1')
    ComposeOllamaSwitch.verify = proof
