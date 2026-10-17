#!/usr/bin/env python3
"""Add knowledge mounts to the existing Portainer stack, preserving images/env/services.
Stops only the stack's agents while reconciling their owned home policy settings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy import Portainer
from configure import provision


def connect(source, apply=False):
    api = Portainer('https://nas.tailb80645.ts.net:9443', '/var/lib/agent-platform/secrets/portainer-token', '100.125.180.36')
    stack = next(s for s in api.request('GET', '/stacks') if s['Name'] == 'homelab-agents' and s['EndpointId'] == 2)
    stack = api.request('GET', f'/stacks/{stack["Id"]}')
    original = api.request('GET', f'/stacks/{stack["Id"]}/file')['StackFileContent']
    compose = yaml.safe_load(original)
    for name in ['t3', 'opencode', 'hermes']:
        if name not in compose['services']: continue
        desired = yaml.safe_load((source / ('compose.yaml' if name == 't3' else f'compose.{name}.yaml')).read_text())['services'][name]
        service = compose['services'][name]
        revision = (source / 'knowledge/brain.py').read_bytes() + b''.join(p.read_bytes() for p in sorted((source / 'knowledge/units').iterdir()))
        service.setdefault('labels', {})['io.qnoxslab.shared-brain.revision'] = hashlib.sha256(revision).hexdigest()
        volumes = service.setdefault('volumes', [])
        for volume in desired['volumes']:
            if volume not in volumes:
                target = volume.split(':')[1]
                if any(v.split(':')[1] == target for v in volumes):
                    raise ValueError('Conflicting mount target; inspect before deployment')
                volumes.append(volume)
        if name == 'hermes':
            service['group_add'] = list(dict.fromkeys([*service.get('group_add', []), '1100']))
    content = yaml.safe_dump(compose, sort_keys=False)
    print('Services:', ', '.join(compose['services']), '; images, ports, environment and other stack settings preserved')
    if not apply: return
    private = Path('/var/lib/agent-platform/knowledge/deployment')
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    for name, value in [('before-compose.yaml', original), ('before-env.json', json.dumps(stack['Env']))]:
        path = private / name
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream: stream.write(value)
    containers = [c for c in api.request('GET', '/endpoints/2/docker/containers/json')
                  if c.get('Labels', {}).get('com.docker.compose.project') == 'homelab-agents'
                  and c.get('Labels', {}).get('com.docker.compose.service') in {'t3', 'hermes', 'opencode'}]
    stopped = []
    try:
        for container in containers:
            api.request('POST', f'/endpoints/2/docker/containers/{container["Id"]}/stop?t=30')
            stopped.append(container['Id'])
        provision(Path('/var/lib/agent-platform'), source, apply=True)
        result = api.request('PUT', f'/stacks/{stack["Id"]}?endpointId=2', {
            'StackFileContent': content, 'Env': stack['Env'], 'Prune': False, 'PullImage': False,
        })
        deadline = time.monotonic() + 120
        while result.get('Status') == 3 and time.monotonic() < deadline:
            time.sleep(2)
            result = api.request('GET', f'/stacks/{stack["Id"]}')
        if result.get('Status') != 1:
            raise RuntimeError('Inspect the existing stack; deployment has not completed')
        print('Knowledge mounts and shared policy applied through Portainer.')
    except Exception:
        # Restore availability of the old services if migration/update fails.
        for ident in stopped:
            try: api.request('POST', f'/endpoints/2/docker/containers/{ident}/start')
            except Exception: pass
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    connect(args.source, args.apply)
