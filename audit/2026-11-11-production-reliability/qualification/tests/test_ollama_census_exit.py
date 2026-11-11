"""Non-atomic Docker omission of a tracked, noncompute child: offline only."""
import errno
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import CleanupPending
from aihub_gpu_runner.core import RecoveryRequired
from tests import test_ollama_exit_races as races
from tests.test_ollama_physical_cleanup import GPU0, GPU1


class CensusExitTests(unittest.TestCase):
    def setUp(self):
        self.r = races.ExitRaceTests('runTest'); self.r.setUp()
        self.addCleanup(self.r.doCleanups); self.c = self.r.c
        self.on_sleep = lambda: None
        def sleep(duration):
            self.c.clock.sleep(duration); self.on_sleep()
        sleep_patch = patch('time.sleep', sleep); sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def spawn(self):
        self.r.live['305'] = '50'; self.r.tops['ollama'].append('305')

    def omit(self):
        self.r.tops['ollama'] = [p for p in self.r.tops['ollama'] if p != '305']

    def pending(self):
        from aihub_gpu_runner.ollama_cleanup import _ProcessPending
        self.c.prepare(); self.spawn()
        with self.assertRaises(_ProcessPending):self.c.guard.sample(60)
        self.omit()

    def test_final_census_omits_same_birth_then_positive_exit_and_three_samples(self):
        self.c.prepare(); self.c.backend.cleanup_models(['synthetic'], 60)
        self.spawn(); self.r.gpu_hook = self.omit
        self.on_sleep = lambda: self.r.exit('305')
        proof = self.c.guard.verify(60)
        obs = self.r.observations()
        self.assertEqual(proof['stable_samples'], 3)
        self.assertEqual(obs[0]['pending_ollama_process_exits'], {'305': '50'})
        self.assertEqual([o['process_snapshot_discarded'] for o in obs][-3:], [False]*3)
        self.assertEqual(proof['observation']['exited_ollama_processes']['305'], '50')
        self.assertEqual(len([x for x in self.c.calls if x[0]=='POST']), 1)
        self.assertNotIn('305', self.c.guard.context['ollama_processes'])

    def test_initial_census_omits_tracked_child_but_full_safety_checks_still_run(self):
        self.pending(); before = len(self.c.commands)
        from aihub_gpu_runner.ollama_cleanup import _ProcessPending
        with self.assertRaises(_ProcessPending):self.c.guard.sample(60)
        commands = self.c.commands[before:]
        self.assertTrue(any(x[1].startswith('--query-compute-apps=') for x in commands))
        self.assertTrue(any(x[1].startswith('--id=') for x in commands))
        self.assertNotIn('305', self.c.guard.exited)
        self.assertEqual(self.r.observations()[-1]['pending_ollama_process_exits'], {'305':'50'})

    def test_persistent_omission_expires_original_60_seconds_and_retains_owner(self):
        self.pending()
        with self.assertRaisesRegex(CleanupPending,'process_settling_pending'):self.c.guard.verify(60)
        self.assertEqual(self.c.clock.t,60)
        self.assertIn('gpu1',self.c.state['owners']);self.assertNotIn('305',self.c.guard.exited)

    def test_pending_child_compute_is_fatal_on_either_gpu_at_zero_memory(self):
        for gpu in (GPU0,GPU1):
            with self.subTest(gpu=gpu):
                # Use the tracked child omitted from Docker, still same /proc birth.
                if self.c.guard.context is None:self.pending()
                self.c.apps=lambda:gpu+',305,0\n'
                self.r.rejected('new_or_reused_process')

    def test_unknown_compute_is_not_hidden_by_pending_exit(self):
        self.pending();self.c.apps=lambda:GPU1+',999,0\n'
        self.r.rejected('unknown_or_duplicate_gpu_process')

    def test_pending_child_birth_change_is_fatal(self):
        self.pending();self.r.live['305']='51';self.r.rejected('process_birth_changed')

    def test_permission_and_io_errors_are_not_pending(self):
        self.pending()
        for number,exception in ((errno.EACCES,PermissionError),(errno.EIO,OSError)):
            with self.subTest(number=number):
                def denied(pid):
                    if pid=='305':raise exception(number,'PRIVATE_SENTINEL')
                self.r.stat_hook=denied
                with self.assertRaises(exception):self.c.guard.verify(60)
                detail=self.c.records[-1]['ollama_cleanup_failure']
                self.assertNotIn('PRIVATE_SENTINEL',str(detail))
                self.assertIn('gpu1',self.c.state['owners'])

    def test_missing_evidence_for_wrong_pid_cannot_verify_exit(self):
        from aihub_gpu_runner.recovery import ProcessDisappeared
        self.pending();original=self.c.guard.birth_reader
        self.c.guard.birth_reader=lambda pid:(_ for _ in ()).throw(ProcessDisappeared('999',errno.ENOENT)) if pid=='305' else original(pid)
        self.r.rejected('unverified_process_disappearance')

    def test_captured_original_compute_worker_missing_census_is_still_hard(self):
        self.c.prepare();self.r.tops['ollama'].remove('304');self.c.apps=lambda:GPU1+',304,32\n'
        self.r.rejected('unverified_process_disappearance')

    def test_main_process_omission_is_still_hard(self):
        self.pending();self.r.tops['ollama'].remove('303');self.r.rejected('main_process_unverified')

    def test_queue_and_driver_health_are_checked_before_pending_exit(self):
        self.pending();self.c.models=[{'name':'foreign'}]
        self.r.rejected('unexplained_backend_activity_ollama')
        self.c.models=[];self.c.observer.driver_health.side_effect=RecoveryRequired('kernel_gpu_driver_fault_requires_operator')
        self.r.rejected('kernel_gpu_driver_fault_requires_operator')

    def test_invalid_physical_health_not_hidden_by_pending_exit(self):
        self.pending();self.c.gpu_override=GPU1+',1,0,90,0,No'
        self.r.rejected('gpu_health_unverified')

    def test_storage_failure_after_pending_exit_observation_retains_owner(self):
        self.pending()
        def fail(**fields):
            if 'ollama_cleanup_observation' in fields:raise OSError(errno.ENOSPC,'PRIVATE_SENTINEL')
        self.c.guard.record=fail
        with self.assertRaises(OSError):self.c.guard.verify(60)
        self.assertIn('gpu1',self.c.state['owners'])

    def test_reappearance_after_positive_retirement_is_still_hard(self):
        self.pending();self.r.exit('305');record=self.c.guard.record
        def write(**fields):
            record(**fields)
            if '305' in self.c.guard.exited:self.r.live['305']='50'
        self.c.guard.record=write;self.r.rejected('unverified_process_disappearance')

    def test_two_samples_after_exit_cannot_release_at_deadline(self):
        self.pending();self.on_sleep=lambda:self.r.exit('305')
        with self.assertRaises(CleanupPending):self.c.guard.verify(.35)
        self.assertIn('gpu1',self.c.state['owners'])

    def test_independent_gpu0_compute_does_not_block_verified_gpu1_exit(self):
        self.pending();self.c.apps=lambda:GPU0+',202,2000\n';self.on_sleep=lambda:self.r.exit('305')
        proof=self.c.guard.verify(60)
        self.assertEqual(proof['stable_samples'],3)
        self.assertEqual(set(proof['observation']['physical_gpus']),{'gpu1'})

    def test_worker_normal_cleanup_once_and_release_only_after_positive_exit(self):
        self.r.after_capture=self.spawn
        runner,lease=self.r.worker();self.r.gpu_hook=self.omit
        self.on_sleep=lambda:self.r.exit('305')
        runner._work('owned-job',lease)
        job=self.c.store.job('owned-job')
        self.assertEqual(job['state'],'success');lease.release.assert_called_once()
        self.assertEqual(job['cleanup_proof']['physical_ollama_idle']['stable_samples'],3)
        unloads=[x for x in self.c.calls if x[0]=='POST' and isinstance(x[2],dict) and x[2].get('keep_alive')==0]
        self.assertEqual(len(unloads),1)

    def test_worker_deadline_keeps_ownership_without_replaying_cleanup(self):
        self.r.after_capture=self.spawn
        runner,lease=self.r.worker();self.r.gpu_hook=self.omit
        runner._work('owned-job',lease)
        job=self.c.store.job('owned-job')
        self.assertEqual(job['state'],'cleanup_pending')
        self.assertEqual(job['error_code'],'ollama_cleanup_process_settling_pending')
        self.assertNotIn('cleanup_proof',job);lease.release.assert_not_called()
        self.assertIn('gpu1',self.c.state['owners'])
        unloads=[x for x in self.c.calls if x[0]=='POST' and isinstance(x[2],dict) and x[2].get('keep_alive')==0]
        self.assertEqual(len(unloads),1)

    def test_no_completed_owned_work_does_not_enable_pending_exit(self):
        job=self.c.store.job('owned-job');job.update(host_stage=True,host_exit_code=0,host_requests=[])
        self.c.store.put_job(job);self.c.prepare();self.spawn()
        self.r.rejected('new_or_reused_process')

    def test_birth_changes_between_positive_missing_reads_remain_hard(self):
        self.pending();self.r.exit('305')
        original=self.c.guard.birth_reader;fired=False
        def birth(pid):
            nonlocal fired
            if pid=='305' and not fired:
                fired=True;self.r.live['305']='51'
                from aihub_gpu_runner.recovery import ProcessDisappeared
                raise ProcessDisappeared(pid,errno.ENOENT)
            return original(pid)
        self.c.guard.birth_reader=birth;self.r.rejected('process_birth_changed')

    def test_same_birth_reappears_during_positive_confirmation_remains_hard(self):
        self.pending();self.r.exit('305')
        original=self.c.guard.birth_reader;fired=False
        def birth(pid):
            nonlocal fired
            if pid=='305' and not fired:
                fired=True;self.r.live['305']='50'
                from aihub_gpu_runner.recovery import ProcessDisappeared
                raise ProcessDisappeared(pid,errno.ENOENT)
            return original(pid)
        self.c.guard.birth_reader=birth;self.r.rejected('unverified_process_disappearance')


if __name__=='__main__':unittest.main()
