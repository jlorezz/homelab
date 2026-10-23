#!/usr/bin/env python3
"""Provision an empty, dedicated NAS preview backend through Portainer."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import quote
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from deploy import Portainer


def load_recovery():
    spec = importlib.util.spec_from_file_location('preview_recovery_helpers', ROOT / 'stacks/dealpipe-recovery/deploy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', type=int, choices=[1, 2], required=True)
    parser.add_argument('--hostname', default='nas.tailb80645.ts.net')
    args = parser.parse_args()
    if os.geteuid() != 0: raise ValueError('Run on the NAS as operator')
    slot = args.slot
    name = f'nas-preview-{slot}'
    state = Path('/var/lib/agent-platform/previews') / str(slot)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings_path = state / 'settings.json'
    helper = load_recovery()
    if settings_path.exists():
        env = json.loads(settings_path.read_text())
    else:
        secret = secrets.token_urlsafe(48)
        env = {'TEST_DB_PASSWORD': secrets.token_urlsafe(36), 'TEST_JWT_SECRET': secret,
               'TEST_ANON_KEY': helper.jwt(secret, 'anon'), 'TEST_SERVICE_KEY': helper.jwt(secret, 'service_role'),
               'TEST_REALTIME_ENC_KEY': secrets.token_urlsafe(12), 'TEST_REALTIME_SECRET_BASE': secrets.token_urlsafe(64),
               'PREVIEW_REDIS_TOKEN': secrets.token_urlsafe(32), 'PREVIEW_INTERNAL_KEY': secrets.token_hex(32),
               'PREVIEW_ENCRYPTION_KEY': secrets.token_hex(32), 'TEST_AUTH_HOOK_ENABLED': 'true'}
        settings_path.write_text(json.dumps(env)); settings_path.chmod(0o600)
    origin = f'https://{args.hostname}:{4100 + slot}'
    template = yaml.safe_load((ROOT / 'stacks/dealpipe-recovery/compose.yaml').read_text())
    del template['services']['quarantine']
    template['networks'] = {'backend': {'internal': True}}
    for service, definition in template['services'].items():
        definition['networks'] = {'backend': {'aliases': [f'{name}-{service}']}}
        if service == 'realtime': definition['networks']['backend']['aliases'].append('realtime-dev.supabase-realtime')
        definition.pop('ports', None)
        definition['cgroup_parent'] = 'agent-workloads.slice'
        definition['memswap_limit'] = definition['mem_limit']
        definition['labels'] = {'qnoxslab.preview.slot': str(slot)}
        if 'volumes' in definition:
            definition['volumes'] = [v.replace('/storage/dealpipe-testing', str(state)).replace('/var/lib/dealpipe-testing', str(state)) for v in definition['volumes']]
    auth = template['services']['auth']['environment']
    auth.update({'API_EXTERNAL_URL': origin+'/supabase', 'GOTRUE_SITE_URL': origin,
                 'GOTRUE_URI_ALLOW_LIST': origin+'/**', 'GOTRUE_SMTP_SENDER_NAME': 'Dealpipe NAS Preview',
                 'GOTRUE_MAILER_SUBJECTS_MAGIC_LINK': '{{ .Token }} is your sign-in code',
                 'GOTRUE_MAILER_SUBJECTS_CONFIRMATION': '{{ .Token }} is your sign-in code'})
    template['services']['mail']['environment']['MP_WEBROOT'] = '/mail/'
    template['services']['db']['command'] = ['postgres', '-D', '/etc/postgresql', '-c', 'shared_buffers=128MB', '-c', 'max_connections=100']
    for d in ['db', 'objects']: (state/d).mkdir(exist_ok=True, mode=0o700)
    init = state/'init.sql'
    if not init.exists():
        init.write_text('\n'.join(f"ALTER USER {role} WITH PASSWORD '{env['TEST_DB_PASSWORD']}';" for role in ['postgres','supabase_admin','authenticator','supabase_auth_admin','supabase_storage_admin'])+'\nCREATE SCHEMA IF NOT EXISTS _realtime AUTHORIZATION supabase_admin;\n')
        init.chmod(0o444)
    routes = [('auth','http://auth:9999/','/auth/v1/'),('rest','http://rest:3000/','/rest/v1/'),('storage','http://storage:5000/','/storage/v1/'),('realtime-api','http://realtime-dev.supabase-realtime:4000/api','/realtime/v1/api/'),('realtime','http://realtime-dev.supabase-realtime:4000/socket','/realtime/v1/')]
    gateway = {'_format_version':'2.1', 'consumers':[{'username':role,'keyauth_credentials':[{'key':env[key]}]} for role,key in [('anon','TEST_ANON_KEY'),('service','TEST_SERVICE_KEY')]],
               'services':[{'name':n,'url':u,'routes':[{'name':n,'paths':[p],'strip_path':True}], 'plugins':[{'name':'cors'},*([{'name':'key-auth','config':{'hide_credentials':False}}] if n!='auth' else []),*([{'name':'request-transformer','config':{'replace':{'headers':['Host:realtime-dev.supabase-realtime']}}}] if n.startswith('realtime') else [])]} for n,u,p in routes]}
    (state/'gateway.yml').write_text(yaml.safe_dump(gateway)); (state/'gateway.yml').chmod(0o444)
    for service,image,port in [('redis','redis@sha256:57e4255cc971b1c8d6f8a6ca283ac01bc3ecdd914273ef6bfaede87aaecd6541',6379),('redis-http','hiett/serverless-redis-http@sha256:65128347949bca511e448fd7238780d624573d74c22b79155a7563db19e9b678',80)]:
        definition = {'image':image,'restart':'unless-stopped','networks':{'backend':{'aliases':[f'{name}-{service}']}},'security_opt':['no-new-privileges:true'],'cpus':0.25,'mem_limit':'128m','memswap_limit':'128m','cgroup_parent':'agent-workloads.slice','labels':{'qnoxslab.preview.slot':str(slot)}}
        if service=='redis-http': definition['environment']={'SRH_MODE':'env','SRH_TOKEN':'${PREVIEW_REDIS_TOKEN}','SRH_CONNECTION_STRING':'redis://redis:6379'}
        else: definition['command']=['redis-server','--save','','--appendonly','no']
        template['services'][service] = definition
    api = Portainer('https://nas.tailb80645.ts.net:9443','/var/lib/agent-platform/secrets/portainer-token','100.125.180.36')
    images = api.request('GET','/endpoints/2/docker/images/json')
    present = {digest for image in images for digest in (image.get('RepoDigests') or [])}
    for image in sorted({definition['image'] for definition in template['services'].values()}):
        if image in present: continue
        result = api.request('POST','/endpoints/2/docker/images/create?fromImage='+quote(image,safe=''))
        events = result.splitlines() if isinstance(result,str) else [json.dumps(result)]
        if any(json.loads(line).get('error') for line in events if line.strip()):
            raise RuntimeError('Pinned preview image pull failed; inspect Portainer.')
    stacks = [s for s in api.request('GET','/stacks') if s['Name']==name and s['EndpointId']==2]
    payload = {'StackFileContent':yaml.safe_dump(template,sort_keys=False),'Env':[{'name':k,'value':v} for k,v in env.items()]}
    if stacks:
        stack = api.request('PUT',f"/stacks/{stacks[0]['Id']}?endpointId=2",{**payload,'Prune':False,'PullImage':False})
    else:
        stack = api.request('POST','/stacks/create/standalone/string?endpointId=2',{**payload,'Name':name})
    deadline = time.monotonic()+300
    while stack['Status']==3 and time.monotonic()<deadline:
        time.sleep(3);stack=api.request('GET',f"/stacks/{stack['Id']}")
    if stack['Status']!=1: raise RuntimeError('Preview stack did not become active; inspect Portainer')
    # The development migrator manages Auth triggers and Storage RLS as postgres.
    # Grant only these schema-owner roles inside this slot, never host/production roles.
    deadline = time.monotonic() + 300
    while subprocess.run(['docker', 'exec', name+'-db-1', 'pg_isready', '-U', 'supabase_admin'],
                         capture_output=True).returncode:
        if time.monotonic() >= deadline:
            raise RuntimeError('Preview database did not become ready')
        time.sleep(2)
    subprocess.run(['docker', 'exec', name+'-db-1', 'psql', '-U', 'supabase_admin',
                    '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c',
                    'GRANT supabase_auth_admin, supabase_storage_admin TO postgres;'],
                   check=True, stdout=subprocess.DEVNULL)
    cid = next(c['Id'] for c in api.request('GET','/endpoints/2/docker/containers/json') if '/homelab-agents-t3-1' in c['Names'])
    network = name+'_backend'
    info = api.request('GET',f'/endpoints/2/docker/containers/{cid}/json')
    if network not in info['NetworkSettings']['Networks']:
        api.request('POST',f'/endpoints/2/docker/networks/{network}/connect',{'Container':cid})
    config = {'slot':slot,'origin':origin,'port':4100+slot,'env':{
        'DATABASE_URL':f"postgresql://postgres:{env['TEST_DB_PASSWORD']}@{name}-db:5432/postgres",
        'NEXT_PUBLIC_SUPABASE_URL':origin+'/supabase','NEXT_PUBLIC_SUPABASE_ANON_KEY':env['TEST_ANON_KEY'],
        'SUPABASE_PUBLISHABLE_KEY':env['TEST_ANON_KEY'],'SUPABASE_SECRET_KEY':env['TEST_SERVICE_KEY'],
        'SUPABASE_SERVICE_ROLE_KEY':env['TEST_SERVICE_KEY'],'SUPABASE_JWT_SECRET':env['TEST_JWT_SECRET'],
        'INTERNAL_API_KEY':env['PREVIEW_INTERNAL_KEY'],'TOKEN_ENCRYPTION_KEY':env['PREVIEW_ENCRYPTION_KEY'],
        'KV_REST_API_URL':f'http://{name}-redis-http:80','KV_REST_API_TOKEN':env['PREVIEW_REDIS_TOKEN'],
        'BILLING_ENABLED':'false','APOLLO_REPORTING_ENABLED':'false','GRAPHQL_ADHOC_DOCUMENTS':'true',
        'NEXT_PUBLIC_SUPABASE_COOKIE_NAME':f'sb-local-{4100+slot}-auth-token'},
        'proxy':{'port':4100+slot,'origin':origin,'dashboard':f'http://localhost:{4200+slot}',
                 'api':f'http://127.0.0.1:{4300+slot}','supabase':f'http://{name}-gateway:8000','mail':f'http://{name}-mail:8025'}}
    directory=Path('/var/lib/agent-platform/bin/preview/configs')
    directory.mkdir(mode=0o750,exist_ok=True);os.chown(directory,0,1100)
    dest=directory/f'{slot}.json'
    temporary=directory/f'{slot}.json.new'
    temporary.write_text(json.dumps(config));temporary.chmod(0o640);os.chown(temporary,0,1100);temporary.replace(dest)
    print(f'Provisioned private preview slot {slot}: {origin}; dedicated development backend only.')

if __name__=='__main__':main()
