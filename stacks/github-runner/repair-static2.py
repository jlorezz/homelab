#!/usr/bin/env python3
"""One-attempt, registration-preserving static-2 repair. Default: read-only preflight."""
import argparse
from contextlib import ExitStack
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('static_replica_repair_base', HERE / 'static-replica.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
sys.path.insert(0, str(HERE.parent / 'agents'))
from workload import admission_policy, capacity_reservation, committed_memory_mib, lock

require, docker, inspect = base.require, base.docker, base.inspect
ROOT, POOL, HEAVY = base.ROOT, base.POOL, base.LOCK
POLICY = Path('/var/lib/agent-workloads/ci-policy/policy.json')
LEDGER = Path('/var/lib/agent-workloads/ci-admission')
NAME = base.NAME
BACKUP = NAME + '-before-12g-repair'
CLAIM = '.static2-12g-repair.json'
IMAGE = 'sha256:c766827a8bf4173c036e831688e5a7da4db19cd0b382bb7e026b68be7bf3baaa'
NATIVE = {'LD_PRELOAD': '/usr/local/lib/agent-platform/worker-custody.so',
          'ACTIONS_RUNNER_HOOK_JOB_STARTED': '/usr/local/bin/ci-job-started.sh',
          'ACTIONS_RUNNER_HOOK_JOB_COMPLETED': '/usr/local/bin/ci-job-completed.sh'}
STAGE = 'preflight'


def environment():
    return dict(base.ENV, RUNNER_WORK_DIRECTORY=str(ROOT / 'work'), AGENT_CI_PROFILE='static-2',
                AGENT_CI_SOCKET_REQUIRED='0', AGENT_CI_EXPIRED_POLICY_FALLBACK='1')


def mounts(new=False):
    result = [(str(ROOT / 'runner'), '/runner', True), (str(ROOT / 'home'), '/home/ci', True),
              (str(ROOT / 'work'), str(ROOT / 'work'), True),
              (str(HEAVY), '/run/agent-workload.lock', True)]
    if new:
        result += [(str(LEDGER), '/run/ci-admission', True),
                   (str(POLICY.parent), '/run/ci-policy', False),
                   (str(POOL), '/run/ci-pool', False)]
    return result


def regular(path, allow_empty=False):
    base.safe_path(path)
    require(stat.S_ISREG(path.stat().st_mode) and (allow_empty or path.stat().st_size > 0),
            'Required regular file missing')


def state():
    base.safe_path(ROOT)
    require(ROOT.stat().st_uid == 1102 and stat.S_IMODE(ROOT.stat().st_mode) == 0o700,
            'Private static-2 ancestor required')
    marker = ROOT / '.static-replica.json'
    regular(marker)
    require(json.loads(marker.read_text()) == {'managed': NAME, 'image': base.IMAGE},
            'Original provisioning marker mismatch')
    for name in ('runner', 'home', 'work'):
        path = ROOT / name
        base.safe_path(path)
        require(path.is_dir(), 'Persistent directory missing')
        if name != 'runner':
            require(path.stat().st_uid == 1102 and stat.S_IMODE(path.stat().st_mode) == 0o700,
                    'Private home/work ownership drift')
    # cp -a can leave runner owned by mapped UID 232072/mode 777. The private
    # ancestor protects it; do not chown or reject that known original layout.
    for name in ('.runner', 'run.sh', '.credentials', '.credentials_rsaparams'):
        regular(ROOT / 'runner' / name)  # Never read credential contents.
    require(os.access(ROOT / 'runner' / 'run.sh', os.X_OK), 'run.sh must be executable')
    identity = json.loads((ROOT / 'runner' / '.runner').read_text(encoding='utf-8-sig'))
    require(identity.get('agentId') == 121 and identity.get('agentName') == 'dealpipe-nas-static-2'
            and identity.get('gitHubUrl', '').rstrip('/') == 'https://github.com/dealpipe/dealpipe'
            and identity.get('workFolder') == str(ROOT / 'work'), 'Registration identity mismatch')
    require(not (ROOT / CLAIM).exists() and not (ROOT / CLAIM).is_symlink(), 'Prior attempt retained; no blind retry')


def owned(item, expected_id, new=False):
    require(item is not None and item['Id'] == expected_id and item['Name'] == '/' + NAME,
            'Exact container identity mismatch')
    cfg, host = item['Config'], item['HostConfig']
    labels = cfg.get('Labels') or {}
    require(labels.get(base.LABEL) == NAME
            and labels.get('com.docker.compose.project') == 'dealpipe-ci'
            and labels.get('com.docker.compose.service') == 'static-2', 'Ownership labels mismatch')
    actual = [(m['Source'], m['Destination'], m['RW']) for m in item['Mounts']]
    require(sorted(actual) == sorted(mounts(new)) and all(m['Type'] == 'bind' for m in item['Mounts']),
            'Mount allowlist mismatch')
    require(item['Image'] == (IMAGE if new else base.IMAGE)
            and cfg['User'] == '0:0' and cfg['Entrypoint'] == ['/usr/local/bin/ci-runner']
            and not cfg.get('Cmd') and host['NetworkMode'] == 'host'
            and not host['Privileged'] and not host.get('Devices') and not host.get('VolumesFrom')
            and not host.get('CapAdd') and host.get('PidMode', '') == ''
            and host['Memory'] == host['MemorySwap'] == (12 if new else 8) * base.GIB
            and host['NanoCpus'] == 2_000_000_000 and host['PidsLimit'] == 2048
            and host['Init'] is True and host['RestartPolicy']['Name'] == 'unless-stopped',
            'Image/resource/namespace/lifecycle mismatch')
    expected = environment() if new else dict(base.ENV, RUNNER_WORK_DIRECTORY=str(ROOT / 'work'))
    values = dict(entry.split('=', 1) for entry in cfg['Env'])
    require(all(values.get(k) == v for k, v in expected.items()), 'Runner environment mismatch')
    metadata = json.loads(docker('image', 'inspect', item['Image']))[0]
    inherited = dict(entry.split('=', 1) for entry in metadata['Config'].get('Env', []))
    require(values == dict(inherited, **expected), 'Unexpected private environment override')
    if new:
        require(all(values.get(k) == v for k, v in NATIVE.items()), 'Native Worker hooks missing')


def gate():
    policy, limits = admission_policy(POLICY, 'static-2', allow_expired=True)
    require(policy['expiresAt'] <= time.time(), 'Repair requires expired policy/global EX fallback')
    require(limits == {'memoryMiB': 12288, 'cpuPercent': 200}, 'Full static-2 hard ceiling required')
    require(int((POOL / 'memory.max').read_text()) == 32 * base.GIB
            and int((POOL / 'memory.swap.max').read_text()) == 0, 'Parent memory/swap drift')
    quota, period = map(int, (POOL / 'cpu.max').read_text().split())
    require(period > 0 and quota == period * 8, 'Parent CPU drift')
    memory = dict(line.replace(':', '').split()[:2] for line in Path('/proc/meminfo').read_text().splitlines())
    total = math.ceil(int(memory['MemTotal']) / 1024)
    available = int(memory['MemAvailable']) // 1024
    reserve = max(11865, math.ceil(total / 4))
    require(policy['reserveMiB'] >= reserve and total >= 32768 + reserve,
            'Physical memory reserve insufficient')
    require(available >= 12288 + policy['reserveMiB']
            and committed_memory_mib(POOL) + 12288 <= 32768
            and policy['maxConcurrentMiB'] >= 12288 and policy['cpuPercent'] >= 200,
            'Fresh full-ceiling headroom unavailable')
    # Historical ancestor OOM counters include the known child OOM. Require no
    # increment during this attempt, rather than pretending the baseline is zero.
    return tuple(int(dict(line.split() for line in (POOL / 'memory.events').read_text().splitlines())[k])
                 for k in ('oom', 'oom_kill'))


def processes(container_id):
    rows = docker('top', container_id, '-eo', 'pid,comm').splitlines()
    require(rows and rows[0].split() == ['PID', 'COMMAND'], 'Unexpected Docker top format')
    result = {}
    for row in rows[1:]:
        pid, comm = row.split()
        require(pid.isdecimal() and int(pid) > 1, 'Invalid host PID')
        result[int(pid)] = comm
    require(bool(result), 'Empty process inspection')
    return result


def identity(pid, container_id):
    proc = Path('/proc') / str(pid)
    status = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines() if ':' in line)
    require(status['Name'].strip() == 'Runner.Listener'
            and set(status['Uid'].split()) == {'1102'}
            and container_id in (proc / 'cgroup').read_text(), 'Listener host PID identity mismatch')
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return fields[19]  # starttime, field 22; comm can contain spaces/parentheses.


