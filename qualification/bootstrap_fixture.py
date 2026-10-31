"""Prepare the exact deployed core fixture for the offline old-reader regression."""
import hashlib,json,pathlib,subprocess
root=pathlib.Path(__file__).resolve().parents[1]
revision='4ebbc624cbc14301a337081a276687fe87bbaac5'
expected='e99b2fc30b41b947376b2c6c01d9c8bdb7cc3442705cd0cace33ff0a0b51c185'
data=subprocess.check_output(['git','-C',str(root),'show',revision+':aihub_gpu_runner/core.py'])
if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('base_fixture_hash_mismatch')
p=root/'qualification/baseline-core.py'
if p.exists():
    if p.read_bytes()!=data:raise RuntimeError('preserve_mismatched_base_fixture')
else:p.write_bytes(data)
p.with_suffix('.sha256.json').write_text(json.dumps({'revision':revision,'sha256':expected})+'\n')
print('Exact deployed core fixture verified; no product or production changes.')
