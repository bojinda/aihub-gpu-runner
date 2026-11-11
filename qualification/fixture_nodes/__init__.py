"""Disposable CPU-only nodes. Never loaded by deployed adapters."""
import os
import signal
import time
import uuid
from PIL import Image
import folder_paths

# Record only local module origins; no credentials or production observations.
def record_imports():
    if os.environ.get("AIHUB_QUALIFICATION_IMPORTS"):
        import json,sys
        from pathlib import Path
        origins={name: getattr(module,"__file__",None) for name,module in sys.modules.items()
                 if name.startswith("_aihub_recovery_comfy")}
        if origins:Path(os.environ["AIHUB_QUALIFICATION_IMPORTS"]).write_text(json.dumps(origins,indent=2))

if os.environ.get('QUALIFICATION_IGNORE_TERM')=='1':signal.signal(signal.SIGTERM,signal.SIG_IGN)
elif os.environ.get('QUALIFICATION_IGNORE_TERM')=='0':signal.signal(signal.SIGTERM,lambda *_:os._exit(143))
# 'native' deliberately leaves the actual pinned Comfy process signal handling
# untouched, including Linux PID 1's treatment of an unhandled SIGTERM.

class FixtureOutput:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'seconds':('FLOAT',{'default':0,'min':0,'max':600})}}
    RETURN_TYPES=()
    FUNCTION='execute'
    OUTPUT_NODE=True
    CATEGORY='qualification'
    def execute(self,seconds):
        record_imports()
        time.sleep(seconds)
        name='qualification-'+uuid.uuid4().hex+'.png'
        Image.new('RGB',(16,16),(20,60,90)).save(os.path.join(folder_paths.get_output_directory(),name))
        return {'ui':{'images':[{'filename':name,'subfolder':'','type':'output'}]}}

class FixtureConditioningIdentity:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{k:('STRING',) for k in ('clip_name','gpu0_uuid','gpu1_uuid')}}
    RETURN_TYPES=('STRING',)
    FUNCTION='unused'
    CATEGORY='qualification'
    def unused(self,**kwargs):raise AssertionError('The qualification graph must not execute conditioning.')

NODE_CLASS_MAPPINGS={'FixtureOutput':FixtureOutput,'AIHubQwenGPU1CLIPLoader':FixtureConditioningIdentity}
