#!/usr/bin/env python3
"""One-shot private-label CI2 canary. Dry-run by default; never stop or remove."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys

from parallel import ADMISSION, hooks
from workload import admission_policy

SOCKET = Path('/run/user/1102/dealpipe-ci2/docker.sock')
ROOT = Path('/var/lib/dealpipe-ci/ci2')
POLICY = Path('/var/lib/agent-workloads/ci-policy/policy.json')
NAME = 'dealpipe-nas-ci-2-canary'
CONFIG = NAME + '-config'
LABEL = 'dealpipe-ci2-canary'
OWNER = 'io.qnoxslab.ci2-canary'
WORK = '/var/lib/dealpipe-ci/work'
OLD_IMAGE = 'sha256:121de326c9541820ea00f82a924286b5d6f9134d48c66cef1f50064e722ee079'

# No Listener, socket, controller secret, token argument or token environment in
# Docker metadata. The main process exits on the exec's completion marker, or
# after five minutes if exec never happens. No helper cleanup races a job.
KEEPER = 'for ((i=0;i<300;i++)); do test ! -e /tmp/config-finished || exit 0; sleep 1; done; exit 1'
REGISTER = r'''set -euo pipefail
set +x
trap 'touch /tmp/config-finished' EXIT
IFS= read -r ACTIONS_RUNNER_INPUT_TOKEN
export ACTIONS_RUNNER_INPUT_TOKEN
cd /runner
cp -a /opt/actions-runner/. /runner/
# cp -a copies upstream directory ownership/mode too; restore private state.
chown 0:0 /runner
chmod 700 /runner
./config.sh --unattended --url https://github.com/dealpipe/dealpipe \
  --name dealpipe-nas-ci-2-canary --labels dealpipe-ci2-canary \
  --work /var/lib/dealpipe-ci/work >/dev/null 2>&1
unset ACTIONS_RUNNER_INPUT_TOKEN
'''


class Refused(RuntimeError):
    pass


def docker(args, *, data=None, output=False, timeout=30):
    # Discard inherited Docker contexts, credential helpers and token env. No
    # exception text, stdout or stderr from a failed subprocess is surfaced.
    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': '/nonexistent', 'DOCKER_CONFIG': '/nonexistent',
           'XDG_RUNTIME_DIR': '/run/user/1102'}
    try:
        result = subprocess.run(['docker', '--host=unix://' + str(SOCKET), *args],
                                input=data, stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Docker operation failed or timed out; retain all CI2 state') from None
    if result.returncode:
        raise Refused('Docker operation failed; retain all CI2 state')
    if output:
        if args[:2] == ['container', 'ls'] and not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            raise Refused('Invalid Docker response; retain all CI2 state') from None


def private_directory(path):
    # Reject symlinks in every ancestor too: never traverse a redirected state root.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused('Symlink in private state path')
    item = path.stat()
    if not stat.S_ISDIR(item.st_mode) or item.st_uid != 1102 or item.st_mode & 0o077:
        raise Refused('State directories must be private and owned by UID1102')


def lane():
    result = {'volumes': [f'{ROOT}/runner:/runner', f'{ROOT}/home:/home/ci',
                          f'{WORK}:{WORK}', f'{SOCKET}:/var/run/docker.sock'],
              'environment': {'HOME': '/home/ci', 'RUNNER_ALLOW_RUNASROOT': '1',
                              'RUNNER_NAME': NAME, 'RUNNER_LABEL': LABEL,
                              'RUNNER_WORK_DIRECTORY': WORK}}
    hooks(result, 'ci2', 'ci-2')
    # Provisioning requires fresh policy; later expiry uses reviewed exclusive admission.
    result['environment']['AGENT_CI_EXPIRED_POLICY_FALLBACK'] = '1'
    return result


def preflight(image):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image) or image == OLD_IMAGE:
        raise Refused('Supply the reviewed new immutable image ID')
    if os.geteuid() != 1102:
        raise Refused('Run as the CI account UID1102')
    item = SOCKET.lstat()
    if not stat.S_ISSOCK(item.st_mode) or item.st_uid != 1102:
        raise Refused('CI2 socket must be a UID1102-owned Unix socket')
    private_directory(ROOT)
    for name in ('runner', 'home'):
        private_directory(ROOT / name)
        if any((ROOT / name).iterdir()):
            raise Refused('State must be empty; existing registration/state is never reused')
    if (ROOT / '.canary-attempt').exists() or (ROOT / '.canary-attempt').is_symlink():
        raise Refused('Previous canary attempt exists; operator reconciliation required')
    admission_policy(POLICY, 'ci2')
    for mount in lane()['volumes']:
        source = Path(mount.split(':')[0])
        if source in (ROOT / 'runner', ROOT / 'home', Path(WORK), SOCKET):
            continue  # Canonical work source is resolved by CI2, never by this host client.
        item = source.stat()
        if source.is_symlink() or not (stat.S_ISDIR(item.st_mode) if mount in ADMISSION else stat.S_ISREG(item.st_mode)):
            raise Refused('Required admission mount missing or redirected')
    info = docker(['info', '--format', '{{json .}}'], output=True)
    if (info.get('DockerRootDir') != '/var/lib/dealpipe-ci/docker'
            or 'name=rootless' not in info.get('SecurityOptions', [])
            or info.get('CgroupDriver') != 'systemd' or info.get('CgroupVersion') != '2'
            or info.get('Containers') != 0):
        raise Refused('CI2 must be empty, rootless, cgroup-v2/systemd with canonical data-root')
    if docker(['container', 'ls', '--all', '--format', '{{json .Names}}'], output=True) is not None:
        raise Refused('CI2 container inventory must be empty')
    # Empty docker ls emits no JSON; any entry (including an old canary) refuses.
    images = docker(['image', 'inspect', image], output=True)
    candidate = images[0]
    cfg = candidate['Config']
    env = dict(value.split('=', 1) for value in cfg.get('Env', []) if '=' in value)
    expected = {'LD_PRELOAD': '/usr/local/lib/agent-platform/worker-custody.so',
                'ACTIONS_RUNNER_HOOK_JOB_STARTED': '/usr/local/bin/ci-job-started.sh',
                'ACTIONS_RUNNER_HOOK_JOB_COMPLETED': '/usr/local/bin/ci-job-completed.sh'}
    if (candidate.get('Id') != image or cfg.get('Entrypoint') != ['/usr/local/bin/ci-runner']
            or cfg.get('Volumes')
            or any(env.get(k) != v for k, v in expected.items())
            or any(re.search(r'TOKEN|SECRET|PASSWORD|CREDENTIAL', key, re.I) for key in env)):
        raise Refused('Image identity, entrypoint, native custody/hooks or secret-free defaults invalid')


def create_args(image, temporary=False):
    args = ['container', 'create', '--name', CONFIG if temporary else NAME,
            '--label', OWNER + '=' + (CONFIG if temporary else NAME),
            '--init', '--user', '0:0', '--network', 'host', '--cgroup-parent', 'dealpipe-ci2.slice',
            '--memory', '12g', '--memory-swap', '12g', '--cpus', '4', '--pids-limit', '4096',
            '--restart', 'no' if temporary else 'unless-stopped']
    spec = lane()
    volumes = spec['volumes'][:2] if temporary else spec['volumes']
    environment = {'HOME': '/home/ci', 'RUNNER_ALLOW_RUNASROOT': '1'} if temporary else spec['environment']
    for volume in volumes:
        source, destination, *options = volume.split(':')
        args += ['--mount', f'type=bind,src={source},dst={destination}' + (',readonly' if options == ['ro'] else '')]
    for key, value in environment.items():
        args += ['--env', key + '=' + value]
    if temporary:
        args += ['--log-driver', 'none', '--entrypoint', '/bin/bash', image, '-c', KEEPER]
    else:
        args += [image]
    return args


def registered():
    data = json.loads((ROOT / 'runner/.runner').read_text(encoding='utf-8-sig'))
    if (data.get('agentName', data.get('runnerName')) != NAME
            or data.get('gitHubUrl') != 'https://github.com/dealpipe/dealpipe'
            or data.get('workFolder') != WORK
            or not (ROOT / 'runner/.credentials').is_file()):
        raise Refused('Registration state does not match this private canary')


def read_token(stream):
    # Linux-only host helper: bound a stalled producer as well as input length.
    def expired(_signum, _frame):
        raise Refused('Registration token stdin timed out')
    previous = signal.signal(signal.SIGALRM, expired)
    try:
        signal.alarm(30)
        return stream.read(4097)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def run(image, apply=False, stream=None):
    preflight(image)
    if not apply:
        return 'DRY RUN: CI2 preflight passed; would register private canary and start listener; no mutations'
    # An exclusive persistent claim serializes attempts and blocks automatic retry
    # even after partial failure. Never delete state or deregister automatically.
    descriptor = os.open(ROOT / '.canary-attempt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    raw = read_token(stream or sys.stdin)
    if len(raw) > 4096 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,4095}\n?', raw):
        raise Refused('Expected one short-lived registration token on stdin')
    token = raw.rstrip('\n').encode() + b'\n'
    del raw
    # Token consumption occurs exactly once, never in a Docker env/argv.
    docker(create_args(image, temporary=True))
    docker(['container', 'start', CONFIG])
    try:
        docker(['exec', '-i', CONFIG, '/bin/bash', '-c', REGISTER], data=token, timeout=180)
    finally:
        del token
    docker(['container', 'wait', CONFIG], timeout=30)
    state = docker(['container', 'inspect', '--format', '{{json .State}}', CONFIG], output=True)
    if state.get('Running') is not False or state.get('ExitCode') != 0:
        raise Refused('Configuration container has not exited successfully; retain state')
    registered()
    admission_policy(POLICY, 'ci2')  # Registration may have crossed the freshness deadline.
    docker(create_args(image))
    docker(['container', 'start', NAME])
    return 'Canary start requested; retain all state. Parent must verify labels, custody and live job gates.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(run(args.image, args.apply))
    except Exception:
        # Token-bearing subprocess exceptions and credential/state content must
        # never enter logs, tracebacks, receipts or the caller's terminal.
        print('CI2 canary refused or failed; retain ALL state and containers; operator reconciliation required.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
