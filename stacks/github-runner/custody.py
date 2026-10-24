#!/usr/bin/env python3
"""Transfer admission to the native Worker, then exit; verify kernel custody at hooks."""
from array import array
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys
import time

from workload import admission_policy, capacity_reservation, lock, process_identity


def configuration():
    env = os.environ
    profile = env.get("AGENT_CI_PROFILE")
    engine = env.get("AGENT_CI_ENGINE_LOCK")
    if not profile or env.get("AGENT_CI_EXCLUSIVE") != "0":
        raise RuntimeError("Measured Worker requires shared admission and a profile")
    if env.get("AGENT_CI_SOCKET_REQUIRED") == "1" and not engine:
        raise RuntimeError("Socket lane requires engine admission")
    budget = float(env.get("AGENT_CI_QUEUE_TIMEOUT", "1800"))
    if not 0 < budget <= 1800:
        raise RuntimeError("Invalid admission queue budget")
    return dict(profile=profile, engine=engine, budget=budget,
                global_lock=env.get("AGENT_WORKLOAD_LOCK", "/run/agent-workload.lock"),
                directory=env.get("AGENT_CI_ADMISSION_DIRECTORY", "/run/ci-admission"),
                policy=env.get("AGENT_CI_ADMISSION_POLICY", "/run/ci-policy/policy.json"),
                meminfo=env.get("AGENT_CI_MEMINFO", "/proc/meminfo"),
                pool=env.get("AGENT_CI_POOL", "/run/ci-pool"))


def claim(channel):
    cfg = configuration()
    owner = os.getppid()
    identity = process_identity(owner)
    if identity is None:
        raise RuntimeError("Worker exited before admission")
    deadline = time.monotonic() + cfg['budget']

    def remaining():
        result = deadline - time.monotonic()
        if result <= 0 or process_identity(owner) != identity:
            raise TimeoutError("Worker admission deadline/identity failed")
        return result

    with ExitStack() as stack:
        fds = [stack.enter_context(lock(cfg['global_lock'], remaining(), shared=True))]
        if cfg['engine']:
            fds.append(stack.enter_context(lock(cfg['engine'], remaining())))
        while True:
            remaining()
            try:
                fds.append(stack.enter_context(capacity_reservation(
                    cfg['directory'], cfg['policy'], cfg['profile'], cfg['meminfo'], cfg['pool'])))
                break
            except TimeoutError:
                time.sleep(min(.1, remaining()))
        channel.settimeout(remaining())
        if channel.sendmsg([b'CIC1'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array('i', fds))]) != 4:
            raise RuntimeError("Incomplete custody transfer")
        if channel.recv(16) != b'ACK1':
            raise RuntimeError("Worker did not confirm custody")
        # ExitStack closes only our references. SCM_RIGHTS transferred the same
        # open file descriptions; flock ownership now lasts until Worker exit.


def verify_worker(pid):
    cfg = configuration()
    identity = process_identity(pid)
    if identity is None or Path(os.readlink(f'/proc/{pid}/exe')).name != 'Runner.Worker':
        raise RuntimeError("Actual Runner.Worker required")
    _, limits = admission_policy(cfg['policy'], cfg['profile'])
    expected = [(Path(cfg['global_lock']), 'READ')]
    if cfg['engine']:
        expected.append((Path(cfg['engine']), 'WRITE'))
    found = set()
    reservations = []
    for entry in Path(f'/proc/{pid}/fd').iterdir():
        try:
            info = Path(f'/proc/{pid}/fdinfo/{entry.name}').read_text()
            stat = entry.stat()
            flags = int(next(line.split()[1] for line in info.splitlines() if line.startswith('flags:')), 8)
        except (OSError, StopIteration, ValueError):
            continue
        if not flags & os.O_CLOEXEC:
            continue
        modes = [line.split()[4] for line in info.splitlines()
                 if line.startswith('lock:') and len(line.split()) >= 8
                 and line.split()[2:4] == ['FLOCK', 'ADVISORY']]
        for index, (path, mode) in enumerate(expected):
            target = path.stat()
            if (stat.st_dev, stat.st_ino) == (target.st_dev, target.st_ino) and mode in modes:
                found.add(index)
        if 'WRITE' in modes:
            for path in Path(cfg['directory']).glob('lease-*'):
                target = path.stat()
                if (stat.st_dev, stat.st_ino) == (target.st_dev, target.st_ino):
                    # Read the pinned inode, rather than trusting an env marker or PID file.
                    with entry.open() as stream:
                        if json.load(stream) == limits:
                            reservations.append(stat.st_ino)
    if len(found) != len(expected) or len(set(reservations)) != 1 or process_identity(pid) != identity:
        raise RuntimeError("Worker kernel admission custody missing")


if __name__ == '__main__':
    try:
        with socket.socket(fileno=int(sys.argv[1])) as channel:
            claim(channel)
    except Exception:
        # Protocol and policy errors must not dump environment/arguments into logs.
        print('CI admission claimer failed', file=sys.stderr)
        raise SystemExit(1)
