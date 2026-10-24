#!/usr/bin/env python3
"""Admit bounded CI lanes together while excluding other heavy agent workloads."""

import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, "/usr/local/lib/agent-platform")
from workload import process_identity
from custody import claim, verify_worker
try:
    from network_reaper import reap_best_effort
except ImportError:  # Socket-free deploy/preview images do not ship engine hygiene.
    def reap_best_effort(stage):
        return


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
    if "AGENT_CI_PROFILE" in os.environ:
        # Queue only after managed Worker startup has consumed the JobRequest.
        # Completion proves continuing custody; it must never reacquire a lease.
        if sys.argv[1] == "started":
            claim(pid)
        elif sys.argv[1] == "completed":
            verify_worker(pid)
        else:
            raise RuntimeError("Unknown job hook stage")
        print("CI kernel admission verified")
        reap_best_effort(sys.argv[1])
        return
    directory = Path("/tmp/agent-ci-leases")
    directory.mkdir(mode=0o700, exist_ok=True)
    ready = directory / f"{pid}-{identity}.ready"
    stop = directory / f"{pid}-{identity}.stop"
    if sys.argv[1] == "completed":
        # Still inside this job's admission: nothing else uses the lane engine.
        try:
            reap_best_effort("completed")
        finally:
            stop.touch(mode=0o600)
        return
    budget = 1800  # Same pilot queue admission budget as agent-workload.
    # Legacy/unconfigured images remain globally exclusive. Shared admission
    # requires an explicit lane configuration, never an implicit new default.
    admission = ["--shared"] if os.environ.get("AGENT_CI_EXCLUSIVE") == "0" else []
    child = subprocess.Popen([
        sys.executable, "/usr/local/lib/agent-platform/workload.py", "lease",
        *admission, "--owner-pid", str(pid), "--owner-identity", identity,
        "--queue-timeout", str(budget), "--ready", str(ready), "--stop", str(stop),
    ], start_new_session=True, stdin=subprocess.DEVNULL,
       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + budget + 5
    while time.monotonic() < deadline:
        if ready.is_file():
            print("CI admission acquired")
            # Leftovers of a lost or cancelled previous job on this lane.
            reap_best_effort("started")
            return
        if child.poll() is not None:
            raise RuntimeError("Workload lease failed; job must not start")
        time.sleep(0.1)
    stop.touch(mode=0o600)
    raise RuntimeError("Workload lease timed out; job must not start")


if __name__ == "__main__":
    main()
