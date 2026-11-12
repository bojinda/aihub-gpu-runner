"""One installation-only conversion, default read-only. Never called by startup/HA."""
import argparse,hashlib,json
from pathlib import Path
from aihub_gpu_runner.config import admission_from_config
from aihub_gpu_runner.estop import compact_history,compact_state

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',required=True);p.add_argument('--backup',required=True)
    p.add_argument('--expected-sha256',required=True);p.add_argument('--apply',action='store_true')
    p.add_argument('--confirmed-stopped',action='store_true');args=p.parse_args()
    _,_,a=admission_from_config(args.config)
    if args.apply:
        try:result=compact_history(a,args.backup,args.expected_sha256,confirmed_stopped=args.confirmed_stopped)
        except OSError:
            print(json.dumps({'outcome':'unknown','error':'compaction_outcome_unknown_inspect_do_not_retry'}))
            return 1
    else:
        raw=a.store.path('ownership.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=args.expected_sha256 or Path(args.backup).read_bytes()!=raw:
            raise ValueError('exact_backup_or_state_mismatch')
        new=compact_state(json.loads(raw))
        result=dict(outcome='dry_run',before_bytes=len(raw),after_bytes=len(json.dumps(new,separators=(',',':')).encode()),
                    preserved_leases=len({o['lease_id'] for o in new['owners'].values()}),mutations=0)
    print(json.dumps(result))

if __name__=='__main__':
    raise SystemExit(main())
