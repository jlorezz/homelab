#!/usr/bin/env python3
"""Activate only offsite units on a running NAS; a later Nix activation adopts them."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
STATE = Path('/var/lib/offsite-backup')
CONTROL = Path('/etc/systemd/system.control')
SCHEDULES = {'copy': '*-*-* 05:30:00', 'check': 'Sun *-*-* 07:00:00', 'restore-test': '*-*-01 08:00:00'}


def units(python, restic):
    result = {}
    for action, calendar in SCHEDULES.items():
        name = 'offsite-' + action
        result[name + '.service'] = f'''[Unit]
Description=Homelab offsite Restic {action}
Wants=network-online.target
After=network-online.target restic-backups-nas.service restic-backups-agents.service
RequiresMountsFor=/storage/backups
[Service]
Type=oneshot
Environment=PATH={Path(restic).parent}:/run/current-system/sw/bin
EnvironmentFile={STATE}/secrets/homelab.env
ExecStart={python} /var/lib/agent-platform/bin/workload.py --lock /var/lib/agent-workloads/heavy.lock run --timeout 7200 -- {python} {HERE}/offsite.py {action} --config {STATE}/config.json
StateDirectory=offsite-backup
StateDirectoryMode=0700
UMask=0077
Nice=15
IOSchedulingClass=idle
CPUQuota=25%
MemoryMax=1G
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={STATE} /storage/backups/nas-restic /var/lib/agent-workloads/heavy.lock
TimeoutStartSec=3h
'''
        result[name + '.timer'] = f'''[Unit]
Description=Schedule Homelab offsite {action}
[Timer]
OnCalendar={calendar}
Persistent=true
RandomizedDelaySec={'15m' if action == 'copy' else '0'}
[Install]
WantedBy=timers.target
'''
    return result


def install(python, restic):
    if os.geteuid() != 0 or not Path('/storage/obsidian').is_mount():
        raise ValueError('Run as root on the NAS with its vault mounted')
    sys.path.insert(0, str(HERE))
    from offsite import Backup, private_file
    private_file(STATE / 'secrets/homelab.env')
    backup = Backup(json.loads((STATE / 'config.json').read_text()))
    backup.execute('health')  # Copy, integrity check and restore must already pass.
    rendered = units(python, restic)
    # Validate before changing any running unit. No cloud credentials are printed.
    with tempfile.TemporaryDirectory(prefix='offsite-units-') as temp:
        files = []
        for name, content in rendered.items():
            p = Path(temp) / name; p.write_text(content); files.append(str(p))
        subprocess.run(['systemd-analyze', 'verify', *files], check=True)
    marker = CONTROL / '.offsite-backup-managed'
    health = CONTROL / 'nas-health-check.service.d/50-offsite.conf'
    for path in [*(CONTROL / name for name in rendered), health]:
        if path.exists() and not marker.exists():
            raise ValueError('Refusing to overwrite an unmanaged unit or drop-in')
    CONTROL.mkdir(parents=True, exist_ok=True)
    for name, executable in [('python', python), ('restic', restic)]:
        executable = Path(executable).resolve()
        if str(executable).startswith('/nix/store/'):
            root = Path('/nix/var/nix/gcroots') / ('offsite-backup-' + name)
            if root.is_symlink(): root.unlink()
            root.symlink_to(executable.parents[1])
    marker.write_text('homelab offsite v1\n')
    for name, content in rendered.items():
        target = CONTROL / name
        target.write_text(content); target.chmod(0o644)
        if name.endswith('.timer'):
            wants = CONTROL / 'timers.target.wants'; wants.mkdir(exist_ok=True)
            link = wants / name
            if not link.is_symlink(): link.symlink_to(target)
    health.parent.mkdir(exist_ok=True)
    health.write_text(f'''[Service]
Environment=OFFSITE_BACKUP_STATUS={STATE}/state/status.json
ExecStart=
ExecStart={python} {HERE.parents[1]}/machines/nas/scripts/check-nas-health.py
''')
    health.chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'start', 'nas-health-check.service'], check=True)
    subprocess.run(['systemctl', 'start', *('offsite-' + a + '.timer' for a in SCHEDULES)], check=True)
    print(json.dumps({'timers': 'active', 'nas_health': 'passed'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--restic', default=shutil.which('restic'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.restic: parser.error('Restic binary required')
    if args.apply: install(args.python, args.restic)
    else: print('\n'.join(units(args.python, args.restic)))
