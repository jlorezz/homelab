#!/usr/bin/env python3
"""Run exactly one ephemeral release worker outside the PR Docker engine."""

import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("staging", "preview-build", "preview-publish"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--credential", type=Path, required=True)
    parser.add_argument("--memory-mib", type=int, required=True)
    parser.add_argument("--cpu-percent", type=int, required=True)
    args = parser.parse_args()
    if args.memory_mib <= 0 or args.cpu_percent <= 0:
        parser.error("Resource limits must be positive")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image):
        raise RuntimeError("Use the immutable local image ID from build-image.py")
    if args.credential.stat().st_mode & 0o077:
        raise RuntimeError("GitHub controller credential must not be group/world readable")
    token = args.credential.read_text().strip()
    if not token:
        raise RuntimeError("Missing GitHub runner-registration credential")
    preview = args.environment.startswith("preview-")
    resource = "preview" if preview else "deploy"
    label = {"preview-build": "dealpipe-nas-preview", "preview-publish": "dealpipe-preview-publish"}.get(args.environment, f"dealpipe-deploy-{args.environment}")
    name = f"dealpipe-deploy-{args.environment}-{uuid4().hex[:12]}"
    # This controller always uses the host engine. Never honor inherited Docker context.
    docker = ["docker", "--host", "unix:///var/run/docker.sock"]

    def github(method, path):
        request = Request(
            "https://api.github.com/repos/dealpipe/dealpipe/actions/runners" + path,
            method=method,
            data=b"{}" if method == "POST" else None,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else None
        except HTTPError as error:
            # Do not log headers, token, API bodies or registration material.
            raise RuntimeError(f"GitHub runner administration: HTTP {error.code}") from None

    def stop(_signum, _frame):
        subprocess.run([*docker, "stop", name], check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        raise InterruptedError("Release runner stopped")

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    registration = github("POST", "/registration-token")["token"]
    try:
        # No socket, host network, credentials mount, shared home or work volume.
        # The only host inode exposed is the non-secret workload admission lock.
        result = subprocess.run([
            *docker, "run", "--rm", "--interactive", "--init", "--name", name,
            "--pull=never", "--network=dealpipe-deploy", "--dns=1.1.1.1",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=1001:123",
            f"--cpus={args.cpu_percent / 100:g}", f"--memory={args.memory_mib}m",
            f"--memory-swap={args.memory_mib}m", "--pids-limit=4096",
            f"--cgroup-parent=dealpipe-{'preview' if preview else 'staging'}.slice",
            f"--mount=type=bind,src=/var/lib/agent-workloads/{resource}.lock,dst=/run/agent-workload.lock",
            "--env", f"RUNNER_NAME={name}", "--env", f"RUNNER_LABEL={label}", "--env", f"DEPLOY_ENVIRONMENT={args.environment}",
            args.image,
        ], input=registration + "\n", text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"Ephemeral runner exited {result.returncode}; inspect its journal")
    finally:
        subprocess.run([*docker, "rm", "--force", name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Ephemeral runners remove themselves after jobs; remove this registration
        # after interrupted config/listener startup too, without touching other lanes.
        page = 1
        while True:
            runners = github("GET", f"?per_page=100&page={page}")["runners"]
            for runner in runners:
                if runner["name"] == name:
                    github("DELETE", f"/{runner['id']}")
            if len(runners) < 100:
                break
            page += 1


if __name__ == "__main__":
    main()
