#!/usr/bin/env python3
"""Coordinate bounded NAS workloads; CI lanes may share admission."""

import argparse
from contextlib import contextmanager, ExitStack
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
import tempfile


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


def admission_policy(path, profile):
    """No measured defaults: a short-lived operator-supplied policy is mandatory."""
    policy = json.loads(Path(path).read_text())
    now = time.time()
    if (policy.get("enabled") is not True or not policy.get("evidence")
            or not now - 86400 <= policy["verifiedAt"] <= now
            or not now < policy["expiresAt"] <= policy["verifiedAt"] + 86400):
        raise ValueError("CI working-set evidence missing, disabled or stale")
    limits = policy["profiles"][profile]
    for value in (limits["memoryMiB"], limits["cpuPercent"], policy["maxConcurrentMiB"]):
        if type(value) is not int or value <= 0:
            raise ValueError("Working-set budgets must be positive integers")
    if policy["reserveMiB"] < 11865 or policy["cpuPercent"] > 800:
        raise ValueError("NAS reserve/CPU budget violated")
    if policy["maxConcurrentMiB"] > min(32768, policy["measuredHeadroomMiB"] - policy["reserveMiB"]):
        raise ValueError("Concurrent working sets exceed verified headroom")
    return policy, limits


@contextmanager
def capacity_reservation(directory, policy_path, profile, meminfo, pool):
    """Serialize reservations; kernel-held file locks survive namespaces, not exits.

    Current usage plus FULL live reservations deliberately double counts active
    jobs. Conservative refusal is preferable to claiming reclaimed/cache RAM.
    The directory and mutex inode are provisioned by the operator, never replaced.
    """
    directory, pool = Path(directory), Path(pool)
    with lock(directory / "mutex.lock", 1):
        policy, limits = admission_policy(policy_path, profile)
        memory = dict(line.replace(":", "").split()[:2] for line in Path(meminfo).read_text().splitlines())
        total = math.ceil(int(memory["MemTotal"]) / 1024)
        available = int(memory["MemAvailable"]) // 1024
        if policy["reserveMiB"] < max(11865, math.ceil(total / 4)):
            raise ValueError("Physical memory reserve violated")
        if int((pool / "memory.max").read_text()) != 32768 * 1024**2 or int((pool / "memory.swap.max").read_text()) != 0:
            raise ValueError("CI parent must remain 32 GiB with swap disabled")
        quota, period = map(int, (pool / "cpu.max").read_text().split())
        if quota != 8 * period:
            raise ValueError("CI parent must remain 8 CPUs")
        used_memory, used_cpu = 0, 0
        for path in directory.glob("lease-*"):
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    with os.fdopen(os.dup(fd)) as stream:
                        item = json.load(stream)
                    used_memory += item["memoryMiB"]
                    used_cpu += item["cpuPercent"]
                else:
                    path.unlink()  # Dead holder; no PID namespace assumptions.
            finally:
                os.close(fd)
        requested = used_memory + limits["memoryMiB"]
        current = math.ceil(int((pool / "memory.current").read_text()) / 1024**2)
        if (requested > policy["maxConcurrentMiB"]
                or available - requested < policy["reserveMiB"]
                or current + requested > 32768
                or used_cpu + limits["cpuPercent"] > policy["cpuPercent"]):
            raise TimeoutError("Measured CI working-set admission unavailable")
        fd, name = tempfile.mkstemp(prefix="lease-", dir=directory)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(fd, json.dumps(limits).encode())
        except BaseException:
            os.close(fd)
            Path(name).unlink()
            raise
    try:
        yield
    finally:
        # Keep the inode until a later holder prunes it under the mutex.
        os.close(fd)


def lease(args):
    """Retain ALL admission resources while this exact Runner.Worker lives."""
    if process_identity(args.owner_pid) != args.owner_identity:
        return 1
    with ExitStack() as stack:
        stack.enter_context(lock(args.lock, args.queue_timeout, shared=args.shared))
        if args.engine_lock:
            if not args.shared:
                raise ValueError("Engine admission requires shared global admission")
            stack.enter_context(lock(args.engine_lock, args.queue_timeout))
        if args.admission_profile:
            deadline = time.monotonic() + args.queue_timeout
            while True:
                try:
                    stack.enter_context(capacity_reservation(args.admission_directory, args.admission_policy,
                                                            args.admission_profile, args.meminfo, args.ci_pool))
                    break
                except TimeoutError:
                    if time.monotonic() >= deadline or process_identity(args.owner_pid) != args.owner_identity:
                        raise
                    time.sleep(0.25)
        if process_identity(args.owner_pid) != args.owner_identity:
            return 1
        args.ready.write_text("ready\n")
        # Completed hooks run before Worker exit. Retain all resources through
        # final runner cleanup; a completion marker is not proof of process exit.
        while process_identity(args.owner_pid) == args.owner_identity:
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
    hold.add_argument("--engine-lock", type=Path)
    hold.add_argument("--admission-profile")
    hold.add_argument("--admission-directory", type=Path, default=Path("/run/ci-admission"))
    hold.add_argument("--admission-policy", type=Path, default=Path("/run/ci-policy/policy.json"))
    hold.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    hold.add_argument("--ci-pool", type=Path, default=Path("/run/ci-pool"))
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
