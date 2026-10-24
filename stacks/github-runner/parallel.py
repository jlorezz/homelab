#!/usr/bin/env python3
"""Render opt-in hook migration and CI2 Compose; no engine/API calls or starts.

Existing input MUST be reconciled against live Portainer configuration first.
The old capacity renderer/deployer is deliberately not used for this rollout.
"""
import argparse
import copy
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agents'))
from workload import admission_policy

ADMISSION = [
    '/var/lib/agent-workloads/ci-admission:/run/ci-admission',
    '/etc/dealpipe-ci-admission:/run/ci-policy:ro',
    '/sys/fs/cgroup/user.slice/user-1102.slice:/run/ci-pool:ro',
]

# Validate the effective service after YAML merge keys have been expanded. A
# mount allowlist alone cannot constrain Compose's other namespace/env overrides.
SERVICE_KEYS = {'image', 'restart', 'init', 'user', 'network_mode', 'cpus',
                'mem_limit', 'memswap_limit', 'pids_limit', 'healthcheck',
                'volumes', 'environment', 'container_name', 'labels'}
ENV_KEYS = {'TZ', 'HOME', 'RUNNER_ALLOW_RUNASROOT', 'RUNNER_NAME', 'RUNNER_LABEL',
            'RUNNER_WORK_DIRECTORY', 'AGENT_CI_EXCLUSIVE', 'AGENT_CI_PROFILE',
            'AGENT_CI_ENGINE_LOCK', 'AGENT_CI_SOCKET_REQUIRED'}


def validate_service(name, lane):
    if not isinstance(lane, dict) or set(lane) - SERVICE_KEYS:
        raise ValueError('Unsupported effective service setting; reconcile before rendering')
    env = lane.get('environment')
    if not isinstance(env, dict) or set(env) - ENV_KEYS:
        raise ValueError('Unsupported worker environment override')
    expected = {'user': '0:0', 'network_mode': 'host', 'init': True,
                'restart': 'unless-stopped'}
    for key, value in expected.items():
        if key in lane and lane[key] != value:
            raise ValueError('Unexpected worker identity/network/lifecycle setting')
        lane[key] = value
    if ('HOME' in env and env['HOME'] != '/home/ci'
            or 'RUNNER_ALLOW_RUNASROOT' in env and str(env['RUNNER_ALLOW_RUNASROOT']) != '1'):
        raise ValueError('Unexpected runner home or root mapping')
    if (type(lane.get('cpus')) not in (int, float) or not 0 < lane['cpus'] <= 8
            or not re.fullmatch(r'[1-9][0-9]*[mMgG]', str(lane.get('mem_limit', '')))
            or lane['mem_limit'] != lane.get('memswap_limit')):
        raise ValueError('Explicit bounded CPU and equal memory/swap ceilings required')
    pids = lane.setdefault('pids_limit', 2048 if name.startswith('static') else 4096)
    if type(pids) is not int or not 0 < pids <= 4096:
        raise ValueError('Bounded PID ceiling required')
    if 'container_name' in lane:
        expected_name = {'runner': 'dealpipe-ci-runner-1', 'browser': 'dealpipe-ci-browser-1',
                         'static': 'dealpipe-ci-static-1', 'static-2': 'dealpipe-ci-static-2-1'}[name]
        if lane['container_name'] != expected_name:
            raise ValueError('Unexpected permanent runner identity')
    state = {'runner': '', 'browser': '/browser', 'static': '/static', 'static-2': '/static-2'}[name]
    env.update(RUNNER_NAME={'runner': 'dealpipe-nas-ci', 'browser': 'dealpipe-nas-browser',
                           'static': 'dealpipe-nas-static', 'static-2': 'dealpipe-nas-static-2'}[name],
               RUNNER_LABEL='dealpipe-nas-static' if name.startswith('static') else
                            'dealpipe-nas-ci' if name == 'runner' else 'dealpipe-nas-browser',
               RUNNER_WORK_DIRECTORY=f'/var/lib/dealpipe-ci{state}/work')


def hooks(lane, profile, engine=None):
    volumes = lane['volumes']
    volumes[:] = [v for v in volumes if not any(x in v for x in
                  ('/heavy.lock:', '/static.lock:', '/ci-main.lock:', '/ci-browser.lock:',
                   '/ci-2.lock:', ':/run/ci-admission', ':/run/ci-policy', ':/run/ci-pool'))]
    volumes.extend(['/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock', *ADMISSION])
    env = lane['environment']
    env.update(AGENT_CI_EXCLUSIVE='0', AGENT_CI_PROFILE=profile)
    env.pop('AGENT_CI_ENGINE_LOCK', None)
    env['AGENT_CI_SOCKET_REQUIRED'] = '1' if engine else '0'
    if engine:
        volumes.append(f'/var/lib/agent-workloads/{engine}.lock:/run/ci-engine.lock')
        env['AGENT_CI_ENGINE_LOCK'] = '/run/ci-engine.lock'


