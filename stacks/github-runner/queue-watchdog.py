#!/usr/bin/env python3
"""Find Dealpipe jobs GitHub left queued while a matching NAS runner sits idle.

On 2026-10-24 a diagnostic temporarily removed `dealpipe-nas-ci` from the
production runner. A job queued in that window never started after the label
returned; GitHub showed an idle online runner and a job "waiting" for 40 min.
Runner re-registration or replacement during an operator batch can orphan jobs
the same way.

Report-only by default; exit status 1 means orphans were found. `--redispatch`
cancels and reruns failed jobs of an affected run only when, re-read just
before cancelling, none of the run's jobs is in progress. GitHub has no
conditional cancel: a job starting in the instant between that re-read and the
cancel is cancelled too, and `rerun --failed` then runs it again (repeated,
not lost). Redispatch is therefore opt-in, never scheduled by default. Uses the
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


def pages(path, key, call):
    """Every item of a paginated list endpoint; a partial view could hide active jobs."""
    items, page = [], 1
    while True:
        batch = call(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")[key]
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def run_jobs(repository, run_id, call=api):
    return pages(f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest", "jobs", call)


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def idle_label_sets(repository, call=api):
    runners = pages(f"repos/{repository}/actions/runners", "runners", call)
    return [{label["name"] for label in r["labels"]}
            for r in runners if r["status"] == "online" and not r["busy"]]


def orphans(repository, now, threshold, call=api):
    idle = idle_label_sets(repository, call)
    found = []
    for status in ("queued", "in_progress"):
        for run in pages(f"repos/{repository}/actions/runs?status={status}", "workflow_runs", call):
            jobs = run_jobs(repository, run["id"], call)
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
    # Re-read every job immediately before cancelling: a queued job may have
    # started since the scan, and cancelling would kill that running work.
    if any(job["status"] == "in_progress" for job in run_jobs(repository, run_id, call)):
        return False
    run("run", "cancel", str(run_id), "-R", repository)
    deadline = time.monotonic() + timeout
    while call(f"repos/{repository}/actions/runs/{run_id}")["status"] != "completed":
        if time.monotonic() > deadline:
            raise TimeoutError(f"Run {run_id} did not finish cancelling; rerun it manually")
        sleep(5)
    # Reruns failed and cancelled jobs with their dependents; passed jobs stay.
    run("run", "rerun", str(run_id), "--failed", "-R", repository)
    return True


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
            done = redispatch(args.repository, run_id)
            print(json.dumps({"run": run_id, "action": "cancelled and reran failed jobs" if done
                              else "skipped: a job started since the scan"}))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
