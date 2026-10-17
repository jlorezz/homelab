#!/usr/bin/env python3
"""Escrow the existing production integration-encryption key privately for encrypted recovery."""
import getpass
import json
import os
from pathlib import Path
import re
from backup import write_private


if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('Run as the NAS operator')
    key=getpass.getpass('Existing production TOKEN_ENCRYPTION_KEY (hidden; do not generate a replacement): ').strip()
    if not re.fullmatch('[0-9a-fA-F]{64}',key):raise SystemExit('Expected the existing 64-character hexadecimal encryption key')
    write_private('/var/lib/dealpipe-recovery/operator-recovery/application-key.json',json.dumps({'TOKEN_ENCRYPTION_KEY':key}))
    print('Escrowed privately; included in the next encrypted production backup, excluded from testing')
