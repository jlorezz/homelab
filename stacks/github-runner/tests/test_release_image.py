"""Check the actual disposable-image contexts without a Docker daemon."""
import importlib.util
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


STACKS = Path(__file__).resolve().parents[2]


class ReleaseImageTests(unittest.TestCase):
    def test_packaged_job_hook_imports_without_repository_modules(self):
        for role in ("github-deploy-runner", "github-preview-runner"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                image = Path(directory)

                def build(command, **kwargs):
                    context = Path(command[-1])
                    # Materialize only files the Dockerfile copies. This catches
                    # both missing build-context inputs and missing COPY lines.
                    for line in (context / "Dockerfile").read_text().splitlines():
                        if line.startswith("COPY "):
                            _, source, destination = shlex.split(line)
                            if destination.startswith("/usr/local/lib/agent-platform/"):
                                shutil.copyfile(context / source, image / Path(destination).name)

                script = STACKS / role / "build-image.py"
                spec = importlib.util.spec_from_file_location("release_image_builder", script)
                builder = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(builder)
                with patch.object(builder.subprocess, "run", side_effect=build) as docker:
                    builder.main()
                docker.assert_called_once()
                # A fresh isolated interpreter cannot borrow custody.py from the
                # developer checkout, PYTHONPATH, or already imported test modules.
                result = subprocess.run(
                    [sys.executable, "-I", "-c",
                     "import runpy,sys; sys.path.insert(0, sys.argv[1]); "
                     "runpy.run_path(sys.argv[1] + '/job-hook.py', run_name='image_import_check'); "
                     "from pathlib import Path; "
                     "assert all(Path(sys.modules[name].__file__).parent == Path(sys.argv[1]) "
                     "for name in ('custody', 'workload'))",
                     str(image)],
                    cwd=image, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
