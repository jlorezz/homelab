#!/usr/bin/env python3
"""Stable root-only operator entrypoint; credentials are entered only through hidden prompts."""
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
COMMANDS={'prepare-production':('install.py','--prepare-production'),
          'activate-production':('install.py','--activate-production'),'health':('health.py',),
          'client-env':('client_env.py',),'store-key':('store_key.py',),'seal-baseline':('baseline.py',),
          'deploy-testing':('deploy.py','--apply'),'smoke':('smoke.py',),
          'realtime-smoke':('realtime_smoke.py',),'agent-access':('agent_access.py','--apply')}


if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('Run as the NAS operator')
    if len(sys.argv)<2 or sys.argv[1] not in COMMANDS:raise SystemExit('Choose: '+', '.join(COMMANDS))
    script,*arguments=COMMANDS[sys.argv[1]]
    raise SystemExit(subprocess.call([sys.executable,str(HERE/script),*arguments,*sys.argv[2:]]))