def stopped(pid):
    tasks = list((Path('/proc') / str(pid) / 'task').iterdir())
    require(bool(tasks), 'Listener threads missing')
    for task in tasks:
        status = (task / 'status').read_text()
        state_line = next(line for line in status.splitlines() if line.startswith('State:'))
        require(state_line.split()[1] == 'T' and not (task / 'children').read_text().strip(),
                'Listener not fully stopped or has active child')


def freeze(container_id, stack):
    rows = processes(container_id)
    listeners = [pid for pid, comm in rows.items() if comm == 'Runner.Listener']
    require(len(listeners) == 1, 'Exactly one Listener required')
    pid = listeners[0]
    start = identity(pid, container_id)
    fd = os.pidfd_open(pid)
    stack.callback(os.close, fd)
    require(identity(pid, container_id) == start, 'Listener identity changed')
    status = (Path('/proc') / str(pid) / 'status').read_text()
    require(next(x for x in status.splitlines() if x.startswith('State:')).split()[1] != 'T',
            'Listener was already stopped')
    # The descriptor prevents PID reuse from targeting another process.
    signal.pidfd_send_signal(fd, signal.SIGSTOP)
    return pid, start, fd


def quiesced(container_id, frozen):
    pid, start, _ = frozen
    require(identity(pid, container_id) == start, 'Frozen Listener identity changed')
    stopped(pid)
    rows = processes(container_id)
    require([p for p, comm in rows.items() if comm == 'Runner.Listener'] == [pid],
            'Listener set changed')
    require(all(comm in {'Runner.Listener', 'tini', 'docker-init', 'bash', 'run.sh'} for comm in rows.values()),
            'Worker or unexpected active process; refusing stop')
    # Every other container process must be a supervisor ancestor. A sibling
    # shell (or a Worker whose comm was changed) is not evidence of idleness.
    chain, current = set(), pid
    while current in rows and current not in chain:
        chain.add(current)
        fields = (Path('/proc') / str(current) / 'stat').read_text().rsplit(')', 1)[1].split()
        current = int(fields[1])
    require(chain == set(rows), 'Unexpected process outside Listener supervisor chain')