def render(compose, image, policy_path):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Supply reviewed NEW hook image digest, never the old live image')
    if image == 'sha256:121de326c9541820ea00f82a924286b5d6f9134d48c66cef1f50064e722ee079':
        raise ValueError('Old live image has incompatible exclusive hooks')
    if not isinstance(compose, dict) or set(compose) - {'name', 'version', 'services', 'x-runner'}:
        raise ValueError('Unsupported Compose project setting')
    # Drop extension templates after checking the fully expanded services. They
    # must never reintroduce settings when an operator reviews/edits the output.
    existing = {'services': copy.deepcopy(compose['services'])}
    services = existing['services']
    if set(services) != {'runner', 'browser', 'static', 'static-2'}:
        raise ValueError('Inventory must include exactly the four current lanes, including deployed static-2')
    for name, lane in services.items():
        validate_service(name, lane)
        profile = {'runner': 'ci1', 'browser': 'browser'}.get(name, name)
        admission_policy(policy_path, profile)
        engine = {'runner': 'ci-main', 'browser': 'ci-browser'}.get(name)
        sockets = [v for v in lane['volumes'] if 'docker.sock' in v]
        expected = {'runner': '/run/user/1102/docker.sock:/var/run/docker.sock',
                    'browser': '/run/user/1102/dealpipe-browser/docker.sock:/var/run/docker.sock'}
        if sockets != ([expected[name]] if engine else []):
            raise ValueError('Reconcile live browser socket/isolation before rendering')
        state = {'runner': '', 'browser': '/browser', 'static': '/static', 'static-2': '/static-2'}[name]
        allowed = {
            f'/var/lib/dealpipe-ci{state}/runner:/runner',
            f'/var/lib/dealpipe-ci{state}/home:/home/ci',
            f'/var/lib/dealpipe-ci{state}/work:/var/lib/dealpipe-ci{state}/work',
            '/var/lib/dealpipe-ci/secrets/registration-token:/run/secrets/registration-token:ro',
            '/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock',
            '/var/lib/agent-workloads/static.lock:/run/agent-workload.lock',
            *ADMISSION,
        }
        if engine:
            allowed.update([expected[name], f'/var/lib/agent-workloads/{engine}.lock:/run/ci-engine.lock'])
        if any(v not in allowed for v in lane['volumes']):
            raise ValueError('Unexpected worker mount; controller secrets/other homes/host sockets forbidden')
        hooks(lane, profile, engine)
        lane['image'] = image
    admission_policy(policy_path, 'ci2')
    second = copy.deepcopy(services['runner'])
    second['container_name'] = 'dealpipe-ci2-runner'  # Compose refuses scaling a singleton.
    second['cgroup_parent'] = 'dealpipe-ci2.slice'
    second['volumes'] = [v.replace('/var/lib/dealpipe-ci/runner:', '/var/lib/dealpipe-ci/ci2/runner:')
                         .replace('/var/lib/dealpipe-ci/home:', '/var/lib/dealpipe-ci/ci2/home:')
                         .replace('/var/lib/dealpipe-ci/work', '/var/lib/dealpipe-ci/ci2/work')
                         .replace('/run/user/1102/docker.sock:', '/run/user/1102/dealpipe-ci2/docker.sock:')
                         for v in second['volumes']]
    second['environment'].update(RUNNER_NAME='dealpipe-nas-ci-2', RUNNER_LABEL='dealpipe-nas-ci',
                                RUNNER_WORK_DIRECTORY='/var/lib/dealpipe-ci/ci2/work')
    hooks(second, 'ci2', 'ci-2')
    return existing, {'name': 'dealpipe-ci2', 'services': {'runner': second}}


def main():
    import yaml
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    old, second = render(yaml.safe_load(args.compose.read_text()), args.image, args.policy)
    args.output.mkdir(mode=0o700)  # Never overwrite a previous rendered batch.
    for name, value in [('existing.yaml', old), ('ci2.yaml', second)]:
        (args.output / name).write_text(yaml.safe_dump(value, sort_keys=False))


if __name__ == '__main__':
    main()
