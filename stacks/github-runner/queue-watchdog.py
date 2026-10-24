#!/usr/bin/env python3
"""Find Dealpipe jobs GitHub left queued while a matching NAS runner sits idle.

On 2026-10-24 a diagnostic temporarily removed `dealpipe-nas-ci` from the
production runner. A job queued in that window never started after the label
returned; GitHub showed an idle online runner and a job "waiting" for 40 min.
Runner re-registration or replacement during an operator batch can orphan jobs
the same way.

Report-only by default; exit status 1 means orphans were found. `--redispatch`
cancels and reruns failed jobs of an affected run, only when none of the run's
jobs is still in progress, so running work is never cancelled. Uses the
caller's `gh` login, which must be allowed to list repository runners.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import time

REPOSITORY = "dealpipe/dealpipe"


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=60).stdout


def api(path):
    return json.loads(gh("api", path))


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def idle_label_sets(repository, call=api):
    runners = call(f"repos/{repository}/actions/runners?per_page=100")["runners"]
    return [{label["name"] for label in r["labels"]}
            for r in runners if r["status"] == "online" and not r["busy"]]


def orphans(repository, now, threshold, call=api):
    idle = idle_label_sets(repository, call)
    found = []
    for status in ("queued", "in_progress"):
        runs = call(f"repos/{repository}/actions/runs?status={status}&per_page=100")["workflow_runs"]
        for run in runs:
            jobs = call(f"repos/{repository}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
            busy = any(job["status"] == "in_progress" for job in jobs)
            for job in jobs:
                if job["status"] != "queued" or not job["labels"]:
                    continue
                if now - parse_time(job["created_at"]) < threshold:
                    continue
                # Busy matching runners mean ordinary queueing, not an orphan.
                if not any(set(job["labels"]) <= labels for labels in idle):
                    continue
                found.append({"run": run["id"], "job": job["id"], "name": job["name"],
                              "labels": job["labels"], "queuedSince": job["created_at"],
                              "runHasActiveJobs": busy, "url": job["html_url"]})
    return found


def redispatch(repository, run_id, run=gh, call=api, sleep=time.sleep, timeout=180):
    run("run", "cancel", str(run_id), "-R", repository)
    deadline = time.monotonic() + timeout
    while call(f"repos/{repository}/actions/runs/{run_id}")["status"] != "completed":
        if time.monotonic() > deadline:
            raise TimeoutError(f"Run {run_id} did not finish cancelling; rerun it manually")
        sleep(5)
    # Reruns failed and cancelled jobs with their dependents; passed jobs stay.
    run("run", "rerun", str(run_id), "--failed", "-R", repository)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--minutes", type=int, default=15, help="queued age before reporting")
    parser.add_argument("--redispatch", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    found = orphans(args.repository, now, timedelta(minutes=args.minutes))
    for item in found:
        print(json.dumps(item), flush=True)
    if args.redispatch:
        for run_id in sorted({item["run"] for item in found}):
            if any(item["runHasActiveJobs"] for item in found if item["run"] == run_id):
                print(json.dumps({"run": run_id, "action": "skipped: other jobs still running"}))
                continue
            redispatch(args.repository, run_id)
            print(json.dumps({"run": run_id, "action": "cancelled and reran failed jobs"}))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
