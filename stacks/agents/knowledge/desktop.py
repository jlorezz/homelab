#!/usr/bin/env python3
"""Install/use desktop transports; every query and proposal executes on the NAS."""
import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

CONFIG = Path.home() / '.config/shared-brain'


def ssh():
    settings = json.loads((CONFIG / 'connection.json').read_text())
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o',
            'UserKnownHostsFile=' + str(CONFIG / 'known_hosts'), settings['user'] + '@' + settings['host']]


def run(action, arguments):
    if action == 'mcp':
        return subprocess.call(ssh() + ['/var/lib/agent-platform/bin/brain-mcp'])
    if action == 'propose' and len(arguments) == 1:
        file, extra, command = arguments[0], '', 'submit'
    elif action == 'receipt' and len(arguments) == 2 and re.fullmatch('[a-z][a-z0-9-]{0,63}', arguments[0]):
        file, extra, command = arguments[1], ' --project ' + shlex.quote(arguments[0]), 'submit-receipt'
    else:
        raise SystemExit('Usage: brain-propose proposal.json | brain-receipt PROJECT handoff.json')
    remote = '''set -eu
proposal=$(mktemp /var/tmp/brain-submission.XXXXXX)
trap 'rm -f "$proposal"' EXIT
cat > "$proposal"
/var/lib/agent-platform/bin/brain-admin COMMAND --spool /var/lib/agent-platform/knowledge/proposals/desktop --file "$proposal" EXTRA
'''.replace('COMMAND', command).replace('EXTRA', extra)
    return subprocess.run(ssh() + [remote], input=Path(file).read_bytes()).returncode


def install(known_hosts, user, host, clients):
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    if known_hosts.resolve() != (CONFIG / 'known_hosts').resolve():
        shutil.copyfile(known_hosts, CONFIG / 'known_hosts')
    (CONFIG / 'known_hosts').chmod(0o600)
    (CONFIG / 'connection.json').write_text(json.dumps({'user': user, 'host': host}) + '\n')
    script = CONFIG / 'desktop.py'
    if Path(__file__).resolve() != script.resolve(): shutil.copyfile(__file__, script)
    bindir = Path.home() / '.local/bin'
    bindir.mkdir(parents=True, exist_ok=True)
    for name, action in [('brain-mcp', 'mcp'), ('brain-propose', 'propose'), ('brain-receipt', 'receipt')]:
        path = bindir / name
        path.write_text('#!/bin/sh\nexec python3 ' + shlex.quote(str(script)) + ' ' + action + ' "$@"\n')
        path.chmod(0o755)
    executable = str(bindir / 'brain-mcp')
    commands = {'codex': ['codex', 'mcp', 'add', 'shared-brain', '--', executable],
                'claude': ['claude', 'mcp', 'add', '--scope', 'user', '--transport', 'stdio', 'shared-brain', '--', executable],
                'opencode': ['opencode', 'mcp', 'add', '--global', 'shared-brain', '--', executable]}
    for client in clients:
        if shutil.which(client): subprocess.run(commands[client], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    setup = sub.add_parser('install')
    setup.add_argument('--known-hosts', type=Path, required=True)
    setup.add_argument('--user', default='jlorezz')
    setup.add_argument('--host', default='100.125.180.36')
    setup.add_argument('--client', action='append', choices=['codex', 'claude', 'opencode'], default=[])
    for action in ['mcp', 'propose', 'receipt']:
        sub.add_parser(action).add_argument('arguments', nargs='*')
    args = parser.parse_args()
    if args.action == 'install': install(args.known_hosts, args.user, args.host, args.client)
    else: raise SystemExit(run(args.action, args.arguments))
