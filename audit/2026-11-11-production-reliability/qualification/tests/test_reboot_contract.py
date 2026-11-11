"""Offline reviewed reboot transition: real journals/flocks, synthetic probes only."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import Target, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, RecoveryRequired
from aihub_gpu_runner.recovery import RecoveryManager, RecoveryObserver, authorize_policy, digest, PersistenceUncertain
from aihub_gpu_runner.reboot_contract import recover_reboot_contract, REVIEW_FLAGS
from tests import test_automatic_recovery as f
from tests import test_container_contract as contracts
from tests.test_wallpaper_conditioning import WORKFLOW, GPU0, GPU1


class Clock:
    def __init__(self): self.t = 100.
    def monotonic(self): return self.t
    def sleep(self, duration): self.t += duration


class Fixture:
    def __init__(self, case):
        self.case = case
        tmp = tempfile.TemporaryDirectory(prefix='offline-reboot-contract-')
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.boot = [f.BOOT1]; self.clock = Clock()
        for target, replacement in (('time.monotonic', self.clock.monotonic),
                                    ('time.sleep', self.clock.sleep),
                                    ('aihub_gpu_runner.recovery.guest_boot_id', lambda: self.boot[0]),
                                    ('subprocess.run', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('live subprocess forbidden')))):
            p = patch(target, replacement); p.start(); case.addCleanup(p.stop)
        self.store = AtomicStore(self.root/'state')
        self.gate = Admission(self.store, {r:self.root/(r+'.lock') for r in ('gpu0','gpu1')},
                              'offline-review', boot_reader=lambda:self.boot[0])
        self.gate.bootstrap({'approval_ref':'OFFLINE', 'bootstrap_id':self.gate.snapshot()['bootstrap_id'],
                             'backend_quiescent':True,'cleanup_verified':True})
        self.targets = {
            'wallpaper':Target('wallpaper','comfy',('gpu0','gpu1'),'http://offline.invalid',
                workflow=WORKFLOW,output_node='2',idle_memory_mb=20,conditioning_idle_memory_mb=10,
                lifecycle='on_demand',startup_timeout=2,shutdown_timeout=2),
            'music':Target('music','comfy',('gpu0',),'http://offline.invalid',workflow=WORKFLOW,output_node='2'),
            'ollama':Target('ollama','ollama',('gpu1',),'http://offline.invalid',models=('offline',))}
        self.metadata = {s:contracts.metadata(s,c) for s,c in (('wallpaper','a'),('music','b'),('ollama','e'))}
        for s,m in self.metadata.items():
            m['HostConfig'].update(Privileged=False,Devices=[],DeviceRequests=[{'Driver':'nvidia','Count':0,
                'DeviceIDs':[GPU0,GPU1],'Capabilities':[['gpu']],'Options':{}}])
            if s!='wallpaper': m['State'].update(Running=True,Pid=303 if s=='music' else 404)
        self.p = f.policy(self.gate)
        self.p['services']['ollama'] = copy.deepcopy(self.p['services']['music'])
        self.p.update(boot_startup='stopped_idle',stopped_idle_memory_mb={'gpu0':8,'gpu1':4},samples=3)
        for s,b in self.p['services'].items():
            m = self.metadata[s]; b.update(id=m['Id'],name=m['Name'][1:],image=m['Image'],contract_sha256=RecoveryObserver.contract(m))
        self.wall_busy=False; self.queue_busy=False; self.api_error=False
        self.apps=GPU0+',303,3\n'; self.rows={}; self.kernel='synthetic healthy kernel'; self.commands=[]
        fixture=self
        class Transport:
            def json(self, method, route, payload=None, timeout=5):
                case.assertEqual(method,'GET','inference/control forbidden')
                if fixture.api_error: raise Uncertain('synthetic_connection_failure')
                if route=='/queue':return {'queue_running':[[0,'foreign']] if fixture.queue_busy else [],'queue_pending':[]}
                if route=='/system_stats':return {'system':{'synthetic':True}}
                if route=='/api/ps':return {'models':[]}
                if route=='/api/version':return {'version':'synthetic'}
                raise AssertionError(route)
        self.backends={s:SimpleNamespace(transport=Transport()) for s in self.targets}
        authorize_policy(self.gate,self.targets,self.backends,self.p,'OFFLINE',self.observer(self.p))
        lease=self.gate.try_acquire('interrupted','request-hash',('gpu0','gpu1'),'wallpaper')
        self.owner=copy.deepcopy(lease.owner); lease.mark('running'); lease.close()
        original=self.observer(self.p).container('wallpaper',101)
        original.update(running=True,pid=2147483000)
        self.store.put_job({'request_id':'interrupted','request_hash':'request-hash','lease_id':self.owner['lease_id'],
            'target':'wallpaper','resources':['gpu0','gpu1'],'boot_id':f.BOOT1,'state':'running',
            'execution_container':original,'execution_processes':{'2147483000':'1'},
            'wallpaper_lifecycle':{'phase':'submit_intent','identity':dict(self.owner,
                resources=['gpu0','gpu1'],policy_id=digest(self.p),container_id='a'*64,
                physical_gpu_uuids=self.p['gpu_uuids'])}})
        j=self.store.job('interrupted')
        # lifecycle identity is binding(owner), not mutable claim phase/timestamp.
        j['wallpaper_lifecycle']['identity']={k:v for k,v in j['wallpaper_lifecycle']['identity'].items()
                                            if k not in ('phase','claimed_at')}
        self.store.put_job(j)
        self.store.put_job({'request_id':'historical','state':'success','value':'preserve-me'})
        self.store.write('results/historical.json',{'value':'original-result'})
        state=self.gate.snapshot();state['recoveries']=[{'historical':'retain'}];state['recovery_budget']=[{'historical':'retain'}]
        self.store.write('ownership.json',state)
        self.metadata['wallpaper']['Config']['Labels']['after_reboot']='requires-explicit-review'
        self.boot[0]=f.BOOT2
        RecoveryManager(self.gate,self.targets,self.backends,self.observer(self.p)).tick(force=True)
        self.before=copy.deepcopy(self.gate.snapshot()); self.job_before=self.store.job('interrupted')
        case.assertEqual(self.before['boot_recovery_error'],'container_identity_or_supervision_changed')
        self.reviewed=copy.deepcopy(self.metadata['wallpaper'])
        self.evidence=dict({k:True for k in REVIEW_FLAGS},approval_ref='OFFLINE-EXPLICIT-REVIEW',
            job_id='interrupted',request_hash='request-hash',lease_id=self.owner['lease_id'],target='wallpaper',
            previous_boot=f.BOOT1,current_boot=f.BOOT2,scope=self.gate.scope,policy_id=digest(self.p),
            old_contract_sha256=self.p['services']['wallpaper']['contract_sha256'],
            new_contract_sha256=RecoveryObserver.contract(self.reviewed),container_id='a'*64,
            image=self.reviewed['Image'],gpu_uuids=copy.deepcopy(self.p['gpu_uuids']),
            lock_identity=copy.deepcopy(self.before['lock_identity']),reviewed_metadata_sha256=digest(self.reviewed))
        self.candidate=copy.deepcopy(self.p)
        self.candidate['services']['wallpaper']['contract_sha256']=self.evidence['new_contract_sha256']
        self.commands=[]

    def observer(self,p): return RecoveryObserver(p,self.targets,self.backends,run=self.run,poll_interval=.1)
    def run(self,argv,**kwargs):
        self.commands.append(argv)
        if argv[0]==self.p['journalctl']:
            raw=json.dumps({'_BOOT_ID':self.boot[0].replace('-',''),'_TRANSPORT':'kernel','MESSAGE':self.kernel})
        elif argv[0]==self.p['docker']:
            if argv[1]=='inspect': raw=json.dumps([m for m in self.metadata.values() if argv[2] in (m['Id'],m['Name'][1:])])
            elif argv[1]=='top':raw='PID\n'+('303' if argv[2]=='b'*64 else '404')
            else:raise AssertionError('Docker control forbidden: '+repr(argv))
        elif 'query-compute-apps' in argv[1]:raw=self.apps
        else:
            uuid=argv[1].split('=',1)[1];raw=self.rows.get(uuid,uuid+',3,0,40,[N/A],[N/A]')
        return SimpleNamespace(returncode=0,stdout=raw)
    def recover(self, **kwargs):
        return recover_reboot_contract(self.gate,self.targets,self.backends,self.evidence,self.reviewed,
                                      observer=self.observer(self.candidate),**kwargs)
    def held(self):
        self.case.assertEqual(self.gate.snapshot()['owners'],self.before['owners'])
        self.case.assertEqual(self.gate.snapshot()['verified_boot'],f.BOOT1)
        self.case.assertEqual(self.gate.snapshot()['recovery_policy'],self.before['recovery_policy'])
    def completed(self):
        state=self.gate.snapshot();self.case.assertEqual(state['owners'],{})
        self.case.assertEqual(state['verified_boot'],f.BOOT2)
        self.case.assertEqual(state['boot_recovery_status'],'ready')
        self.case.assertEqual(state['recovery_policy']['policy'],self.candidate)
        self.case.assertEqual(len(state['recoveries']),2)
        audit=state['recoveries'][-1]['operator_contract_transition']
        self.case.assertEqual(audit['approval'],self.evidence)
        self.case.assertEqual(audit['previous_recovery_policy'],self.before['recovery_policy'])
        self.case.assertEqual(audit['original_refusal']['boot_recovery_error'],'container_identity_or_supervision_changed')
        j=self.store.job('interrupted')
        for k,v in self.job_before.items():
            if k!='updated_at':self.case.assertEqual(j[k],v)
        self.case.assertEqual(state['lock_identity'],self.before['lock_identity'])
        self.case.assertEqual(state['recovery_budget'],self.before['recovery_budget'])
        self.case.assertEqual(state['recoveries'][0],self.before['recoveries'][0])
        self.case.assertEqual(self.store.job('historical'),{'request_id':'historical','state':'success','value':'preserve-me'})
        self.case.assertEqual(self.store.read('results/historical.json'),{'value':'original-result'})
        self.case.assertFalse((self.store.path('results/interrupted.json')).exists())


class RebootContractTests(unittest.TestCase):
    def setUp(self):self.c=Fixture(self)
    def test_success_preserves_job_results_history_and_atomically_opens_boot_gate(self):
        c=self.c; original=c.store.write; writes=[]
        def write(name,value):
            if name=='ownership.json':
                owners=value['owners'];self.assertIn(set(owners),({'gpu0','gpu1'},set()))
                if not owners:
                    self.assertEqual(value['verified_boot'],f.BOOT2)
                    self.assertEqual(value['recovery_policy']['policy'],c.candidate)
                    self.assertEqual(len(value['recoveries']),2)
            writes.append(name);return original(name,value)
        self.assertIsNone(c.gate.try_acquire('early','hash',('gpu0',),'wallpaper'))
        with patch.object(c.store,'write',side_effect=write):self.assertFalse(c.recover()['already_recorded'])
        c.completed();before={str(p):p.read_bytes() for p in c.store.root.rglob('*.json')}
        self.assertTrue(c.recover()['already_recorded']);self.assertEqual(before,{str(p):p.read_bytes() for p in c.store.root.rglob('*.json')})
        lease=c.gate.try_acquire('late','hash',('gpu0',),'wallpaper');self.assertIsNotNone(lease);lease.close()
        self.assertTrue(all(cmd[1] in ('inspect','top') for cmd in c.commands if cmd[0]==c.p['docker']))

    def test_each_approval_binding_and_explicit_review_flag_is_required(self):
        c=self.c
        for key in c.evidence:
            with self.subTest(key=key):
                original=copy.deepcopy(c.evidence)
                c.evidence[key]=False if key in REVIEW_FLAGS else '' if key=='approval_ref' else 'incorrect'
                with self.assertRaises((RecoveryRequired,ValueError,KeyError,TypeError,AttributeError)):c.recover()
                c.held();c.evidence=original

    def test_unreviewed_executable_mount_gpu_or_hostname_change_is_rejected(self):
        c=self.c
        mutations=[lambda m:m['Config'].update(Cmd=['different-command']),
                   lambda m:m['Mounts'][0].update(RW=False),
                   lambda m:m['HostConfig']['DeviceRequests'][0].update(DeviceIDs=[GPU1]),
                   lambda m:m['Config'].update(Hostname='explicit-other')]
        for change in mutations:
            old=copy.deepcopy(c.metadata['wallpaper']);change(c.metadata['wallpaper'])
            with self.assertRaises(RecoveryRequired):c.recover()
            c.held();c.metadata['wallpaper']=old

    def test_even_explicit_review_cannot_authorize_unbounded_gpu_exposure(self):
        c=self.c;c.reviewed['HostConfig']['DeviceRequests'][0]['DeviceIDs']=[]
        c.reviewed['HostConfig']['DeviceRequests'][0]['Count']=-1
        c.evidence['reviewed_metadata_sha256']=digest(c.reviewed)
        c.evidence['new_contract_sha256']=RecoveryObserver.contract(c.reviewed)
        with self.assertRaisesRegex(RecoveryRequired,'explicit_paired_gpu_exposure'):c.recover()
        c.held()

    def test_observed_aihub_cdi_uuid_envelope_can_be_explicitly_reviewed(self):
        c=self.c
        request=[{'Driver':'cdi','Count':0,'DeviceIDs':['nvidia.com/gpu='+u for u in (GPU0,GPU1)],
                  'Capabilities':None,'Options':None}]
        c.reviewed['HostConfig']['DeviceRequests']=copy.deepcopy(request)
        c.metadata['wallpaper']['HostConfig']['DeviceRequests']=copy.deepcopy(request)
        c.evidence['reviewed_metadata_sha256']=digest(c.reviewed)
        c.evidence['new_contract_sha256']=RecoveryObserver.contract(c.reviewed)
        c.candidate['services']['wallpaper']['contract_sha256']=c.evidence['new_contract_sha256']
        c.recover();c.completed()

    def test_cdi_all_foreign_duplicate_reordered_extra_or_changed_envelopes_are_refused(self):
        c=self.c
        original={'Driver':'cdi','Count':0,'DeviceIDs':['nvidia.com/gpu='+u for u in (GPU0,GPU1)],
                  'Capabilities':None,'Options':None}
        mutations=[{'DeviceIDs':['nvidia.com/gpu=all']},{'DeviceIDs':['nvidia.com/gpu='+GPU0]*2},
                   {'DeviceIDs':['nvidia.com/gpu='+GPU1,'nvidia.com/gpu='+GPU0]},
                   {'DeviceIDs':['nvidia.com/gpu='+GPU0,'nvidia.com/gpu=GPU-33333333-3333-4333-8333-333333333333']},
                   {'Count':-1},{'Capabilities':[['gpu']]},{'Options':{}},{'Driver':'other'},
                   {'ExtraField':'unreviewed-extension'}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                request=dict(copy.deepcopy(original),**mutation)
                c.reviewed['HostConfig']['DeviceRequests']=[request]
                c.evidence['reviewed_metadata_sha256']=digest(c.reviewed)
                c.evidence['new_contract_sha256']=RecoveryObserver.contract(c.reviewed)
                with self.assertRaisesRegex(RecoveryRequired,'explicit_paired_gpu_exposure'):c.recover()
                c.held()
        c.reviewed['HostConfig']['DeviceRequests']=[original,original]
        c.evidence['reviewed_metadata_sha256']=digest(c.reviewed)
        c.evidence['new_contract_sha256']=RecoveryObserver.contract(c.reviewed)
        with self.assertRaisesRegex(RecoveryRequired,'explicit_paired_gpu_exposure'):c.recover()
        c.held()

    def test_running_paused_restarting_dead_and_changed_generation_are_rejected(self):
        c=self.c
        for update in ({'Running':True,'Pid':900},{'Paused':True},{'Restarting':True},{'Dead':True},{'StartedAt':'other'}):
            old=copy.deepcopy(c.metadata['wallpaper']['State']);c.metadata['wallpaper']['State'].update(update)
            with self.assertRaises(RecoveryRequired):c.recover()
            c.held();c.metadata['wallpaper']['State']=old

    def test_busy_queues_gpus_foreign_processes_unavailable_probes_and_driver_faults_retain_both(self):
        c=self.c
        mutations=[('queue_busy',True),('api_error',True),('apps',GPU0+',999,3'),
                   ('rows',{GPU0:GPU0+',999,0,40,0,No'}),('rows',{GPU1:GPU1+',3,99,40,0,No'}),
                   ('rows',{GPU1:GPU1+',3,0,99,0,No'}),('rows',{GPU1:GPU1+',3,0,40,1,No'}),
                   ('rows',{GPU1:GPU1+',nan,0,40,0,No'}),('kernel','NVRM: Xid fatal GPU lost')]
        for field,value in mutations:
            with self.subTest(field=field,value=value):
                original=copy.deepcopy(getattr(c,field));setattr(c,field,value)
                with self.assertRaises((RecoveryRequired,Uncertain)):c.recover()
                c.held();setattr(c,field,original)

    def test_stale_or_partial_leases_boot_changes_and_lock_drift_fail_closed(self):
        c=self.c
        for mutation in ('lease','one_owner','boot','verified_boot','lock','job'):
            with self.subTest(mutation=mutation):
                state=copy.deepcopy(c.before)
                if mutation=='lease':state['owners']['gpu1']['lease_id']='different'
                if mutation=='one_owner':del state['owners']['gpu1']
                if mutation=='boot':c.boot[0]=f.BOOT1
                if mutation=='verified_boot':state['verified_boot']=f.BOOT2
                if mutation=='lock':state['lock_identity']['gpu0']['inode']+=1
                if mutation=='job':
                    j=c.store.job('interrupted');j['lease_id']='stale';c.store.put_job(j)
                c.store.write('ownership.json',state)
                with self.assertRaises(RecoveryRequired):c.recover()
                self.assertEqual(c.gate.snapshot()['owners'],state['owners'])
                c.boot[0]=f.BOOT2;c.store.write('ownership.json',c.before);c.store.put_job(c.job_before)

    def test_supervisor_recovery_and_original_physical_locks_exclude_command(self):
        c=self.c
        for path in (c.store.path('supervisor.lock'),c.store.path('recovery.lock'),c.gate.lock_paths['gpu0']):
            with NativeLock(path,create=False).held(0):
                with self.assertRaises((TimeoutError,RecoveryRequired)):c.recover()
            c.held()

    def test_configuration_or_boot_change_during_journal_writes_cannot_commit(self):
        c=self.c;original=c.store.write
        for field in ('configuration','boot'):
            fired=[]
            def write(name,value):
                original(name,value)
                if name.startswith('jobs/') and not fired:
                    fired.append(True)
                    if field=='configuration':c.metadata['wallpaper']['Config']['Cmd']=['changed-during-write']
                    else:c.boot[0]=f.BOOT1
            with patch.object(c.store,'write',side_effect=write):
                with self.assertRaises(RecoveryRequired):c.recover()
            c.held();c.boot[0]=f.BOOT2
            c.metadata['wallpaper']=copy.deepcopy(c.reviewed)

    def test_process_survival_rejects_cleanup(self):
        c=self.c
        with patch.object(RecoveryObserver,'gone',side_effect=RecoveryRequired('original_container_process_survives')):
            with self.assertRaises(RecoveryRequired):c.recover()
        c.held()

    def test_every_write_interruption_and_uncertain_response_resumes_without_duplicate_release(self):
        # Four durable writes: incident, proof, job proof, atomic authority/release.
        for error in (f.Crash,OSError):
            for when in ('before','after'):
                for cut in range(1,5):
                    with self.subTest(error=error.__name__,when=when,cut=cut):
                        c=Fixture(self);original=c.store.write;count=[0]
                        def write(name,value):
                            count[0]+=1;hit=count[0]==cut
                            if hit and when=='before':raise error('synthetic write interruption')
                            original(name,value)
                            if hit and when=='after':raise error('synthetic response lost')
                        with patch.object(c.store,'write',side_effect=write):
                            with self.assertRaises((f.Crash,PersistenceUncertain)):c.recover()
                        # Reopen durable state as a restarted process would; the
                        # ordinary runner must never resume an uncommitted approval.
                        c.gate=Admission(AtomicStore(c.store.root),c.gate.lock_paths,c.gate.scope,
                                         require_existing_locks=True,boot_reader=lambda:c.boot[0])
                        if c.gate.snapshot()['owners']:c.held()
                        else:c.completed()
                        before=c.store.path('ownership.json').read_bytes()
                        RecoveryManager(c.gate,c.targets,c.backends,c.observer(c.gate.snapshot()['recovery_policy']['policy'])).startup()
                        self.assertEqual(c.store.path('ownership.json').read_bytes(),before)
                        c.recover();c.completed();c.recover();c.completed()

    def test_atomic_replace_and_directory_fsync_failures_are_outcome_uncertain(self):
        for where in (('before_replace','after_replace','directory_fsync') if os.name!='nt'
                      else ('before_replace','after_replace')):
            with self.subTest(where=where):
                c=Fixture(self);real_write=c.store.write;real_replace=os.replace;real_fsync=os.fsync
                count=[0]
                def write(name,value):
                    count[0]+=1
                    if count[0]!=4:return real_write(name,value)
                    def replace(src,dest):
                        if where=='before_replace':raise OSError('rename failed')
                        real_replace(src,dest)
                        if where=='after_replace':raise OSError('rename response lost')
                    fsyncs=[0]
                    def fsync(fd):
                        fsyncs[0]+=1
                        if where=='directory_fsync' and fsyncs[0]==2:raise OSError('directory flush failed')
                        return real_fsync(fd)
                    with patch('os.replace',side_effect=replace),patch('os.fsync',side_effect=fsync):return real_write(name,value)
                with patch.object(c.store,'write',side_effect=write):
                    with self.assertRaises(PersistenceUncertain):c.recover()
                c.recover();c.completed()

    def test_lost_final_flush_cannot_be_acknowledged_until_receipt_flush_succeeds(self):
        c=self.c;c.recover();c.completed()
        before=c.store.path('ownership.json').read_bytes()
        with patch('os.fsync',side_effect=OSError('receipt flush unavailable')):
            with self.assertRaises(PersistenceUncertain):c.recover()
        self.assertEqual(c.store.path('ownership.json').read_bytes(),before)
        self.assertTrue(c.recover()['already_recorded']);c.completed()

    def test_cli_uses_reviewed_transition_and_never_legacy_force_recovery(self):
        from aihub_gpu_runner.__main__ import main
        c=self.c;e=c.root/'approval.json';r=c.root/'review.json'
        e.write_text(json.dumps(c.evidence));r.write_text(json.dumps([c.reviewed]))
        init=RecoveryObserver.__init__
        def observer_init(observer,p,targets,backends,**kwargs):
            init(observer,p,targets,backends,run=c.run,poll_interval=.1)
        args=['runner','--config','synthetic.json','recover-reboot-contract','--job-id','interrupted',
              '--evidence-file',str(e),'--container-review-file',str(r)]
        output=io.StringIO()
        with patch('sys.argv',args),patch('aihub_gpu_runner.__main__.admission_from_config',return_value=({},c.targets,c.gate)), \
                patch('aihub_gpu_runner.config.configured_backends',return_value=c.backends), \
                patch.object(RecoveryObserver,'__init__',observer_init), \
                patch.object(c.gate,'recover',side_effect=AssertionError('legacy force release forbidden')),redirect_stdout(output):
            main()
        self.assertFalse(json.loads(output.getvalue())['backend_action_performed']);c.completed()

    def test_cli_refuses_policy_override_and_mismatched_job(self):
        from aihub_gpu_runner.__main__ import main
        c=self.c;e=c.root/'approval.json';r=c.root/'review.json'
        e.write_text(json.dumps(c.evidence));r.write_text(json.dumps([c.reviewed]))
        args=['runner','--config','synthetic.json','recover-reboot-contract','--job-id','interrupted',
              '--evidence-file',str(e),'--container-review-file',str(r)]
        for extra in (['--policy-file','arbitrary.json'],['--rollback'],['--new-config','other.json'],['--job-id','other']):
            with patch('sys.argv',args+extra),patch('aihub_gpu_runner.__main__.admission_from_config',return_value=({},c.targets,c.gate)),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):main()
            c.held()

    def test_completed_approval_cannot_release_a_new_lease_or_cross_another_boot(self):
        c=self.c;c.recover();c.completed()
        lease=c.gate.try_acquire('new-job','new-hash',('gpu0','gpu1'),'wallpaper');lease.close()
        before=copy.deepcopy(c.gate.snapshot()['owners'])
        self.assertTrue(c.recover()['already_recorded'])
        self.assertEqual(c.gate.snapshot()['owners'],before)
        c.boot[0]='33333333-3333-4333-8333-333333333333'
        with self.assertRaises(RecoveryRequired):c.recover()
        self.assertEqual(c.gate.snapshot()['owners'],before)
