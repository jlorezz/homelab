#!/usr/bin/env python3
"""Build the disposable runner in the host engine; print its immutable image ID."""

from pathlib import Path
import subprocess
import shutil
import tempfile


def main():
    directory = Path(__file__).resolve().parent
    sources = {
        "Dockerfile": directory.parent / "github-deploy-runner/Dockerfile",
        "entrypoint.sh": directory / "entrypoint.sh",
        "job-hook.py": directory.parent / "github-runner/job-hook.py",
        "workload.py": directory.parent / "agents/workload.py",
    }
    with tempfile.TemporaryDirectory(prefix="dealpipe-preview-image-") as context:
        for name, path in sources.items():
            shutil.copyfile(path, Path(context) / name)
        subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", "build", "--quiet", context],
                       check=True)


if __name__ == "__main__":
    main()
