#!/usr/bin/env python3
"""Opt-in sanitized backend access for NAS workers; no raw recovery network or credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

from backup import private_json

HERE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    state=private_json('/var/lib/dealpipe-recovery/status.json')
    if any(state[event]['sanitized_at']!=state['sanitize_refresh']['at'] for event in ['testing_smoke','testing_realtime']):
        raise ValueError('A verified sanitized dataset is required before connecting workers')
    if not args.apply:print('Plan: attach T3/Hermes only to testing network and write private opt-in backend env files');return
    sys.path.insert(0,'/etc/nixos/homelab/stacks/agents')
    from deploy import Portainer
    api=Portainer('https://nas.tailb80645.ts.net:9443','/var/lib/agent-platform/secrets/portainer-token','100.125.180.36')
    settings=private_json('/var/lib/dealpipe-testing/settings.json')
    network=next(n for n in api.request('GET','/endpoints/2/docker/networks') if n['Name']=='dealpipe-testing_testing')
    containers=api.request('GET','/endpoints/2/docker/containers/json')
    for role,uid in [('t3',1100),('hermes',1101)]:
        container=next(c for c in containers if '/homelab-agents-'+role+'-1' in c['Names'])
        info=api.request('GET','/endpoints/2/docker/containers/'+container['Id']+'/json')
        if network['Name'] not in info['NetworkSettings']['Networks']:
            api.request('POST','/endpoints/2/docker/networks/'+network['Id']+'/connect',{'Container':container['Id']})
        workspace=Path(next(m['Source'] for m in info['Mounts'] if m['Destination']=='/workspace'))
        destination=workspace/'.dealpipe-testing.env'
        values={'DATABASE_URL':'postgresql://postgres:'+quote(settings['TEST_DB_PASSWORD'],safe='')+'@dealpipe-testing-db:5432/postgres',
                'NEXT_PUBLIC_SUPABASE_URL':'http://dealpipe-testing-api:8000','NEXT_PUBLIC_SUPABASE_ANON_KEY':settings['TEST_ANON_KEY'],
                'SUPABASE_SECRET_KEY':settings['TEST_SERVICE_KEY'],'SUPABASE_SERVICE_ROLE_KEY':settings['TEST_SERVICE_KEY'],
                'SUPABASE_JWT_SECRET':settings['TEST_JWT_SECRET'],'BILLING_ENABLED':'false'}
        values['DIRECT_DATABASE_URL']=values['DATABASE_URL']
        content='# Opt-in SANITIZED testing backend only. Read the shared Brain recovery guide first.\n'+'\n'.join(k+'='+json.dumps(v) for k,v in values.items())+'\n'
        if destination.exists():
            if destination.is_symlink() or destination.read_text()!=content:raise ValueError('Existing worker env differs; rotate deliberately')
        else:
            fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:stream.write(content)
        os.chown(destination,uid,uid);destination.chmod(0o600)
        print('Prepared opt-in sanitized backend access for '+role)


if __name__=='__main__':main()
