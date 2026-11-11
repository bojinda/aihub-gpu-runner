"""Synthetic analogue of retained Nov 8 pre-start data; no live helper imports."""
import copy
from datetime import datetime, timezone
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock
from aihub_gpu_runner.maintenance import initialize, read_gate
from aihub_gpu_runner.recovery_control import RecoveryControl
from aihub_gpu_runner.recovery_checks import digest
from aihub_gpu_runner.recovery_observer import COVERAGE

BOOT = '11111111-1111-4111-8111-111111111111'
REASON = 'service_exceeds_calibrated_idle_baseline_music'


class Fixture:
    def __init__(self, case):
        self.tmp = tempfile.TemporaryDirectory(prefix='ha-recovery-offline-')
        case.addCleanup(self.tmp.cleanup)
        self.store = AtomicStore(Path(self.tmp.name)/'state')
        self.now = 1000.0
        self.boot = BOOT
        paths = {r: Path(self.tmp.name)/(r+'.lock') for r in ('gpu0', 'gpu1')}
        self.a = Admission(self.store, paths, 'synthetic-scope', boot_reader=lambda: self.boot, control_required=True)
        self.a.bootstrap(dict(approval_ref='OFFLINE', bootstrap_id=self.a.snapshot()['bootstrap_id'],
                              backend_quiescent=True, cleanup_verified=True))
        initialize(self.a)
        policy = dict(samples=3, observation_timeout=30, gpu_uuids={'gpu0': 'GPU-A', 'gpu1': 'GPU-B'},
            stopped_idle_memory_mb={'gpu0': 512, 'gpu1': 64}, idle_utilization_pct=5, max_temperature_c=85,
            services={s: {'idle_memory_mb': {'gpu0': 450 if s == 'music' else 64, 'gpu1': 64}}
                      for s in ('music', 'ollama', 'wallpaper')})
        payload = {'fixture': 'never submitted'}
        request = digest(dict(target='wallpaper', operation='workflow', payload=payload))
        identity = dict(job_id='fixture-wallpaper', lease_id='fixture-lease', request_hash=request,
            boot_id=BOOT, target='wallpaper', policy_id='fixture-policy',
            physical_gpu_uuids=policy['gpu_uuids'], resources=['gpu0', 'gpu1'])
        self.original = dict(contract_sha256='fixture-contract', id='fixture-stopped', pid=0,
                             running=False, started_at='2026-11-08T14:00:00Z')
        self.job = dict(request_id=identity['job_id'], lease_id=identity['lease_id'], request_hash=request,
            boot_id=BOOT, target='wallpaper', resources=['gpu0', 'gpu1'], operation='workflow', payload=payload,
            state='reconciling', error_code='supervision_or_storage_failure',
            created_at='2026-11-08T15:00:00Z', updated_at='2026-11-08T15:00:01Z',
            recovery_status='operator_required', lifecycle_operator_error=REASON, recovery_error=REASON,
            wallpaper_lifecycle=dict(identity=dict(identity,container_id=self.original['id']), phase='prepared', original_container=self.original,
                                     created_at='2026-11-08T15:00:00Z', startup_deadline=999))
        owner = {k: identity[k] for k in ('job_id', 'lease_id', 'request_hash', 'boot_id', 'target')}
        owner.update(phase='reconciling', claimed_at='2026-11-08T15:00:00Z')
        state = self.a.snapshot()
        state.update(verified_boot=BOOT, boot_recovery_status='ready',
            owners={r: copy.deepcopy(owner) for r in ('gpu0', 'gpu1')},
            recovery_policy=dict(policy_id='fixture-policy', policy=policy),
            automatic_recovery={'fixture-incident': dict(id='fixture-incident', identity=identity,
                phase='operator_required', kind='wallpaper', observed_boot=BOOT, error=REASON)},
            recoveries=[{'approval_ref': 'historic-independent-lease'}])
        self.store.write('ownership.json', state)
        self.store.put_job(self.job)
        self.store.write_bytes('results/preserved.bin', b'preserved artifact')
        self.supervisor = NativeLock(self.store.path('supervisor.lock'))
        assert self.supervisor.acquire(0)
        case.addCleanup(self.supervisor.close)
        job_mutex = NativeLock(self.store.path('jobs.lock'))
        with job_mutex.held():
            pass
        self.runner = SimpleNamespace(admission=self.a, store=self.store, service_lock=self.supervisor,
            job_mutex=job_mutex, futures={}, pending={}, closed=False, max_pending=64,
            targets={'wallpaper': SimpleNamespace(mode_switch=None, resources=('gpu0', 'gpu1'))},
            backends={'wallpaper': SimpleNamespace(validate=lambda *a: None)}, wake=threading.Event())
        proof = dict(cleanup_verified=True, idle_ready=True, backend_stopped=True, original_execution_gone=True,
            stable_samples=3, stopped_container=self.original, queues=dict(music=dict(queue_pending=[],queue_running=[]),
            ollama=dict(models=[])), observations=dict(driver_health=dict(reported_gpu_faults=False,boot_id=BOOT),
            gpus={r: dict(uuid=policy['gpu_uuids'][r], memory_mb=434 if r == 'gpu0' else 1,
                          utilization_pct=0, temperature_c=40) for r in ('gpu0','gpu1')},
            service_memory_mb={s: dict(gpu0=422 if s=='music' else 0,gpu1=0) for s in policy['services']},
            service_identities={s: dict(id=s) for s in policy['services']}))
        state_fields = dict(Status='running', Running=True, Paused=False, Restarting=False, OOMKilled=False,
            Dead=False, Pid=123, ExitCode=0, Error='', StartedAt='stable', FinishedAt='',
            Health=dict(Status='healthy', FailingStreak=0, Log=[]))
        containers = {s: dict(Id=s, Image='sha256:fixture', State=copy.deepcopy(state_fields), RestartCount=0,
            contract='fixed', pid_birth='1234', netns=456, executable='fixture', address='fixture-'+s,
            health_fresh=True, readiness=True) for s in ('music','ollama','wallpaper')}
        containers['wallpaper']['State'].update(Status='exited',Running=False,Pid=0)
        self.observation = dict(at=self.now, boot=BOOT, source='fixture-source', configuration='fixture-config',
            isolation=dict(verified=True, binding='fixture-perimeter', coverage={k:True for k in COVERAGE},
                observer_route_verified=True, unmanaged_clients=[], gate_closed=False, denial_verified=False),
            proof=proof, backend_available=True, manual_activity=False, containers=containers)
        self.observe_hook = None
        self.observer = SimpleNamespace(observe=self.observe)
        self.control = RecoveryControl(self.runner, self.observer, b'synthetic-ticket-key-never-use-live-'*2, clock=lambda: self.now)
        self.calls = Mock(wraps=self.a.recover)
        self.a.recover = self.calls
        for target in ('subprocess.run', 'subprocess.Popen', 'urllib.request.urlopen'):
            p = patch(target, side_effect=AssertionError('production I/O forbidden'))
            p.start(); case.addCleanup(p.stop)
        self.before = copy.deepcopy(state)

    def observe(self, *, fenced):
        if self.observe_hook:
            self.observe_hook(fenced)
        value = copy.deepcopy(self.observation)
        value['at'] = self.now
        value['proof']['observed_at'] = datetime.fromtimestamp(self.now,timezone.utc).isoformat()
        value['isolation'].update(gate_closed=fenced, denial_verified=fenced)
        return value

    def token(self):
        checked = self.control.check('operator1')
        assert checked['eligible'], checked
        return checked['token']

    def run(self, token=None):
        return self.control.recover(token or self.token(), 'operator1', True)

    def gate(self):
        return read_gate(self.store, True)
