#!/usr/bin/env python3
"""Hidden NAS terminal entry. Provision a read-only exporter; never retain the admin URL."""
import base64
import getpass
import json
import os
from pathlib import Path
import secrets
import urllib.request

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo

from backup import HERE,private_json,write_private,source_connection


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
    with urllib.request.urlopen(request,timeout=30) as response:auth=json.load(response)
    scope=auth['apiInfo']['storageApi']['allowed']
    if [b.get('name') for b in (scope.get('buckets') or [])]!=['qnoxslab-dealpipe-production-backups']:
        raise SystemExit('B2 credential must be restricted to the separate production bucket')
    if scope.get('namePrefix')!='production/':raise SystemExit('Restrict the B2 key to production/ prefix')
    if not {'listFiles','readFiles','writeFiles'}<=set(scope.get('capabilities',[])):
        raise SystemExit('B2 export/restore needs listing, reading and writing within its production prefix')
    reader='dealpipe_backup_reader';password=secrets.token_urlsafe(48)
    with psycopg.connect(make_conninfo(**values),connect_timeout=15) as connection:
        if connection.execute('select 1 from pg_roles where rolname=%s',(reader,)).fetchone():raise ValueError('Reader exists; supply its existing credential with install-credentials instead')
        # Send a SCRAM verifier rather than plaintext in the role-creation SQL.
        verifier=connection.pgconn.encrypt_password(password.encode(),reader.encode(),b'scram-sha-256').decode()
        connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(reader),sql.Literal(verifier)))
        connection.execute(sql.SQL('GRANT pg_read_all_data TO {}').format(sql.Identifier(reader)))
        connection.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(reader)))
    values['user']=reader+'.'+ref if host.endswith('.pooler.supabase.com') else reader
    values['password']=password
    credentials={'database_url':make_conninfo(**values),'storage_read_key':storage,'b2_key_id':key_id,'b2_application_key':key}
    # Retain recovery access even if the first authentication attempt needs pooler propagation.
    write_private(state/'credentials.json',json.dumps(credentials))
    write_private(state/'repository-password',secrets.token_urlsafe(48)+'\n')
    with source_connection(credentials,config):pass
    print('Reader and private credentials installed. Admin connection discarded. Save repository-password outside NAS before initialization.')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Provisioning failed. Inspect NAS privately; never paste credentials or raw diagnostics.') from None
