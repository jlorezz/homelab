#!/usr/bin/env python3
"""Narrow, marked systemd rollout. Production timers require verified cloud recovery receipts."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
CONTROL=Path('/etc/systemd/system.control')
STATE=Path('/var/lib/dealpipe-recovery')
SCHEDULES={'backup':'*-*-* 02:00:00','check':'Sun *-*-* 04:30:00','restore-verify':'*-*-01 06:00:00','refresh':'Sun *-*-* 06:30:00','retention':'Mon *-*-* 09:00:00'}


def units(python,production):
    return {path.name:path.read_text().replace('@python@',str(python)).replace('@source@',str(HERE)).replace('@failure@',str(HERE.parent/'backups/failure.py'))
            for path in (HERE/'units').iterdir()
            if production or path.name.startswith('dealpipe-testing-')}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--activate-production',action='store_true');parser.add_argument('--prepare-production',action='store_true');args=parser.parse_args()
    if os.geteuid()!=0:raise ValueError('Run as the NAS operator')
    if args.activate_production:
        from health import check
        check(STATE/'status.json')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True);STATE.chmod(0o700)
    Path('/storage/backups/dealpipe-encrypted-object-cache').mkdir(mode=0o700,exist_ok=True)
    rendered=units(sys.executable,args.activate_production or args.prepare_production)
    with tempfile.TemporaryDirectory() as temp:
        paths=[]
        for name,content in rendered.items():
            path=Path(temp)/name;path.write_text(content);paths.append(str(path))
        subprocess.run(['systemd-analyze','verify',*paths],check=True)
    marker=CONTROL/'.dealpipe-recovery-managed'
    for name in rendered:
        if (CONTROL/name).exists() and not marker.exists() and name!='dealpipe-testing-proxy.service':raise ValueError('Unmanaged unit exists')
    CONTROL.mkdir(exist_ok=True);marker.write_text('homelab dealpipe recovery v1\n')
    for name,content in rendered.items():
        path=CONTROL/name;path.write_text(content);path.chmod(0o644)
        if (name.endswith('.timer') and args.activate_production) or name.startswith('dealpipe-testing-'):
            wants=CONTROL/('timers.target.wants' if name.endswith('.timer') else 'multi-user.target.wants');wants.mkdir(exist_ok=True)
            link=wants/name
            if not link.is_symlink():link.symlink_to(path)
    bindir=STATE/'bin';bindir.mkdir(mode=0o700,exist_ok=True)
    for name,script in [('provision','provision.py'),('recovery','backup.py'),('operator','operations.py')]:
        wrapper=bindir/name
        temporary=bindir/(name+'.new')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o700)
        with os.fdopen(fd,'w') as stream:
            stream.write(f'#!/bin/sh\nexec "{sys.executable}" "{HERE/script}" "$@"\n')
        temporary.replace(wrapper)
    binary=Path(sys.executable).resolve()
    if str(binary).startswith('/nix/store/'):
        root=Path('/nix/var/nix/gcroots/dealpipe-recovery-python')
        if root.is_symlink():root.unlink()
        root.symlink_to(binary.parents[1])
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','start','dealpipe-testing-ram.service','dealpipe-testing-targets.service'],check=True)
    subprocess.run(['systemctl','restart','dealpipe-testing-proxy.service'],check=True)
    if args.activate_production:
        # These actions just passed manually. Seed persistent timer stamps from
        # their real receipts to avoid duplicate catch-up runs on first enable.
        receipts=json.loads((STATE/'status.json').read_text())
        directory=Path('/var/lib/systemd/timers');directory.mkdir(mode=0o755,parents=True,exist_ok=True)
        for action,event in [('backup','backup'),('check','check'),('restore-verify','restore_verify'),('refresh','sanitize_refresh')]:
            timestamp=datetime.fromisoformat(receipts[event]['at']).timestamp()
            stamp=directory/('stamp-dealpipe-production-'+action+'.timer')
            stamp.touch();os.utime(stamp,(timestamp,timestamp))
        dropin=CONTROL/'nas-health-check.service.d/60-dealpipe.conf';dropin.parent.mkdir(exist_ok=True)
        dropin.write_text('[Service]\nEnvironment=DEALPIPE_RECOVERY_STATUS=/var/lib/dealpipe-recovery/status.json\n')
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','start','nas-health-check.service'],check=True)
        subprocess.run(['systemctl','start',*('dealpipe-production-'+a+'.timer' for a in SCHEDULES)],check=True)
    print(json.dumps({'testing_runtime':'active','production_timers':args.activate_production}))


if __name__=='__main__':main()
