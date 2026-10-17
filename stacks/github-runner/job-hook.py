#!/usr/bin/env python3
"""Admit bounded CI lanes together while excluding other heavy agent workloads."""

import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, "/usr/local/lib/agent-platform")
from workload import process_identity


def worker_ancestor():
    pid = os.getppid()
    while pid > 1:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        command = Path(f"/proc/{pid}/comm").read_text().strip()
        if command == "Runner.Worker":
            return pid
        pid = int(stat[1])
    raise RuntimeError("Cannot find this job's Runner.Worker; refusing an uncoordinated job")


def main():
    pid = worker_ancestor()
    identity = process_identity(pid)
    if identity is None:
        raise RuntimeError("Runner.Worker has exited")
    directory = Path("/tmp/agent-ci-leases")
    directory.mkdir(mode=0o700, exist_ok=True)
    ready = directory / f"{pid}-{identity}.ready"
    stop = directory / f"{pid}-{identity}.stop"
    if sys.argv[1] == "completed":
        stop.touch(mode=0o600)
        return
    budget = 1800  # Same pilot queue admission budget as agent-workload.
    child = subprocess.Popen([
        sys.executable, "/usr/local/lib/agent-platform/workload.py", "lease",
        "--shared", "--owner-pid", str(pid), "--owner-identity", identity,
        "--queue-timeout", str(budget), "--ready", str(ready), "--stop", str(stop),
    ], start_new_session=True, stdin=subprocess.DEVNULL,
       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + budget + 5
    while time.monotonic() < deadline:
        if ready.is_file():
            print("Shared CI admission acquired")
            return
        if child.poll() is not None:
            raise RuntimeError("Workload lease failed; job must not start")
        time.sleep(0.1)
    stop.touch(mode=0o600)
    raise RuntimeError("Workload lease timed out; job must not start")


if __name__ == "__main__":
    main()
