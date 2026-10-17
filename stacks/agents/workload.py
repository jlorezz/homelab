#!/usr/bin/env python3
"""Coordinate bounded NAS workloads; CI lanes may share admission."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def positive(value):
    result = float(value)
    if result <= 0 or not math.isfinite(result):
        raise argparse.ArgumentTypeError("Budget must be a finite positive number")
    return result


@contextmanager
def lock(path, seconds, shared=False):
    # Host provisioning owns the inode; never create/replace it inside a worker.
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        deadline = time.monotonic() + seconds
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Heavy workload queue budget exhausted")
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def process_identity(pid):
    try:
        # comm may contain spaces and parentheses; starttime is field 22.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Descendants may survive after the group's original leader exits.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_command(command, lock_path, queue_timeout, timeout, receipt=None):
    result = {"started_at": datetime.now(timezone.utc).isoformat(),
              "executable": Path(command[0]).name, "status": "error", "exit_code": 1,
              "spend_usd": None}
    # Do not persist argv, environment, command output, or repository data.
    started = time.monotonic()
    process = None
    old_handlers = {}

    def interrupted(signum, _frame):
        raise InterruptedError(signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, interrupted)
        with lock(lock_path, queue_timeout):
            result["queue_seconds"] = round(time.monotonic() - started, 3)
            process = subprocess.Popen(command, start_new_session=True)
            try:
                result["exit_code"] = process.wait(timeout=timeout)
                result["status"] = "passed" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                result.update(status="timed_out", exit_code=124)
            finally:
                # Commands must not leave previews/workers running after releasing the slot.
                terminate_group(process)
                process = None
    except TimeoutError:
        result.update(status="queue_timeout", exit_code=75)
    except InterruptedError as exc:
        result.update(status="interrupted", exit_code=128 + int(exc.args[0]))
    finally:
        if process is not None:
            terminate_group(process)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        if receipt:
            # Refuse an existing destination instead of overwriting a prior receipt or symlink.
            fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
    return result["exit_code"] if result["exit_code"] >= 0 else 128 - result["exit_code"]


def lease(args):
    """CI hook helper: retain the slot only while this exact Runner.Worker lives."""
    if process_identity(args.owner_pid) != args.owner_identity:
        return 1
    with lock(args.lock, args.queue_timeout, shared=args.shared):
        if process_identity(args.owner_pid) != args.owner_identity:
            return 1
        args.ready.write_text("ready\n")
        while process_identity(args.owner_pid) == args.owner_identity and not args.stop.exists():
            time.sleep(0.25)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default=os.environ.get("AGENT_WORKLOAD_LOCK", "/run/agent-workload.lock"))
    sub = parser.add_subparsers(dest="verb", required=True)
    run = sub.add_parser("run")
    run.add_argument("--queue-timeout", type=positive, default=1800, help="Pilot admission ceiling, not an application timeout")
    run.add_argument("--timeout", type=positive, required=True, help="Explicit command deadline in seconds")
    run.add_argument("--receipt", type=Path)
    run.add_argument("command", nargs=argparse.REMAINDER)
    hold = sub.add_parser("lease", help=argparse.SUPPRESS)
    hold.add_argument("--shared", action="store_true", help="Bounded CI lanes may overlap; agent commands retain exclusive admission")
    hold.add_argument("--owner-pid", type=int, required=True)
    hold.add_argument("--owner-identity", required=True)
    hold.add_argument("--queue-timeout", type=positive, required=True)
    hold.add_argument("--ready", type=Path, required=True)
    hold.add_argument("--stop", type=Path, required=True)
    args = parser.parse_args()
    if args.verb == "lease":
        return lease(args)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required after --")
    # Avoid starting a command if its receipt cannot be written safely.
    if args.receipt and (not args.receipt.parent.is_dir() or args.receipt.exists() or args.receipt.is_symlink()):
        parser.error("Receipt needs an existing directory and a new filename")
    return run_command(command, args.lock, args.queue_timeout, args.timeout, args.receipt)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TimeoutError) as exc:
        print(f"agent-workload: {exc}", file=sys.stderr)
        raise SystemExit(1)
