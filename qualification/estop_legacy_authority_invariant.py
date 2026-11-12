"""Desired invariant, currently FAILING: unchanged legacy cached writer fenced.

Run only as an offline test. Uses an actual preserved SDK interpreter in a real
subprocess and a TemporaryDirectory. No production path or configuration accepted.
This demonstrates write capability; it does not assert a production incident used
this exact cached-state call sequence. Failure is an E1 stop, not a passing repair.
"""
import argparse,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from aihub_gpu_runner.core import AtomicStore,NativeLock

LEGACY=None
def wait_for(path):
    end=time.monotonic()+8
    while time.monotonic()<end:
        if path.exists():return
        time.sleep(.005)
    raise AssertionError('fixture_handshake_timeout')

def old_writer(root):
    root=Path(root);assert root.name.startswith('estop-authority-')
    s=AtomicStore(root);cached=s.read('ownership.json')
    (root/'cached').write_text('ready')
    wait_for(root/'write-now')
    # Real old API, even under the same OS admission mutex. It has no CAS/epoch.
    with NativeLock(root/'admission.lock').held():s.write('ownership.json',cached)
    (root/'legacy-origin.json').write_text(json.dumps({'python':sys.executable,'store_origin':__import__('inspect').getfile(AtomicStore)}))

class AuthoritativeGenerationInvariant(unittest.TestCase):
    def test_legacy_cached_write_cannot_replace_committed_authority(self):
        with tempfile.TemporaryDirectory(prefix='estop-authority-') as tmp:
            root=Path(tmp);s=AtomicStore(root)
            s.write('ownership.json',{'schema':1,'scope':'synthetic','ready':True,'owners':{'gpu1':{'job_id':'old','lease_id':'old-lease'}}})
            env=dict(os.environ);env.pop('PYTHONPATH',None)
            p=subprocess.Popen([LEGACY,'-I','-B',str(Path(__file__).resolve()),'--old-writer',str(root)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                wait_for(root/'cached')
                with NativeLock(root/'admission.lock').held():
                    s.write('ownership.json',{'schema':2,'scope':'synthetic','ready':True,'owners':{},
                        'admission_generation':{'id':'a'*32},'estop_audits':[{'operation':'b'*32}]})
                committed=s.path('ownership.json').read_bytes();(root/'write-now').touch()
                stdout,stderr=p.communicate(timeout=10)
                self.assertEqual(p.returncode,0,(stdout+stderr).decode())
                after=s.path('ownership.json').read_bytes()
                print(json.dumps({'legacy':json.loads((root/'legacy-origin.json').read_text()),
                    'committed_schema':2,'observed_schema':s.read('ownership.json')['schema'],
                    'committed_generation_survived':after==committed,'backend_operations':0}),flush=True)
                self.assertEqual(after,committed,'BLOCKER: real legacy AtomicStore replaced committed authority and removed generation/audit')
            finally:
                if p.poll() is None:p.kill();p.communicate()

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--old-writer':old_writer(sys.argv[2])
    else:
        parser=argparse.ArgumentParser();parser.add_argument('--legacy-python',required=True)
        args=parser.parse_args();LEGACY=args.legacy_python
        unittest.main(argv=[sys.argv[0]],verbosity=2)
