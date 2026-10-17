#!/usr/bin/env python3
"""Hidden NAS terminal entry. Provision a read-only exporter; never retain the admin URL."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import urllib.error
import urllib.request

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo

from backup import HERE,private_json,write_private,source_connection


def validate_b2(auth, config):
    """Pin the bucket ID; B2 may omit names for restricted keys."""
    storage_api=auth.get('apiInfo',{}).get('storageApi',{})
    scope=storage_api.get('allowed',auth.get('allowed'))
    if not isinstance(scope,dict):
        raise SystemExit('Backblaze returned no recognizable key restrictions; no database changes were made.')
    buckets=scope.get('buckets')
    if buckets is None and scope.get('bucketId'):
        buckets=[{'id':scope['bucketId'],'name':scope.get('bucketName')}]
    if not isinstance(buckets,list) or len(buckets)!=1 or buckets[0].get('id')!=config['b2_bucket_id']:
        raise SystemExit('B2 key is not restricted to the production bucket ID shown in your bucket screenshot. Select qnoxslab-dealpipe-production-backups only.')
    if scope.get('namePrefix')!='production/':raise SystemExit('Restrict the B2 key to production/ prefix')
    if not {'listFiles','readFiles','writeFiles'}<=set(scope.get('capabilities',[])):
        raise SystemExit('B2 export/restore needs listing, reading and writing within its production prefix')


@contextmanager
def stage(state, name):
    """Persist allowlisted diagnostics only; provider/SQL exception text can contain secrets."""
    try:
        yield
    except Exception as error:
        details={'stage':name,'at':datetime.now(timezone.utc).isoformat()}
        hint='Unexpected installer error; the operator can inspect the safe stage receipt.'
        if isinstance(error,urllib.error.HTTPError):
            details['http_status']=error.code
            hint='Backblaze rejected authentication; check the key ID and application key.' if error.code==401 else 'Backblaze request failed; check its HTTP status.'
            error.close()
        elif isinstance(error,psycopg.Error):
            code=error.sqlstate
            if code and len(code)==5 and code.isalnum():details['sqlstate']=code
            hint={'28P01':'The database rejected the username/password.',
                  '42501':'The database role lacks permission for this provisioning step.',
                  '42710':'The backup reader already exists; do not recreate or overwrite it.'}.get(code,'Database connection or operation failed; check the session URL, TLS and network access.')
        elif isinstance(error,(urllib.error.URLError,TimeoutError,OSError)):
            hint='Network, TLS or local file access failed at this step.'
        state.mkdir(mode=0o700,parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',dir=state,delete=False) as stream:
            json.dump(details,stream);temporary=Path(stream.name)
        temporary.chmod(0o600);temporary.replace(state/'provision-status.json')
        suffix=''.join(' '+key+'='+str(details[key]) for key in ['http_status','sqlstate'] if key in details)
        raise SystemExit('Provisioning failed at '+name+'.'+suffix+' '+hint+' No credential values were logged.') from None


def main():
    if os.geteuid()!=0:raise SystemExit('Run in an interactive NAS root terminal')
    config=json.loads((HERE/'config.json').read_text())
    state=Path(config['state_directory']);state.mkdir(mode=0o700,parents=True,exist_ok=True);state.chmod(0o700)
    if (state/'credentials.json').exists():raise SystemExit('Already provisioned. Rotate deliberately; do not overwrite.')
    # Prompt only inside the user's SSH terminal. No values enter the chat or command arguments.
    admin=getpass.getpass('Production database direct/session URL (hidden; used once to create reader): ').strip()
    values=conninfo_to_dict(admin)
    ref=config['project_ref'];host=values.get('host','');user=values.get('user','')
    if values.get('port','5432')!='5432' or not(host=='db.'+ref+'.supabase.co' or(host.endswith('.pooler.supabase.com') and user.endswith('.'+ref))):
        raise SystemExit('Use this production project and direct/session port 5432')
    values['sslmode']='verify-full';values.setdefault('sslrootcert','system')
    storage=getpass.getpass('Production Storage server credential (hidden; download only): ').strip()
    key_id=getpass.getpass('Separate production B2 key ID (hidden): ').strip()
    key=getpass.getpass('Separate production B2 application key (hidden): ').strip()
    request=urllib.request.Request('https://api.backblazeb2.com/b2api/v4/b2_authorize_account',headers={'Authorization':'Basic '+base64.b64encode((key_id+':'+key).encode()).decode()})
    with stage(state,'Backblaze authentication'):
        with urllib.request.urlopen(request,timeout=30) as response:auth=json.load(response)
    with stage(state,'Backblaze bucket restriction validation'):
        validate_b2(auth,config)
    reader='dealpipe_backup_reader';password=secrets.token_urlsafe(48)
    with stage(state,'production database connection'):
        connection=psycopg.connect(make_conninfo(**values),connect_timeout=15)
    with connection:
        with stage(state,'backup reader creation'):
            if connection.execute('select 1 from pg_roles where rolname=%s',(reader,)).fetchone():
                raise SystemExit('Backup reader already exists; stop and recover its saved credential rather than replacing it.')
            # Send a SCRAM verifier rather than plaintext in the role-creation SQL.
            verifier=connection.pgconn.encrypt_password(password.encode(),reader.encode(),b'scram-sha-256').decode()
            connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(reader),sql.Literal(verifier)))
            connection.execute(sql.SQL('GRANT pg_read_all_data TO {}').format(sql.Identifier(reader)))
            connection.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(reader)))
    values['user']=reader+'.'+ref if host.endswith('.pooler.supabase.com') else reader
    values['password']=password
    credentials={'database_url':make_conninfo(**values),'storage_read_key':storage,'b2_key_id':key_id,'b2_application_key':key}
    # Retain recovery access even if the first authentication attempt needs pooler propagation.
    with stage(state,'private credential installation'):
        write_private(state/'credentials.json',json.dumps(credentials))
        write_private(state/'repository-password',secrets.token_urlsafe(48)+'\n')
    with stage(state,'backup reader login verification'):
        with source_connection(credentials,config):pass
    print('Reader and private credentials installed. Admin connection discarded. Save repository-password outside NAS before initialization.')


if __name__=='__main__':
    if sys.argv[1:]==['--version']:
        print('dealpipe-recovery-provision v3 (pinned bucket ID; safe stage diagnostics)')
    else:
        with stage(Path('/var/lib/dealpipe-recovery'),'installer setup'):
            main()
