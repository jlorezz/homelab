"""Exercise disposable workers offline with fake registration and engine APIs."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "github-preview-runner/controller.py"
spec = importlib.util.spec_from_file_location("preview_controller", SOURCE)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ControllerTests(unittest.TestCase):
    def test_worker_boundary_and_role_budgets(self):
        for role, lock, parent, label in (
            ("preview-build", "preview", "preview", "dealpipe-nas-preview"),
            ("preview-publish", "preview", "preview", "dealpipe-preview-publish"),
            ("staging", "deploy", "staging", "dealpipe-deploy-staging"),
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                credential = Path(directory) / "fixture-credential"
                credential.write_text("controller-fixture")
                credential.chmod(0o600)
                argv = ["controller", "--environment", role, "--image", "sha256:" + "0" * 64,
                        "--credential", str(credential), "--memory-mib", "2048", "--cpu-percent", "100"]
                replies = [io.BytesIO(json.dumps({"token": "registration-fixture"}).encode()),
                           io.BytesIO(b'{"runners": []}')]
                with patch.object(sys, "argv", argv), patch.object(controller, "urlopen", side_effect=replies), \
                     patch.object(controller.signal, "signal"), \
                     patch.object(controller.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                    controller.main()
                command = run.call_args_list[0].args[0]
                mounts = [v for v in command if v.startswith("--mount=")]
                self.assertEqual(mounts, [f"--mount=type=bind,src=/var/lib/agent-workloads/{lock}.lock,dst=/run/agent-workload.lock"])
                self.assertIn(f"--cgroup-parent=dealpipe-{parent}.slice", command)
                self.assertIn(f"RUNNER_LABEL={label}", command)
                self.assertIn("--memory=2048m", command)
                self.assertIn("--memory-swap=2048m", command)
                self.assertIn("--cpus=1", command)
                self.assertNotIn(str(credential), command)
                self.assertNotIn("controller-fixture", " ".join(command))
                self.assertNotIn("--privileged", command)
                self.assertIn("--network=dealpipe-deploy", command)
                self.assertEqual(run.call_args_list[0].kwargs["input"], "registration-fixture\n")
