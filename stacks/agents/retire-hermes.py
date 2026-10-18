#!/usr/bin/env python3
"""Retire only Hermes through Portainer; preserve all homes, secrets and volumes."""
import argparse
import json
import os
from pathlib import Path
import time

import yaml
from deploy import Portainer


def retire(api, endpoint, name, backup, apply=False):
    matches = [s for s in api.request('GET', '/stacks')
               if s['Name'] == name and s['EndpointId'] == endpoint]
    if len(matches) != 1:
        raise ValueError('Expected exactly one existing agent stack')
    stack = api.request('GET', f'/stacks/{matches[0]["Id"]}')
    original = api.request('GET', f'/stacks/{stack["Id"]}/file')['StackFileContent']
    compose = yaml.safe_load(original)
    services = compose['services']
    if 't3' not in services:
        raise ValueError('Refusing to modify a stack without T3')
    services.pop('hermes', None)
    environment = [e for e in stack.get('Env', []) if not e['name'].startswith('HERMES_')]
    containers = [c for c in api.request('GET', f'/endpoints/{endpoint}/docker/containers/json?all=true')
                  if c.get('Labels', {}).get('com.docker.compose.project') == name
                  and c.get('Labels', {}).get('com.docker.compose.service') == 'hermes']
    print(json.dumps({'remaining_services': sorted(services), 'hermes_containers': len(containers),
                      'apply': apply, 'state_and_volumes': 'preserved'}))
    if not apply:
        return
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup.chmod(0o700)
    for filename, data in [('compose.yaml', original), ('env.json', json.dumps(stack.get('Env', [])))]:
        path = backup / filename
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(data)
    result = api.request('PUT', f'/stacks/{stack["Id"]}?endpointId={endpoint}', {
        'StackFileContent': yaml.safe_dump(compose, sort_keys=False), 'Env': environment,
        'Prune': False, 'PullImage': False,
    })
    deadline = time.monotonic() + 120
    while result.get('Status') == 3:
        if time.monotonic() >= deadline:
            raise RuntimeError('Stack update still pending; inspect before retrying')
        time.sleep(2)
        result = api.request('GET', f'/stacks/{stack["Id"]}')
    if result.get('Status') != 1:
        raise RuntimeError('Stack update failed; private rollback snapshot retained')
    for container in containers:
        if container.get('State') == 'running':
            api.request('POST', f'/endpoints/{endpoint}/docker/containers/{container["Id"]}/stop?t=30')
        # No v=true: volumes and bind-mounted state remain recoverable.
        api.request('DELETE', f'/endpoints/{endpoint}/docker/containers/{container["Id"]}')
    remaining = api.request('GET', f'/endpoints/{endpoint}/docker/containers/json?all=true')
    if any(c.get('Labels', {}).get('com.docker.compose.project') == name
           and c.get('Labels', {}).get('com.docker.compose.service') == 'hermes' for c in remaining):
        raise RuntimeError('Hermes container remains; inspect Portainer')
    print('Hermes retired; T3 stack retained. Archived state and private rollback snapshot preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://nas.tailb80645.ts.net:9443')
    parser.add_argument('--token-file', default='/var/lib/agent-platform/secrets/portainer-token')
    parser.add_argument('--connect-address', default='100.125.180.36')
    parser.add_argument('--endpoint', type=int, default=2)
    parser.add_argument('--name', default='homelab-agents')
    parser.add_argument('--backup', type=Path, default=Path('/var/lib/agent-platform/retired/hermes'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    retire(Portainer(args.url, args.token_file, args.connect_address), args.endpoint,
           args.name, args.backup, args.apply)


if __name__ == '__main__':
    main()
