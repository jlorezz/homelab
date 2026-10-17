#!/usr/bin/env python3
"""Deploy an isolated testing and quarantine stack through the operator's Portainer API."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import subprocess
import time
from urllib.parse import urlencode

import yaml

HERE=Path(__file__).resolve().parent
STATE=Path('/var/lib/dealpipe-testing')


def jwt(secret,role):
    def enc(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
    header=enc(json.dumps({'alg':'HS256','typ':'JWT'},separators=(',',':')).encode())
    # Local fixture keys, rotated when the isolated stack is reprovisioned.
    payload=enc(json.dumps({'role':role,'iss':'supabase-demo','iat':int(time.time()),'exp':int(time.time())+365*86400},separators=(',',':')).encode())
    unsigned=header+'.'+payload
    return unsigned+'.'+enc(hmac.new(secret.encode(),unsigned.encode(),hashlib.sha256).digest())


def settings():
    from backup import private_json,write_private
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True);STATE.chmod(0o700)
    path=STATE/'settings.json'
    if path.exists():return private_json(path)
    secret=secrets.token_urlsafe(48)
    env={'TEST_DB_PASSWORD':secrets.token_urlsafe(36),'QUARANTINE_DB_PASSWORD':secrets.token_urlsafe(36),
         'TEST_JWT_SECRET':secret,'TEST_ANON_KEY':jwt(secret,'anon'),'TEST_SERVICE_KEY':jwt(secret,'service_role')}
    write_private(path,json.dumps(env));return env


def update_proxy(api,endpoint):
    proxy={}
    for service,listen,target in [('quarantine',18766,5432),('db',18786,5432),('gateway',18785,8000),('mail',18788,8025)]:
        containers=api.request('GET',f'/endpoints/{endpoint}/docker/containers/json?all=1')
        container=next(c for c in containers if '/dealpipe-testing-'+service+'-1' in c['Names'])
        info=api.request('GET',f'/endpoints/{endpoint}/docker/containers/'+container['Id']+'/json')
        networks=info['NetworkSettings']['Networks']
        address=next(iter(networks.values()))['IPAddress']
        proxy[str(listen)]={'host':address,'port':target}
    directory=Path('/etc/dealpipe-testing');directory.mkdir(mode=0o755,exist_ok=True)
    target=directory/'proxy.json';temporary=directory/'proxy.json.new'
    temporary.write_text(json.dumps(proxy));temporary.chmod(0o644);temporary.replace(target)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');parser.add_argument('--refresh-proxy-only',action='store_true');args=parser.parse_args()
    compose=yaml.safe_load((HERE/'compose.yaml').read_text())
    if args.refresh_proxy_only:
        sys.path.insert(0,os.environ.get('AGENT_OPERATOR_LIBRARY','/etc/nixos/homelab/stacks/agents'))
        from deploy import Portainer
        api=Portainer('https://nas.tailb80645.ts.net:9443','/var/lib/agent-platform/secrets/portainer-token','100.125.180.36')
        update_proxy(api,2);return
    if not args.apply:print('Validated compose; --apply deploys only dealpipe-testing');return
    if os.geteuid()!=0:raise ValueError('Run as the NAS operator')
    sys.path.insert(0,os.environ.get('AGENT_OPERATOR_LIBRARY','/etc/nixos/homelab/stacks/agents'))
    from deploy import Portainer
    env=settings()
    if 'TEST_REALTIME_ENC_KEY' not in env:
        env['TEST_REALTIME_ENC_KEY']=secrets.token_urlsafe(12)
        env['TEST_REALTIME_SECRET_BASE']=secrets.token_urlsafe(64)
        temporary=STATE/'settings.json.new';temporary.write_text(json.dumps(env));temporary.chmod(0o600);temporary.replace(STATE/'settings.json')
    if (STATE/'schema-baseline.json').exists():
        import psycopg
        with psycopg.connect(host='127.0.0.1',port=18786,user='supabase_admin',password=env['TEST_DB_PASSWORD'],dbname='postgres') as connection:
            connection.execute('CREATE SCHEMA IF NOT EXISTS _realtime AUTHORIZATION supabase_admin')
    env['TEST_AUTH_HOOK_ENABLED']='true' if (STATE/'schema-baseline.json').exists() else 'false'
    raw=Path('/run/dealpipe-recovery-quarantine');raw.mkdir(mode=0o700,exist_ok=True)
    if not raw.is_mount():
        subprocess.run(['mount','-t','tmpfs','-o','mode=0700,size=2g,noswap,nosuid,nodev','tmpfs',str(raw)],check=True)
    options=subprocess.check_output(['findmnt','-n','-o','OPTIONS','--mountpoint',str(raw)],text=True)
    if 'noswap' not in options.strip().split(','):raise ValueError('Quarantine must use a noswap tmpfs')
    for name in ['db','objects']:
        path=Path('/storage/dealpipe-testing')/name;path.mkdir(parents=True,exist_ok=True,mode=0o700)
    init=STATE/'init.sql'
    from backup import write_private
    if not init.exists():
        # Generated URL-safe fixture password contains no SQL quoting characters.
        write_private(init,'\n'.join('ALTER USER '+role+" WITH PASSWORD '"+env['TEST_DB_PASSWORD']+"';" for role in ['postgres','supabase_admin','authenticator','supabase_auth_admin','supabase_storage_admin']))
        # Image startup runs as postgres. The containing directory stays root-only;
        # Docker's explicit read-only file mount grants only this bootstrap file.
        init.chmod(0o444)
    quarantine_init=STATE/'quarantine-init.sql'
    if not quarantine_init.exists():
        write_private(quarantine_init,'\n'.join('ALTER USER '+role+" WITH PASSWORD '"+env['QUARANTINE_DB_PASSWORD']+"';" for role in ['postgres','supabase_admin']))
        quarantine_init.chmod(0o444)
    kong={'_format_version':'2.1','consumers':[{'username':'anon','keyauth_credentials':[{'key':env['TEST_ANON_KEY']}]},{'username':'service','keyauth_credentials':[{'key':env['TEST_SERVICE_KEY']}]}],
      'services':[{'name':name,'url':url,'routes':[{'name':name,'strip_path':True,'paths':[path]}],
                   'plugins':[{'name':'cors'},*([{'name':'key-auth','config':{'hide_credentials':False}}] if name!='auth' else [])]}
        for name,url,path in [('auth','http://auth:9999/','/auth/v1/'),('rest','http://rest:3000/','/rest/v1/'),('storage','http://storage:5000/','/storage/v1/')]]}
    kong['services'] += [
        {'name':'realtime-ws','url':'http://realtime-dev.supabase-realtime:4000/socket','routes':[{'name':'realtime-ws','strip_path':True,'paths':['/realtime/v1/']}],
         'plugins':[{'name':'cors'},{'name':'key-auth','config':{'hide_credentials':False}},
                    {'name':'request-transformer','config':{'replace':{'headers':['Host:realtime-dev.supabase-realtime']}}}]},
        {'name':'realtime-rest','url':'http://realtime-dev.supabase-realtime:4000/api','routes':[{'name':'realtime-rest','strip_path':True,'paths':['/realtime/v1/api/']}],
         'plugins':[{'name':'cors'},{'name':'key-auth','config':{'hide_credentials':False}}]}
    ]
    gateway=STATE/'gateway.yml'
    content=yaml.safe_dump(kong,sort_keys=False)
    gateway_changed=not gateway.exists() or gateway.read_text()!=content
    gateway.write_text(content);gateway.chmod(0o444)
    api=Portainer('https://nas.tailb80645.ts.net:9443','/var/lib/agent-platform/secrets/portainer-token','100.125.180.36')
    endpoint=2
    images=api.request('GET',f'/endpoints/{endpoint}/docker/images/json')
    digests={d for image in images for d in (image.get('RepoDigests') or [])}
    for image in sorted({service['image'] for service in compose['services'].values()}):
        if image not in digests:
            print('Pulling pinned test-stack image: '+image.split('@')[0],flush=True)
            pulled=api.request('POST',f'/endpoints/{endpoint}/docker/images/create?'+urlencode({'fromImage':image}))
            events=pulled.splitlines() if isinstance(pulled,str) else [json.dumps(pulled)]
            if any(json.loads(line).get('error') for line in events if line.strip()):raise RuntimeError('Pinned image pull failed')
    existing=[s for s in api.request('GET','/stacks') if s['Name']=='dealpipe-testing' and s['EndpointId']==endpoint]
    payload={'StackFileContent':yaml.safe_dump(compose,sort_keys=False),'Env':[{'name':k,'value':v} for k,v in env.items()]}
    if existing:
        api.request('PUT',f'/stacks/{existing[0]["Id"]}?endpointId={endpoint}',{**payload,'Prune':False,'PullImage':False})
    else:
        api.request('POST',f'/stacks/create/standalone/string?endpointId={endpoint}',{**payload,'Name':'dealpipe-testing'})
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        stack=next(s for s in api.request('GET','/stacks') if s['Name']=='dealpipe-testing' and s['EndpointId']==endpoint)
        if stack['Status']==1:
            update_proxy(api,endpoint)
            if gateway_changed:
                container=next(c for c in api.request('GET',f'/endpoints/{endpoint}/docker/containers/json') if '/dealpipe-testing-gateway-1' in c['Names'])
                api.request('POST',f'/endpoints/{endpoint}/docker/containers/'+container['Id']+'/restart')
            print('Isolated stack deployed; private proxy targets recorded');return
        if stack['Status']==4:raise RuntimeError('Portainer deployment failed; inspect redacted deployment status')
        time.sleep(5)
    raise RuntimeError('Portainer deployment did not finish within the operator deadline')


if __name__=='__main__':main()
