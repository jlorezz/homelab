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

    @staticmethod
    def page(items, path):
        number = int(path.rsplit("page=", 1)[1])
        return items[(number - 1) * 100:number * 100]

    def __call__(self, path):
        if path.startswith(f"repos/{REPO}/actions/runners"):
            return {"runners": self.page(self.runners, path)}
        if "/actions/runs?status=" in path:
            status = path.split("status=")[1].split("&")[0]
            runs = [{"id": r} for r, s in self.runs.items() if s == status]
            return {"workflow_runs": self.page(runs, path)}
        if "/jobs?filter=latest&" in path:
            return {"jobs": self.page(self.jobs[int(path.split("/runs/")[1].split("/")[0])], path)}
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

    def test_active_job_on_a_later_page_blocks_cancellation(self):
        jobs = [job(1, "queued", ["dealpipe-nas-ci"])] + [job(i, "completed", ["x"]) for i in range(2, 150)]
        jobs.append(job(150, "in_progress", ["dealpipe-nas-browser"]))
        call = Api([runner("dealpipe-nas-ci", ["dealpipe-nas-ci"])], {3: "queued"}, {3: jobs})
        found = watchdog.orphans(REPO, NOW, timedelta(minutes=15), call)
        self.assertEqual([(f["job"], f["runHasActiveJobs"]) for f in found], [(1, True)])

    def test_redispatch_refuses_when_a_job_started_since_the_scan(self):
        call = Api([], {}, {9: [job(1, "in_progress", ["dealpipe-nas-ci"])]})
        commands = []
        self.assertFalse(watchdog.redispatch(REPO, 9, run=lambda *a: commands.append(a), call=call))
        self.assertEqual(commands, [])

    def test_redispatch_cancels_waits_then_reruns_failed_only(self):
        call = Api([], {}, {9: [job(1, "queued", ["dealpipe-nas-ci"])]})
        call.status = {9: "in_progress"}
        commands = []

        def sleep(_):
            call.status[9] = "completed"

        self.assertTrue(watchdog.redispatch(REPO, 9, run=lambda *a: commands.append(a), call=call, sleep=sleep))
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
