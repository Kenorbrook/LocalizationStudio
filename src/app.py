"""Source CLI and public application entry points."""

import argparse
from core import Store, dump
from paths import DEFAULT_DB
from commands import dispatch as api
from http_server import serve
from job_runtime import launch, run_limits, validate_job_settings
from queries import state, records, queue_page, record_context, job_log

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--worker", type=int)
    parser.add_argument("--import-project")
    args = parser.parse_args()
    store = Store(args.db)
    if args.worker:
        from worker import run

        run(store, args.worker)
    elif args.import_project:
        print(dump(api(store, "project", {"root": args.import_project, "scan": True})))
    else:
        serve(store, args.port, args.open)
