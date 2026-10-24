#!/usr/bin/env python3
"""Bounded, operator-invoked first-phase static replica; dry-run by default."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

GIB = 1024 ** 3
IMAGE = 'sha256:121de326c9541820ea00f82a924286b5d6f9134d48c66cef1f50064e722ee079'
SOURCE, NAME = 'dealpipe-ci-static-1', 'dealpipe-ci-static-2-1'
CONFIG = NAME + '-configure'
LABEL = 'lab.qnoxs.static-replica'
ATTEMPT = LABEL + '.attempt'
ROOT = Path('/var/lib/dealpipe-ci/static-2')
LOCK = Path('/var/lib/agent-workloads/heavy.lock')
POOL = Path('/sys/fs/cgroup/user.slice/user-1102.slice')
ENV = {'HOME': '/home/ci', 'RUNNER_ALLOW_RUNASROOT': '1',
       'RUNNER_NAME': 'dealpipe-nas-static-2', 'RUNNER_LABEL': 'dealpipe-nas-static',
       'RUNNER_WORK_DIRECTORY': str(ROOT / 'work'), 'AGENT_CI_EXCLUSIVE': '0'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def docker(*args, data=None):
    # Explicit endpoint and clean CLI environment: ignore contexts/DOCKER_HOST.
    result = subprocess.run(['docker', '--host=unix:///run/user/1102/docker.sock', *args],
                            input=data, text=True, capture_output=True, timeout=180,
                            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                 'HOME': '/nonexistent', 'DOCKER_CONFIG': '/nonexistent'})
    require(result.returncode == 0, 'Engine operation failed (output suppressed)')
    return result.stdout


def inspect(name):
    # Distinguish missing names from daemon/inspection failures; never mask errors.
    names = docker('container', 'ls', '-a', '--format', '{{.Names}}').splitlines()
    return json.loads(docker('container', 'inspect', name))[0] if name in names else None


def idle(name):
    require('Runner.Worker' not in docker('top', name, '-eo', 'comm,args'),
            'Runner.Worker active; refusing operation')


def safe_path(path):
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'Symlink in state/lock path')


def mounts():
    return {str(ROOT / 'runner'): '/runner', str(ROOT / 'home'): '/home/ci',
            str(ROOT / 'work'): str(ROOT / 'work'), str(LOCK): '/run/agent-workload.lock'}


def events():
    values = dict(line.split() for line in (POOL / 'memory.events').read_text().splitlines())
    return tuple(int(values[key]) for key in ('oom', 'oom_kill'))


def capacity():
    require(int((POOL / 'memory.max').read_text()) == 32 * GIB, 'CI memory cap must be 32 GiB')
    require(int((POOL / 'memory.swap.max').read_text()) == 0, 'CI aggregate swap must remain disabled')
    quota, period = map(int, (POOL / 'cpu.max').read_text().split())
    require(quota == 8 * period, 'CI CPU cap must be 8 CPUs')
    memory = dict(line.replace(':', '').split()[:2] for line in Path('/proc/meminfo').read_text().splitlines())
    total, available = (int(memory[key]) * 1024 for key in ('MemTotal', 'MemAvailable'))
    reserve = max(8 * GIB, (total + 3) // 4)
    require(total >= 32 * GIB + reserve, 'Physical memory cannot sustain existing cap plus reserve')
    require(available >= 8 * GIB + reserve, 'Insufficient incremental MemAvailable plus reserve')
    usage = dict((key, int(value)) for key, value in
                 (line.split() for line in (POOL / 'memory.stat').read_text().splitlines()))
    kernel = usage.get('kernel', sum(usage.get(k, 0) for k in ('kernel_stack', 'pagetables', 'sock', 'slab')))
    committed = max(usage['anon'] + kernel,
                    int((POOL / 'memory.current').read_text()) - usage['file'])
    require(committed + 8 * GIB <= 32 * GIB, 'Insufficient aggregate non-file memory headroom')
    baseline = events()
    require(baseline == (0, 0), 'Existing CI OOM events require investigation')
    return baseline


def preflight(image, rollback=False):
    require(os.geteuid() == 1102, 'Run as rootless CI UID 1102')
    info = json.loads(docker('info', '--format', '{{json .}}'))
    require('name=rootless' in info['SecurityOptions'], 'Engine must be rootless')
    if rollback:
        return None, None
    source = inspect(SOURCE)
    require(source is not None and source['State']['Running'], 'Source static runner must be running')
    cfg = source['Config']
    require(cfg['Labels'].get('com.docker.compose.service') == 'static'
            and 'RUNNER_LABEL=dealpipe-nas-static' in cfg['Env'], 'Source must be static lane')
    require(source['Image'] == image and cfg['User'] == '0:0'
            and cfg['Entrypoint'] == ['/usr/local/bin/ci-runner']
            and source['HostConfig']['NetworkMode'] == 'host', 'Source image/user/entrypoint/network drift')
    for mount in source['Mounts']:
        path = Path(mount['Source'])
        require('.sock' not in mount['Source'] + mount['Destination']
                and not stat.S_ISSOCK(path.stat().st_mode), 'Socket-bearing source refused')
    metadata = json.loads(docker('image', 'inspect', image))[0]
    require(not metadata['Config'].get('Volumes'), 'Image may not create implicit volumes')
    safe_path(LOCK)
    require(stat.S_ISREG(LOCK.stat().st_mode), 'Existing heavy.lock must be a regular file')
    return capacity(), (LOCK.stat().st_dev, LOCK.stat().st_ino)


def state(image, create=False):
    safe_path(ROOT)
    marker = ROOT / '.static-replica.json'
    expected = {'managed': NAME, 'image': image}
    if ROOT.exists():
        require(ROOT.stat().st_uid == 1102 and stat.S_IMODE(ROOT.stat().st_mode) == 0o700,
                'State root must be private and owned by UID 1102')
        safe_path(marker)
        require(marker.is_file() and json.loads(marker.read_text()) == expected
                and marker.stat().st_uid == 1102 and stat.S_IMODE(marker.stat().st_mode) == 0o600,
                'Unknown previous state; refusing adoption')
    elif create:
        ROOT.mkdir(mode=0o700)  # Existing parent only; never provision shared paths.
        with marker.open('x') as stream:
            os.chmod(marker, 0o600)
            json.dump(expected, stream)
    else:
        return
    for name in ('runner', 'home', 'work'):
        path = ROOT / name
        safe_path(path)
        if create and not path.exists():
            path.mkdir(mode=0o700)
        require(path.is_dir() and path.stat().st_uid == 1102
                and stat.S_IMODE(path.stat().st_mode) == 0o700, 'State directory ownership/mode drift')


def owned(container, image, name=NAME):
    cfg, host = container['Config'], container['HostConfig']
    require(container['Name'] == '/' + name and cfg['Labels'].get(LABEL) == NAME,
            'Unknown container; refusing adoption/removal')
    actual = container['Mounts']
    require(len(actual) == 4 and {m['Source']: m['Destination'] for m in actual} == mounts()
            and all(m['Type'] == 'bind' and m['RW'] for m in actual), 'Unknown mounts')
    require(container['Image'] == image and cfg['User'] == '0:0'
            and host['NetworkMode'] == 'host' and not host['Privileged']
            and not host.get('VolumesFrom') and not host.get('Devices')
            and host['Memory'] == host['MemorySwap'] == 8 * GIB
            and host['NanoCpus'] == 2_000_000_000 and host['PidsLimit'] == 2048
            and host['Init'] is True, 'Managed container limits/isolation drift')
    if name == NAME:
        require(all(f'{k}={v}' in cfg['Env'] for k, v in ENV.items())
                and cfg['Entrypoint'] == ['/usr/local/bin/ci-runner']
                and host['RestartPolicy']['Name'] == 'unless-stopped'
                and cfg['Labels'].get('com.docker.compose.project') == 'dealpipe-ci'
                and cfg['Labels'].get('com.docker.compose.service') == 'static-2', 'Managed configuration drift')


def command(name):
    result = ['--name', name, '--label', f'{LABEL}={NAME}', '--pull=never', '--user=0:0',
              '--network=host', '--memory=8g', '--memory-swap=8g', '--cpus=2', '--pids-limit=2048', '--init']
    for source, destination in mounts().items():
        result += ['--mount', f'type=bind,src={source},dst={destination}']
    for key, value in ENV.items():
        result += ['--env', f'{key}={value}']
    return result


def execute(args):
    baseline, inode = preflight(args.expected_image, args.rollback)
    state(args.expected_image)
    existing = inspect(NAME)
    if existing:
        owned(existing, args.expected_image)
    require(inspect(CONFIG) is None, 'Configuration container already exists; inspect privately')
    if args.apply:
        state(args.expected_image, create=True)
    if args.apply or args.rollback:
        require(ROOT.exists(), 'No owned state for mutation')
        with (ROOT / '.static-replica.json').open() as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return operate(args, baseline, inode)
    return operate(args, baseline, inode)


def operate(args, baseline, inode):
    existing = inspect(NAME)
    if existing:
        owned(existing, args.expected_image)
    require(inspect(CONFIG) is None, 'Configuration container already exists; inspect privately')
    if args.rollback:
        require(existing is not None and ROOT.exists(), 'No owned replica to roll back')
        require(not existing['State']['Running'],
                'Running replica retained: admission cannot be quiesced safely by this helper')
        docker('rm', NAME)
        print('Owned stopped replica removed; private state retained. GitHub cleanup is operator-owned.')
        return
    if existing:
        require(ROOT.exists() and existing['State']['Running'], 'Owned replica is stopped; inspect privately')
        print('Exact managed replica already running; no changes.')
        return
    require(not (ROOT / 'runner' / '.runner').exists(), 'Prior registration retained; operator cleanup required')
    if not args.apply:
        print('Dry-run passed: add only static-2 at 8 GiB/2 CPUs inside existing 32 GiB/8 CPU cap.')
        return
    token = sys.stdin.readline(8192).strip()
    require(bool(re.fullmatch(r'[A-Za-z0-9_\-\.]+', token)), 'Invalid registration token on stdin')
    state(args.expected_image, create=True)
    created, attempt = False, uuid.uuid4().hex
    try:
        idle(SOURCE)  # Immediately before configuration; other lanes may remain active.
        script = ('set -euo pipefail; IFS= read -r ACTIONS_RUNNER_INPUT_TOKEN; '
                  'export ACTIONS_RUNNER_INPUT_TOKEN; cp -a /opt/actions-runner/. /runner/; chown 0:0 /runner; chmod 0700 /runner; cd /runner; '
                  './config.sh --unattended --url https://github.com/dealpipe/dealpipe '
                  '--name "$RUNNER_NAME" --labels "$RUNNER_LABEL" --work "$RUNNER_WORK_DIRECTORY"')
        docker('run', '--rm', '-i', '--log-driver=none', *command(CONFIG), '--label', f'{ATTEMPT}={attempt}', '--entrypoint=bash',
               args.expected_image, '-c', script, data=token + '\n')
        token = None
        require((ROOT / 'runner' / '.runner').is_file() and (ROOT / 'runner' / '.runner').stat().st_size > 0,
                'Configuration did not persist registration')
        state(args.expected_image)
        require(capacity() == baseline, 'CI OOM counters changed during configuration')
        require((LOCK.stat().st_dev, LOCK.stat().st_ino) == inode, 'Shared lock inode changed')
        created = True  # Also reconcile an ambiguous CLI create failure by exact ownership.
        docker('create', *command(NAME), '--label', f'{ATTEMPT}={attempt}', '--restart=unless-stopped',
               '--label=com.docker.compose.project=dealpipe-ci',
               '--label=com.docker.compose.service=static-2', args.expected_image)
        owned(inspect(NAME), args.expected_image)
        docker('start', NAME)
        require(inspect(NAME)['State']['Running'], 'Replica failed to start')
        require(capacity() == baseline, 'CI OOM counters changed during startup')
    except (Exception, KeyboardInterrupt):
        # Only exact containers created by this attempt; no prune or volume deletion.
        for name in ([NAME] if created else []) + [CONFIG]:
            container = inspect(name)
            if container:
                if container['Config']['Labels'].get(ATTEMPT) != attempt:
                    print('Cleanup refused: container belongs to another attempt.', file=sys.stderr)
                    continue
                owned(container, args.expected_image, name)
                if name == NAME and container['State']['Running']:
                    # An idle inspection is not a drain: work may arrive before stop.
                    print('Running replica retained; rollback gated until stopped safely.', file=sys.stderr)
                    continue
                docker('rm', *(['-f'] if name == CONFIG else []), name)
        raise
    print('Static-2 started. Verify listener/GitHub health and unit hashes; state retained on failure.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    parser.add_argument('--expected-image', default=IMAGE, help='Reviewed immutable source image ID')
    args = parser.parse_args()
    try:
        require(bool(re.fullmatch(r'sha256:[0-9a-f]{64}', args.expected_image)), 'Expected immutable image ID')
        execute(args)
    except (Exception, KeyboardInterrupt):
        # Exceptions and engine/config output may contain the stdin token: never echo them.
        print('Static replica operation refused/failed; inspect prerequisites privately. State retained.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
