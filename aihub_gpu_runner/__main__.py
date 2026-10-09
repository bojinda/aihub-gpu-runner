"""Local operator actions; no remote recovery endpoint, deployment or job trigger."""
import argparse
import json
import os
from .api import Application, serve
from .config import admission_from_config, make_runner
from .core import strict_json, timestamp

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("action", choices=("inspect", "bootstrap", "recover", "serve", "transition-scope", "dual-restore"))
    parser.add_argument("--evidence-file")
    parser.add_argument("--job-id")
    parser.add_argument("--new-config")
    parser.add_argument("--rollback", action='store_true')
    args = parser.parse_args()
    if args.action == 'transition-scope':
        if not args.new_config or not args.evidence_file:
            parser.error('new config and local operator evidence required')
        from .scope_transition import transition_scope
        with open(args.evidence_file, 'rb') as stream:
            evidence = strict_json(stream.read())
        print(json.dumps(transition_scope(args.config, args.new_config, evidence, rollback=args.rollback)))
        return
    if args.action == "serve":
        value, runner = make_runner(args.config)
        try:
            token = os.environ.get(value.get("auth_token_env", "AIHUB_GPU_RUNNER_TOKEN"), "")
            if len(token) < 32:
                raise ValueError("high_entropy_internal_token_required")
            listen = value.get("listen", {})
            application = Application(runner, token)
            serve(application, listen.get("host", "127.0.0.1"), listen.get("port", 8790))
        finally:
            runner.close()
        return
    _, targets, admission = admission_from_config(args.config)
    if args.action == "inspect":
        print(json.dumps(admission.snapshot(), indent=2))
        return
    if not args.evidence_file:
        parser.error("local operator evidence file is required")
    with open(args.evidence_file, "rb") as stream:
        evidence = strict_json(stream.read())
    if args.action == "bootstrap":
        admission.bootstrap(evidence)
    elif args.action == 'dual-restore':
        if not args.job_id:
            parser.error('exact held job ID required')
        from .dual_gpu import restore_held
        job = admission.store.job(args.job_id)
        proof = restore_held(admission, targets[job['target']], args.job_id, evidence)
        print(json.dumps({'recorded': 'dual-restore', 'owners_retained': True, 'proof': proof}))
        return
    else:
        if not args.job_id:
            parser.error("job ID is required")
        admission.recover(args.job_id, evidence)
        job = admission.store.read(admission.store.job_name(args.job_id))
        if job:
            job.update(state="failed", error_code="operator_recovered_no_automatic_resubmission",
                       updated_at=timestamp())
            if job.get("host_stage") is True:
                job["host_exit_code"] = 70
            admission.store.put_job(job)
    print(json.dumps({"recorded": args.action, "backend_action_performed": False}))

if __name__ == "__main__":
    main()