def candidate_command(attempt):
    result = ['create', '--name', NAME, '--pull=never', '--user=0:0', '--network=host',
              '--memory=12g', '--memory-swap=12g', '--cpus=2', '--pids-limit=2048', '--init',
              '--restart=unless-stopped', '--label', f'{base.LABEL}={NAME}',
              '--label', f'{base.ATTEMPT}={attempt}', '--label=com.docker.compose.project=dealpipe-ci',
              '--label=com.docker.compose.service=static-2']
    for source, target, rw in mounts(True):
        result += ['--mount', f'type=bind,src={source},dst={target}' + ('' if rw else ',readonly')]
    for key, value in environment().items():
        result += ['--env', f'{key}={value}']
    return [*result, IMAGE]


def execute(args):
    global STAGE
    STAGE = 'preflight'
    require(os.geteuid() == 1102, 'Run as UID 1102')
    require(re.fullmatch('[0-9a-f]{64}', args.expected_container_id), 'Full old container ID required')
    info = json.loads(docker('info', '--format', '{{json .}}'))
    require('name=rootless' in info['SecurityOptions'], 'Rootless engine required')
    state()
    require(inspect(BACKUP) is None, 'Backup already exists; no blind retry')
    old = inspect(NAME)
    owned(old, args.expected_container_id)
    require(old['State']['Running'] and not old['State'].get('Paused')
            and not old['State'].get('Restarting'), 'Old container must be actively running')
    metadata = json.loads(docker('image', 'inspect', IMAGE))[0]
    require(metadata['Id'] == IMAGE and not metadata['Config'].get('Volumes')
            and metadata['Config']['Entrypoint'] == ['/usr/local/bin/ci-runner'], 'Reviewed image missing/drifted')
    values = dict(entry.split('=', 1) for entry in metadata['Config']['Env'])
    require(all(values.get(k) == v for k, v in NATIVE.items()), 'Image lacks native custody hooks')
    regular(POLICY)
    for path in (HEAVY, LEDGER / 'mutex.lock'):
        regular(path, allow_empty=True)
    for path in (LEDGER, POLICY.parent, POOL):
        base.safe_path(path)
        require(path.is_dir(), 'Admission mount source missing')
    baseline = gate()
    if not args.apply:
        print('Dry-run passed: static-2 only, retained registration, 12 GiB/2 CPU; no mutation.')
        return
    frozen, stop_attempted = None, False
    with ExitStack() as stack:
        # Claim is durable before signals/engine mutations. Never erase on error.
        STAGE = 'claim-attempt'
        fd = os.open(ROOT / CLAIM, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'oldId': args.expected_container_id, 'newImage': IMAGE, 'backup': BACKUP}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        try:
            STAGE = 'admission'
            stack.enter_context(lock(HEAVY, 1))
            stack.enter_context(capacity_reservation(LEDGER, POLICY, 'static-2', '/proc/meminfo', POOL, exclusive=True))
            require(gate() == baseline, 'New OOM event before migration')
            STAGE = 'freeze-listener'
            frozen = freeze(args.expected_container_id, stack)
            # Stabilize after SIGSTOP; any pre-freeze accepted Worker survives and
            # causes refusal. Check twice with all Listener threads stopped.
            time.sleep(.25)
            quiesced(args.expected_container_id, frozen)
            time.sleep(.25)
            quiesced(args.expected_container_id, frozen)
            STAGE = 'disable-old-restart'
            docker('update', '--restart=no', args.expected_container_id)
            quiesced(args.expected_container_id, frozen)
            STAGE = 'stop-old'
            stop_attempted = True  # Ambiguous stop failures must never auto-resume.
            docker('stop', '--time=30', args.expected_container_id)
            old = inspect(NAME)
            require(old['Id'] == args.expected_container_id and old['State']['Status'] == 'exited'
                    and not old['State']['Running'] and not old['State'].get('Restarting')
                    and old['HostConfig']['RestartPolicy']['Name'] == 'no', 'Old container not inactive')
            STAGE = 'retain-old-backup'
            require(inspect(BACKUP) is None, 'Backup name collision')
            docker('rename', args.expected_container_id, BACKUP)
            STAGE = 'create-candidate'
            require(inspect(NAME) is None, 'Original name unexpectedly occupied')
            require(gate() == baseline, 'New OOM before candidate creation')
            docker(*candidate_command(args.expected_container_id))
            candidate = inspect(NAME)
            owned(candidate, candidate['Id'], new=True)
            require(candidate['State']['Status'] == 'created'
                    and candidate['Config']['Labels'].get(base.ATTEMPT) == args.expected_container_id,
                    'Candidate ownership/state mismatch')
            STAGE = 'start-candidate'
            docker('start', candidate['Id'])
            require(inspect(NAME)['State']['Running'], 'Candidate failed to remain running')
            require(gate() == baseline, 'New OOM during startup')
            STAGE = 'complete'
        except BaseException:
            if frozen is not None and not stop_attempted:
                STAGE += '-resume-old'
                pid, start, listener_fd = frozen
                if identity(pid, args.expected_container_id) == start:
                    signal.pidfd_send_signal(listener_fd, signal.SIGCONT)
            raise
    print('Static-2 replacement started; old backup/state retained. Operator must verify real CI before restoring label.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-container-id', required=True, help='Full ID from current operator receipt')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        execute(args)
    except (Exception, KeyboardInterrupt):
        # Never emit exception/engine output or Config.Env; it can contain secrets.
        print(f'Static-2 repair refused/failed at {STAGE}; retain all state and inspect privately. No automatic cleanup.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
