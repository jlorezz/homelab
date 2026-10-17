#!/usr/bin/env python3
"""Prove local email login and tenant isolation on the sanitized NAS stack."""
import html
import json
import re
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

import psycopg
from backup import private_json,record,HERE
from refresh import local_dsn

API='http://127.0.0.1:18785'
MAIL='http://127.0.0.1:18788'


def request(url,headers=None,data=None):
    req=urllib.request.Request(url,headers=headers or {},data=json.dumps(data).encode() if data is not None else None)
    if data is not None:req.add_header('Content-Type','application/json')
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)


def login(settings,email):
    if not email.endswith(('@example.invalid','@testing.example.invalid')):raise ValueError('Smoke login accepts synthetic invalid-domain recipients only')
    headers={'apikey':settings['TEST_ANON_KEY'],'Authorization':'Bearer '+settings['TEST_ANON_KEY']}
    previous={m['ID'] for m in request(MAIL+'/api/v1/messages')['messages']}
    for attempt in range(3):
        try:
            request(API+'/auth/v1/otp',headers,{'email':email,'create_user':False})
            break
        except urllib.error.HTTPError as error:
            if error.code!=429 or attempt==2:raise
            # Consecutive API/Realtime probes can hit the local one-second SMTP limit.
            time.sleep(1.1)
    message=None
    for _ in range(10):
        messages=request(MAIL+'/api/v1/messages')['messages']
        message=next((m for m in messages if m['ID'] not in previous and any(a.get('Address')==email for a in m.get('To',[]))),None)
        if message:break
        time.sleep(1)
    if not message:raise ValueError('No captured local verification email')
    body=request(MAIL+'/api/v1/message/'+message['ID'])
    content=html.unescape(body.get('HTML','')+'\n'+body.get('Text',''))
    links=re.findall(r'http://127\.0\.0\.1:18785/auth/v1/verify[^\s<>"\']+',content)
    if not links:raise ValueError('No local Auth verification link')
    params=urllib.parse.parse_qs(urllib.parse.urlsplit(links[0]).query)
    token=request(API+'/auth/v1/verify',headers,{'token_hash':params['token'][0],'type':params['type'][0]})
    return token['access_token']


def main():
    settings=private_json('/var/lib/dealpipe-testing/settings.json')
    with psycopg.connect(local_dsn(settings,False)) as conn:
        from baseline import verify
        verify(conn)
        users=conn.execute("""select u.id,u.email,array_agg(m.org_id) from public.users u
          join public.org_members m on m.user_id=u.id where u.email in ('tenant-a@testing.example.invalid','tenant-b@testing.example.invalid') group by u.id,u.email order by u.id""").fetchall()
    selected=[]
    for user in users:
        if not selected or set(user[2]).isdisjoint(set(selected[0][2])):selected.append(user)
        if len(selected)==2:break
    if len(selected)!=2:raise ValueError('Two independent fixture tenants required')
    for user_id,email,orgs in selected:
        token=login(settings,email)
        rows=request(API+'/rest/v1/projects?select=id,org_id',{'apikey':settings['TEST_ANON_KEY'],'Authorization':'Bearer '+token})
        if not rows:raise ValueError('Positive own-tenant RLS read failed')
        if not {r['org_id'] for r in rows}<={str(o) for o in orgs}:raise ValueError('Cross-tenant RLS leak')
        try:
            request(API+'/rest/v1/projects?select=name',{'apikey':settings['TEST_ANON_KEY'],'Authorization':'Bearer '+token})
            raise ValueError('Application data column unexpectedly exposed through the Data API')
        except urllib.error.HTTPError as error:
            if error.code not in (401,403):raise
    try:
        anonymous=request(API+'/rest/v1/projects?select=id,org_id',{'apikey':settings['TEST_ANON_KEY'],'Authorization':'Bearer '+settings['TEST_ANON_KEY']})
        if anonymous:raise ValueError('Anonymous tenant data visible')
    except urllib.error.HTTPError as error:
        if error.code not in (401,403):raise
    result={'local_otp_logins':2,'positive_tenant_reads':2,'cross_tenant_isolation':'passed','anonymous_access':'denied','application_data_api':'denied'}
    config=json.loads((HERE/'config.json').read_text())
    status=json.loads((Path(config['state_directory'])/'status.json').read_text())
    record(config,'testing_smoke',sanitized_at=status['sanitize_refresh']['at'],**result)
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Testing smoke failed; inspect only in the private operator context') from None
