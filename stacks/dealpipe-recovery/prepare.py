#!/usr/bin/env python3
"""Prepare private RAM mounts before Docker starts; refuse disk-backed raw recovery data."""
from pathlib import Path
import subprocess


def main():
    for name,size in [('dealpipe-recovery-quarantine','2g'),('dealpipe-recovery-private','16g')]:
        path=Path('/run')/name;path.mkdir(mode=0o700,exist_ok=True)
        if not path.is_mount():
            subprocess.run(['mount','-t','tmpfs','-o','mode=0700,size='+size+',noswap,nosuid,nodev','tmpfs',str(path)],check=True)
        options=subprocess.check_output(['findmnt','-n','-o','OPTIONS','--mountpoint',str(path)],text=True).strip().split(',')
        if 'noswap' not in options:raise ValueError('Recovery mounts require noswap tmpfs')
    print('Private RAM mounts ready')


if __name__=='__main__':main()
