#!/usr/bin/env python3
"""Copy the repository password privately to this separate operator machine, preserving bytes."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    trust=Path.home()/'.config/shared-brain/known_hosts'
    ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(trust),'root@100.125.180.36']
    password=subprocess.check_output(ssh+['cat /var/lib/dealpipe-recovery/repository-password'])
    if not password or len(password)>1024:raise ValueError('Unexpected recovery-password file')
    directory=Path.home()/'.local/share/offsite-recovery';directory.mkdir(mode=0o700,parents=True,exist_ok=True);directory.chmod(0o700)
    target=directory/'dealpipe-production-password'
    if target.exists():
        if target.is_symlink() or target.read_bytes()!=password:raise ValueError('A different escrow file exists; do not overwrite it')
    else:
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(password)
    target.chmod(0o600)
    receipt={'sha256':hashlib.sha256(password).hexdigest(),'saved_at':datetime.now(timezone.utc).isoformat(),'independent_copy':'operator development machine; private byte-preserving file'}
    # Input contains only a receipt hash, never the password. No secret in argv or chat.
    remote="umask 077; cat > /var/lib/dealpipe-recovery/recovery-password-copy.json"
    subprocess.run(ssh+[remote],input=json.dumps(receipt).encode(),check=True)
    print('Independent private recovery-password copy verified; import it into your password manager/offline recovery kit')


if __name__=='__main__':main()
