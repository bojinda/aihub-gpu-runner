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
    parser.add_argument("action", choices=("inspect", "bootstrap", "recover", "serve"))
    parser.add_argument("--evidence-file")
    parser.add_argument("--job-id")
    args = parser.parse_args()
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
    _, _, admission = admission_from_config(args.config)
    if args.action == "inspect":
        print(json.dumps(admission.snapshot(), indent=2))
        return
    if not args.evidence_file:
        parser.error("local operator evidence file is required")
    with open(args.evidence_file, "rb") as stream:
        evidence = strict_json(stream.read())
    if args.action == "bootstrap":
        admission.bootstrap(evidence)
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
