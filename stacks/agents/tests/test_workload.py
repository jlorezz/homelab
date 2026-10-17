import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workload


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.lock = self.path / "heavy.lock"
        self.lock.touch()

    def tearDown(self):
        self.tmp.cleanup()

    def test_exit_status_and_receipt_without_argv_secrets(self):
        receipt = self.path / "receipt.json"
        rc = workload.run_command([sys.executable, "-c", "raise SystemExit(7)", "private-token"], self.lock, 1, 2, receipt)
        self.assertEqual(7, rc)
        result = json.loads(receipt.read_text())
        self.assertEqual("failed", result["status"])
        self.assertNotIn("private-token", receipt.read_text())
        self.assertEqual(0o600, receipt.stat().st_mode & 0o777)

    def test_timeout_releases_slot(self):
        rc = workload.run_command([sys.executable, "-c", "import time; time.sleep(30)"], self.lock, 1, 0.1)
        self.assertEqual(124, rc)
        with workload.lock(self.lock, 0.1):
            pass

    def test_competing_workload_times_out_in_queue(self):
        with workload.lock(self.lock, 1):
            rc = workload.run_command([sys.executable, "-c", "raise SystemExit(99)"], self.lock, 0.1, 1)
        self.assertEqual(75, rc)

    def test_parallel_ci_lanes_still_exclude_heavy_agent_commands(self):
        with workload.lock(self.lock, 1, shared=True):
            with workload.lock(self.lock, 1, shared=True):
                with self.assertRaises(TimeoutError):
                    with workload.lock(self.lock, 0.05):
                        self.fail("Agent overlapped parallel CI lanes")
            with self.assertRaises(TimeoutError):
                with workload.lock(self.lock, 0.05):
                    self.fail("Agent started before the last CI lane finished")
        with workload.lock(self.lock, 0.1):
            pass

    def test_missing_or_symlink_lock_refuses_to_run(self):
        for lock in (self.path / "missing", self.path / "linked"):
            if lock.name == "linked":
                lock.symlink_to(self.lock)
            with self.assertRaises(OSError):
                with workload.lock(lock, 1):
                    self.fail("unsafe lock was acquired")

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "Linux CI lease integration")
    def test_ci_lease_excludes_agents_and_releases_on_worker_exit(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        ready, stop = self.path / "ready", self.path / "stop"
        child = subprocess.Popen([sys.executable, str(Path(workload.__file__)), "--lock", str(self.lock), "lease",
            "--owner-pid", str(owner.pid), "--owner-identity", workload.process_identity(owner.pid),
            "--queue-timeout", "1", "--ready", str(ready), "--stop", str(stop)])
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            with self.assertRaises(TimeoutError):
                with workload.lock(self.lock, 0.1):
                    self.fail("Agent overlapped a CI job")
            owner.terminate()
            owner.wait(timeout=2)
            self.assertEqual(0, child.wait(timeout=2))
            with workload.lock(self.lock, 0.1):
                pass
        finally:
            for process in (owner, child):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=3)

    def test_background_descendant_is_stopped_before_slot_release(self):
        marker = self.path / "escaped"
        code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1]])"
        child_code = f"import time,pathlib; time.sleep(1); pathlib.Path({str(marker)!r}).touch()"
        self.assertEqual(0, workload.run_command([sys.executable, "-c", code, child_code], self.lock, 1, 2))
        time.sleep(1.1)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
