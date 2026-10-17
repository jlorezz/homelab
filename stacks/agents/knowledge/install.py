#!/usr/bin/env python3
"""Narrow NAS rollout; also used to render the exact Nix-owned systemd units.
Does not activate NixOS, modify agent homes, or restart agent containers.
"""
import argparse
import os
from pathlib import Path
import shutil
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
STATE = Path('/var/lib/agent-platform/knowledge')


def units(python, runtime):
    return {p.name: p.read_text().replace('[Service]', '[Service]\nEnvironment=PATH=' + os.environ.get('PATH', '/run/current-system/sw/bin')).replace('@python@', str(python)).replace('@runtime@', str(runtime)).replace('@state@', str(STATE))
            for p in (HERE / 'units').iterdir()}


def install(python):
    if os.geteuid() != 0:
        raise ValueError('Run on the NAS as root')
    if not Path('/storage/obsidian').is_mount():
        raise ValueError('NAS vault storage is not mounted')
    for directory in [STATE, STATE / 'runtime', STATE / 'repos', STATE / 'evidence', STATE / 'proposals', STATE / 'review']:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    (STATE / 'review').chmod(0o700)
    for directory in ['repos', 'evidence']:
        os.chown(STATE / directory, 1100, 1100)
    for project in ['homelab', 'dealpipe']:
        for collection in ['receipts', 'prs', 'issues', 'milestones']:
            path = STATE / 'evidence' / project / collection
            path.mkdir(parents=True, exist_ok=True)
            os.chown(path, 1100, 1100)
            os.chown(path.parent, 1100, 1100)
    for worker, uid in [('t3', 1100), ('opencode', 1100), ('hermes', 1101)]:
        path = STATE / 'proposals' / worker
        path.mkdir(exist_ok=True)
        os.chown(path, uid, uid)
        path.chmod(0o700)
    desktop = STATE / 'proposals/desktop'
    desktop.mkdir(exist_ok=True)
    os.chown(desktop, Path('/storage/obsidian').stat().st_uid, 100)
    desktop.chmod(0o700)
    brain = Path('/storage/obsidian/Brain')
    for name in ['', 'Projects', 'Projects/homelab', 'Projects/dealpipe', 'Archive', 'Inbox', 'Projects/homelab/Decisions', 'Projects/homelab/Learnings', 'Projects/homelab/Runbooks', 'Projects/dealpipe/Decisions', 'Projects/dealpipe/Learnings', 'Projects/dealpipe/Runbooks', 'Projects/sbms', 'Areas/deal-pipe-ug', 'Personal', 'System', 'Templates']:
        path = brain / name
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, Path('/storage/obsidian').stat().st_uid, 100)
        path.chmod(0o2770)
    shutil.copyfile(HERE.parent / 'policy/handoff.schema.json', STATE / 'runtime/handoff.schema.json')
    (STATE / 'runtime/handoff.schema.json').chmod(0o444)
    for name in ['brain.py', 'source_review.py', 'sources.json']:
        target = STATE / ('runtime/' + name if name.endswith('.py') else name)
        if target.exists():
            backup = target.with_suffix(target.suffix + '.before-knowledge')
            if not backup.exists(): shutil.copy2(target, backup)
        shutil.copyfile(HERE / name, target)
        target.chmod(0o444)
    bindir = STATE.parent / 'bin'
    bindir.mkdir(mode=0o755, exist_ok=True)
    for name, args in [('brain-mcp', 'bridge'), ('brain-admin', '"$@"')]:
        path = bindir / name
        path.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' ' + str(STATE / 'runtime/brain.py') + ' ' + args + '\n')
        path.chmod(0o555)
    for name, executable in [('python', python), ('gh', shutil.which('gh'))]:
        if executable and str(executable).startswith('/nix/store/'):
            root = Path('/nix/var/nix/gcroots') / ('agent-knowledge-' + name)
            if root.is_symlink(): root.unlink()
            root.symlink_to(Path(executable).parents[1])
    # NixOS owns /etc/systemd/system as a read-only generation. The persistent
    # system.control load path permits this narrow rollout without switching
    # an unrelated host generation. The Nix module adopts these units later.
    unit_directory = Path('/etc/systemd/system.control')
    unit_directory.mkdir(parents=True, exist_ok=True)
    (unit_directory / '.agent-knowledge-managed').write_text('homelab shared-brain v1\n')
    for name, content in units(python, STATE / 'runtime').items():
        path = unit_directory / name
        if path.is_symlink():
            raise ValueError('Unit is Nix-owned; use Nix activation to update it')
        if path.exists() and path.read_text() != content:
            backup = path.with_suffix(path.suffix + '.before-knowledge')
            if not backup.exists(): shutil.copy2(path, backup)
        path.write_text(content)
        if name.endswith('.timer') or name == 'agent-knowledge.service':
            wants = unit_directory / ('timers.target.wants' if name.endswith('.timer') else 'multi-user.target.wants')
            wants.mkdir(exist_ok=True)
            link = wants / name
            if not link.is_symlink():
                link.symlink_to(path)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'start', 'agent-knowledge.service', 'agent-knowledge-index.timer', 'agent-knowledge-collect.timer', 'agent-knowledge-refresh.timer', 'agent-knowledge-review.timer'], check=True)
    subprocess.run(['systemctl', 'start', 'agent-knowledge-index.service'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply: install(args.python)
    else: print('\n'.join(units(args.python, STATE / 'runtime')))
