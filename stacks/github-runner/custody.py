#!/usr/bin/env python3
"""Transfer admission to the native Worker, then exit; verify kernel custody at hooks."""
from array import array
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import struct
import time

from workload import ExpiredPolicy, admission_policy, capacity_reservation, lock, process_identity


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
                expiry_fallback=env.get('AGENT_CI_EXPIRED_POLICY_FALLBACK') == '1',
                meminfo=env.get("AGENT_CI_MEMINFO", "/proc/meminfo"),
                pool=env.get("AGENT_CI_POOL", "/run/ci-pool"))


def claim(owner):
    cfg = configuration()
    identity = process_identity(owner)
    if identity is None:
        raise RuntimeError("Worker exited before admission")
    endpoint = os.environ.get('AGENT_CI_CUSTODY_ENDPOINT', '')
    if not endpoint.startswith(f'ci-custody-{owner}-') or len(endpoint) > 79:
        raise RuntimeError("Worker custody endpoint missing")
    deadline = time.monotonic() + cfg['budget']

    def remaining():
        result = deadline - time.monotonic()
        if result <= 0 or process_identity(owner) != identity:
            raise TimeoutError("Worker admission deadline/identity failed")
        return result

    # An expiry while queued releases every shared/engine reference before
    # retrying with global EX. Never attempt an in-place SH -> EX lock upgrade.
    while True:
        try:
            admission_policy(cfg['policy'], cfg['profile'])
            exclusive = False
        except ExpiredPolicy:
            if not cfg['expiry_fallback']:
                raise
            admission_policy(cfg['policy'], cfg['profile'], allow_expired=True)
            exclusive = True
        with ExitStack() as stack:
            try:
                _transfer(owner, identity, endpoint, cfg, remaining, stack, exclusive)
                return
            except ExpiredPolicy:
                remaining()


def _transfer(owner, identity, endpoint, cfg, remaining, stack, exclusive):
    def acquire(path, shared=False):
        while True:
            try:
                return stack.enter_context(lock(path, min(.1, remaining()), shared=shared))
            except TimeoutError:
                remaining()

    fds = [acquire(cfg['global_lock'], shared=not exclusive)]
    if cfg['engine']:
        fds.append(acquire(cfg['engine']))
    while True:
        remaining()
        try:
            fds.append(stack.enter_context(capacity_reservation(
                cfg['directory'], cfg['policy'], cfg['profile'], cfg['meminfo'], cfg['pool'],
                exclusive=exclusive)))
            break
        except TimeoutError:
            time.sleep(min(.1, remaining()))
    remaining()
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as channel:
        channel.settimeout(min(2, remaining()))
        channel.connect('\0' + endpoint)
        peer, uid, _ = struct.unpack('3i', channel.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        if peer != owner or uid != os.getuid() or process_identity(owner) != identity:
            raise RuntimeError("Actual Worker custody peer required")
        if channel.sendmsg([b'CIC1'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array('i', fds))]) != 4:
            raise RuntimeError("Incomplete custody transfer")
        if channel.recv(16) != b'ACK1':
            raise RuntimeError("Worker did not confirm custody")
        # The ACK proves receiver retention; kernel inode/flock proof below
        # must also pass before the started hook permits any job steps.
        verify_worker(owner)
    # ExitStack closes only our references. The Worker retains the same open
    # file descriptions through process exit, even after the receiver returns.


def verify_worker(pid):
    cfg = configuration()
    identity = process_identity(pid)
    if identity is None or Path(os.readlink(f'/proc/{pid}/exe')).name != 'Runner.Worker':
        raise RuntimeError("Actual Runner.Worker required")
    # Active admission is pinned in its kernel-held record. Re-reading mutable
    # policy here would fail completion after renewal/expiry of a valid lease.
    held = {}
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
        held.setdefault((stat.st_dev, stat.st_ino), set()).update(modes)
        if 'WRITE' in modes:
            for path in Path(cfg['directory']).glob('lease-*'):
                try:
                    target = path.stat()
                except FileNotFoundError:
                    continue
                if (stat.st_dev, stat.st_ino) == (target.st_dev, target.st_ino):
                    # Read the pinned inode, rather than trusting an env marker or PID file.
                    with entry.open() as stream:
                        item = json.load(stream)
                    if (item.get('_profile') == cfg['profile']
                            and type(item.get('_exclusive')) is bool
                            and all(type(item.get(k)) is int and item[k] > 0
                                    for k in ('memoryMiB', 'cpuPercent'))):
                        reservations.append((stat.st_ino, item['_exclusive']))
    unique = set(reservations)
    if len(unique) != 1:
        raise RuntimeError('Worker pinned reservation missing')
    _, exclusive = next(iter(unique))
    expected = [(Path(cfg['global_lock']), 'WRITE' if exclusive else 'READ')]
    if cfg['engine']:
        expected.append((Path(cfg['engine']), 'WRITE'))
    valid = all(mode in held.get((path.stat().st_dev, path.stat().st_ino), set())
                for path, mode in expected)
    if not valid or process_identity(pid) != identity:
        raise RuntimeError("Worker kernel admission custody missing")
