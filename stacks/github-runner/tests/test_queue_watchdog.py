"""Offline orphaned-queue detection replaying the 2026-10-24 coverage-merge stall."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("queue_watchdog", ROOT / "queue-watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

NOW = datetime(2026, 10, 24, 15, 15, tzinfo=timezone.utc)
REPO = "dealpipe/dealpipe"


def runner(name, labels, busy=False, status="online"):
    return {"name": name, "status": status, "busy": busy,
            "labels": [{"name": x} for x in ["self-hosted", "Linux", "X64", *labels]]}


def job(job_id, status, labels, created="2026-10-24T14:35:58Z"):
    return {"id": job_id, "name": f"job {job_id}", "status": status, "labels": labels,
            "created_at": created, "html_url": f"https://example/{job_id}"}


class Api:
    def __init__(self, runners, runs, jobs):
        self.runners, self.runs, self.jobs, self.status = runners, runs, jobs, {}

    def __call__(self, path):
        if path.startswith(f"repos/{REPO}/actions/runners"):
            return {"runners": self.runners}
        if "/actions/runs?status=" in path:
            status = path.split("status=")[1].split("&")[0]
            return {"workflow_runs": [{"id": r} for r, s in self.runs.items() if s == status]}
        if path.endswith("/jobs?filter=latest&per_page=100"):
            return {"jobs": self.jobs[int(path.split("/runs/")[1].split("/")[0])]}
        run_id = int(path.rsplit("/", 1)[1])
        return {"status": self.status.get(run_id, "completed")}


class QueueWatchdogTests(unittest.TestCase):
    def test_reports_incident_job_queued_with_idle_matching_runner(self):
        call = Api([runner("dealpipe-nas-ci", ["dealpipe-nas-ci"])],
                   {40101585603: "queued"},
                   {40101585603: [job(1, "completed", ["dealpipe-nas-ci"]),
                                  job(2, "queued", ["dealpipe-nas-ci"])]})
        found = watchdog.orphans(REPO, NOW, timedelta(minutes=15), call)
        self.assertEqual([(f["run"], f["job"], f["runHasActiveJobs"]) for f in found],
                         [(40101585603, 2, False)])

    def test_ordinary_queueing_is_not_reported(self):
        jobs = {7: [job(1, "queued", ["dealpipe-nas-ci"]),
                    job(2, "queued", ["dealpipe-nas-browser"], created="2026-10-24T15:10:00Z"),
                    job(3, "queued", ["dealpipe-nas-static"]),
                    job(4, "queued", ["ubuntu-latest"]),
                    job(5, "queued", [])]}
        call = Api([runner("dealpipe-nas-ci", ["dealpipe-nas-ci"], busy=True),
                    runner("dealpipe-nas-browser", ["dealpipe-nas-browser"]),
                    runner("dealpipe-nas-static", ["dealpipe-nas-static"], status="offline"),
                    runner("dealpipe-nas-static-2", [])],
                   {7: "in_progress"}, jobs)
        self.assertEqual(watchdog.orphans(REPO, NOW, timedelta(minutes=15), call), [])

    def test_redispatch_cancels_waits_then_reruns_failed_only(self):
        call = Api([], {}, {})
        call.status = {9: "in_progress"}
        commands = []

        def sleep(_):
            call.status[9] = "completed"

        watchdog.redispatch(REPO, 9, run=lambda *a: commands.append(a), call=call, sleep=sleep)
        self.assertEqual(commands, [("run", "cancel", "9", "-R", REPO),
                                    ("run", "rerun", "9", "--failed", "-R", REPO)])

    def test_main_never_cancels_a_run_with_active_jobs(self):
        found = [{"run": 5, "job": 1, "runHasActiveJobs": True}]
        original = watchdog.orphans, watchdog.redispatch
        calls = []
        try:
            watchdog.orphans = lambda *a, **k: found
            watchdog.redispatch = lambda *a, **k: calls.append(a)
            self.assertEqual(watchdog.main(["--redispatch"]), 1)
        finally:
            watchdog.orphans, watchdog.redispatch = original
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
