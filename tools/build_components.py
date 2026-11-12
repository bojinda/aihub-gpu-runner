"""Deterministic pure-Python wheels from a named Git commit, never worktree bytes.

Each installed component has its own package namespace and source root. Building
all components is a provenance check, not an instruction to deploy all of them.
"""
import argparse,base64,csv,hashlib,io,json,subprocess,zipfile
from pathlib import Path

COMPONENTS={
 'runner':('aihub_gpu_runner','aihub-gpu-runner','0.2.0+ollama.cleanup.budget.20261112.1'),
 'host-sdk':('components/host-sdk/aihub_gpu_runner','aihub-gpu-runner','0.1.0+ha.recovery.completion.20261110.1'),
 'privileged-helper':('components/privileged-helper/aihub_gpu_runner','aihub-gpu-runner','0.1.0+helper.binding.correction.20261110.1'),
 'comfy-plugin':('components/comfy-plugin/_aihub_recovery_comfy','aihub-recovery-comfy','0.1.0+reconstructed.20261111'),
 'client-sdk':('components/client-sdk/aihub_gpu_runner','aihub-gpu-runner','0.1.0+reliability.20261101.2'),
}

def build(repo,commit,component,out):
 def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
 sha=git('rev-parse',commit+'^{commit}').decode().strip()
 root,name,version=COMPONENTS[component];package=root.rsplit('/',1)[-1]
 paths=git('ls-tree','-r','--name-only',sha,'--',root).decode().splitlines()
 values={package+'/'+p[len(root)+1:]:git('show',sha+':'+p) for p in paths if p.endswith('.py')}
 if not values or package+'/__init__.py' not in values:raise ValueError('committed_package_missing')
 module_hashes={n:hashlib.sha256(v).hexdigest() for n,v in values.items()}
 normalized=name.replace('-','_');dist=f'{normalized}-{version}.dist-info'
 values[dist+'/METADATA']=f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: >=3.11\n'.encode()
 values[dist+'/WHEEL']=b'Wheel-Version: 1.0\nGenerator: committed-component-builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
 if component=='runner':values[dist+'/entry_points.txt']=b'[console_scripts]\naihub-gpu-runner = aihub_gpu_runner.__main__:main\n'
 values[dist+'/SOURCE.json']=json.dumps({'commit':sha,'component':component,'source_root':root,'module_sha256':module_hashes},sort_keys=True).encode()
 stream=io.StringIO(newline='');writer=csv.writer(stream,lineterminator='\n')
 for n,b in sorted(values.items()):writer.writerow([n,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b'=').decode(),len(b)])
 writer.writerow([dist+'/RECORD','','']);values[dist+'/RECORD']=stream.getvalue().encode()
 target=out/component/f'{normalized}-{version}-py3-none-any.whl';target.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for n,b in sorted(values.items()):
   info=zipfile.ZipInfo(n,date_time=(2026,1,1,0,0,0));info.external_attr=0o644<<16;info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,b)
 return dict(component=component,commit=sha,source_root=root,wheel=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),modules=module_hashes)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--commit',required=True);p.add_argument('--out',type=Path,required=True)
 p.add_argument('--component',choices=COMPONENTS,action='append');a=p.parse_args()
 repo=Path(__file__).resolve().parents[1]
 # E1 reuses the canonical runner wheel in the CPU, native-host and fixed-helper
 # environments. Other choices reproduce preserved baseline artifacts only.
 result=[build(repo,a.commit,c,a.out) for c in a.component or ('runner',)]
 print(json.dumps(result,indent=2))
