import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review


class ReviewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Snapshot test")
        (self.repo / "app.txt").write_text("old\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        for directory in self.root.rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o755)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def test_snapshot_excludes_dirty_untracked_and_git_credentials(self):
        (self.repo / "app.txt").write_text("committed\n")
        self.commit()
        (self.repo / "app.txt").write_text("dirty\n")
        (self.repo / ".env").write_text("secret=private\n")
        output = self.root / "snapshot"
        result = review.prepare(self.repo, self.base, "HEAD", output)
        self.assertTrue(result["dirty_work_excluded"])
        self.assertEqual("committed\n", (output / "source/app.txt").read_text())
        self.assertFalse((output / "source/.git").exists())
        self.assertFalse((output / "source/.env").exists())
        self.assertIn("+committed", (output / "changes.patch").read_text())

    def test_repository_runtime_config_is_not_loaded_by_reviewer(self):
        (self.repo / "opencode.json").write_text('{"plugin":["untrusted"]}')
        self.commit()
        output = self.root / "snapshot"
        result = review.prepare(self.repo, self.base, "HEAD", output)
        self.assertFalse((output / "source/opencode.json").exists())
        self.assertIn("opencode.json", result["excluded_runtime_configuration"])

    def test_tracked_environment_file_refuses_export(self):
        (self.repo / ".env").write_text("secret=private\n")
        self.commit()
        with self.assertRaises(ValueError):
            review.prepare(self.repo, self.base, "HEAD", self.root / "snapshot")
        self.assertFalse((self.root / "snapshot").exists())

    def test_deleted_base_environment_file_also_refuses_export(self):
        (self.repo / ".env").write_text("secret=private\n")
        self.commit()
        base = self.git("rev-parse", "HEAD").strip()
        (self.repo / ".env").unlink()
        self.commit()
        with self.assertRaises(ValueError):
            review.prepare(self.repo, base, "HEAD", self.root / "snapshot")

    def test_symlink_cannot_escape_snapshot(self):
        (self.repo / "escape").symlink_to("/etc/passwd")
        self.commit()
        with self.assertRaises(ValueError):
            review.prepare(self.repo, self.base, "HEAD", self.root / "snapshot")

    def run_args(self):
        snapshot = self.root / "snapshot"
        review.prepare(self.repo, self.base, "HEAD", snapshot)
        key = self.root / "key"
        key.write_text("test-inference-only-key")
        key.chmod(0o600)
        return SimpleNamespace(snapshot=snapshot, output=self.root / "result", key_file=key,
            token_file=str(self.root / "admin-token"), image="localhost/homelab-agents:" + "a" * 32,
            url="https://example.invalid", connect_address="192.0.2.1", endpoint=2, timeout=5)

    def test_review_container_has_only_read_only_source_and_policy_mounts(self):
        args = self.run_args()
        calls = []

        class API:
            def request(self, method, route, data=None, **kwargs):
                calls.append((method, route, data))
                if route.endswith("/create"):
                    return {"Id": "review-container"}
                if route.endswith("/json"):
                    return {"State": {"Running": False, "ExitCode": 0}}
                if "/logs?" in route:
                    text = b'{"findings":[]}\n'
                    return b"\x01\0\0\0" + len(text).to_bytes(4, "big") + text

        with patch.object(review, "Portainer", return_value=API()), patch.object(review.shutil, "which", return_value="gitleaks"), patch.object(review.subprocess, "run"):
            self.assertEqual(0, review.run(args))
        body = next(data for method, route, data in calls if route.endswith("/create"))
        self.assertTrue(body["HostConfig"]["ReadonlyRootfs"])
        self.assertEqual([str(args.snapshot) + ":/review:ro", "/var/lib/agent-platform/policy:/opt/agent-policy:ro"], body["HostConfig"]["Binds"])
        self.assertIn("--pure", body["Cmd"])
        self.assertTrue(any(method == "DELETE" for method, _, _ in calls))
        self.assertEqual('{"findings":[]}\n', (args.output / "review-output.log").read_text())
        receipt = (args.output / "receipt.json").read_text()
        self.assertNotIn("test-inference-only-key", receipt)
        self.assertEqual("removed", json.loads(receipt)["container_cleanup"])

    def test_start_failure_still_removes_container_and_writes_receipt(self):
        args = self.run_args()
        deleted = []

        class API:
            def request(self, method, route, data=None, **kwargs):
                if route.endswith("/create"):
                    return {"Id": "review-container"}
                if route.endswith("/start"):
                    raise RuntimeError("start failed")
                if method == "DELETE":
                    deleted.append(route)

        with patch.object(review, "Portainer", return_value=API()), patch.object(review.shutil, "which", return_value="gitleaks"), patch.object(review.subprocess, "run"):
            with self.assertRaises(RuntimeError):
                review.run(args)
        self.assertTrue(deleted)
        result = json.loads((args.output / "receipt.json").read_text())
        self.assertEqual("error", result["status"])
        self.assertEqual("removed", result["container_cleanup"])


if __name__ == "__main__":
    unittest.main()
