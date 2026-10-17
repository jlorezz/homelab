#!/usr/bin/env python3
"""Restore encrypted recovery data privately, sanitize, then atomically publish data to testing."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import tempfile
import urllib.request
import urllib.error
import urllib.parse

from backup import private_json,pg_environment,restic,run,record,verify_artifacts,private_ram
from sanitize import sanitize,catalog,validate_policy

HERE=Path(__file__).resolve().parent


def local_dsn(settings, quarantine, database='postgres'):
    from psycopg.conninfo import make_conninfo
    return make_conninfo(host='127.0.0.1',port='18766' if quarantine else '18786',user='supabase_admin',
        password=settings['QUARANTINE_DB_PASSWORD' if quarantine else 'TEST_DB_PASSWORD'],dbname=database)


def placeholder(key):
    # No real source bytes, filenames, EXIF, PDF text or embedded credentials are reused.
    if key.lower().endswith(('.png','.jpg','.jpeg','.webp')):
        import struct
        import zlib
        def chunk(kind,data):
            return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
        data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',16,16,8,2,0,0,0))
        data+=chunk(b'IDAT',zlib.compress((b'\x00'+b'\x88\xaa\xcc'*16)*16))+chunk(b'IEND',b'')
        return data,'image/png'
    if key.lower().endswith('.pdf'):
        text=b'BT /F1 12 Tf 20 40 Td (Synthetic test document) Tj ET'
        chunks=[b'1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n',b'2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n',b'3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n',b'4 0 obj << /Length '+str(len(text)).encode()+b' >> stream\n'+text+b'\nendstream endobj\n',b'5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n']
        data=b'%PDF-1.4\n';offsets=[]
        for chunk in chunks:offsets.append(len(data));data+=chunk
        start=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'
        for offset in offsets:data+=f'{offset:010d} 00000 n \n'.encode()
        data+=b'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'
        return data,'application/pdf'
    return b'Synthetic testing placeholder. No production file content.\n','text/plain'


def refresh(config, credentials, password, fixture=None):
    import psycopg
    from psycopg import sql
    settings=private_json('/var/lib/dealpipe-testing/settings.json')
    policy=private_json(config['policy_file'])
    workspace=Path(config['workspace']) if fixture else private_ram(config)
    workspace.mkdir(mode=0o700,parents=True,exist_ok=True)
    mounts=run(['docker','exec','dealpipe-testing-quarantine-1','cat','/proc/mounts']).decode().splitlines()
    data_mount=next(line.split() for line in mounts if line.split()[1]=='/var/lib/postgresql/data')
    if data_mount[2]!='tmpfs' or 'noswap' not in data_mount[3].split(','):
        raise ValueError('Quarantine must use verified noswap RAM, never persistent storage')
    limits=json.loads(run(['docker','inspect','--format','{{json .HostConfig}}','dealpipe-testing-quarantine-1']))
    if not limits.get('Memory') or limits.get('MemorySwap')!=limits['Memory']:
        raise ValueError('Quarantine swap must be disabled before any raw restore')
    database='quarantine_'+secrets.token_hex(8)
    admin=local_dsn(settings,True)
    with tempfile.TemporaryDirectory(prefix='sanitize-',dir=workspace) as temp:
        root=Path(temp)
        if fixture:
            # Fixture mode accepts only an operator-created, non-production archive for proving the pipeline.
            archive=Path(fixture)
        else:
            restic(config,credentials,password,'restore','latest','--host','nas','--tag','dealpipe-production','--target',str(root))
            verify_artifacts(root,config)
            archive=root/'database.dump'
        with psycopg.connect(admin,autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
        try:
            dsn=local_dsn(settings,True,database)
            toc=run(['pg_restore','--list',str(archive)]).decode()
            schema_names=re.findall(r'^\d+; \d+ \d+ SCHEMA - ([a-z_]+) ',toc,re.M)
            selected=root/'restore.list'
            selected.write_text('\n'.join(line for line in toc.splitlines() if not re.match(r'^\d+; \d+ \d+ SCHEMA - ',line))+'\n')
            metadata=json.loads(Path(str(fixture)+'.extensions.json').read_text()) if fixture else json.loads((root/'manifest.json').read_text())['extensions']
            with psycopg.connect(dsn) as conn:
                for name in schema_names:conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(name)))
                for item in sorted(metadata,key=lambda x:x['name']=='supabase_vault'):
                    if item['schema']!='pg_catalog':conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(item['schema'])))
                    conn.execute(sql.SQL('CREATE EXTENSION IF NOT EXISTS {} WITH SCHEMA {} VERSION {}').format(sql.Identifier(item['name']),sql.Identifier(item['schema']),sql.Literal(item['version'])))
            run(['pg_restore','--no-owner','--no-acl','--exit-on-error','--use-list',str(selected),'--dbname',database,str(archive)],env=pg_environment(dsn))
            with psycopg.connect(dsn) as conn:result=sanitize(conn,policy)
            data=root/'sanitized.sql'
            run(['pg_dump','--data-only','--no-owner','--no-acl','--table','public.*','--table','auth.users','--table','auth.identities','--file',str(data)],env=pg_environment(dsn))
            # Keep the existing migration-built schema and its cross-schema policies.
            # Raw production functions, defaults, grants and roles are never published.
            test=local_dsn(settings,False)
            with psycopg.connect(test) as conn:
                from baseline import verify
                verify(conn)
                validate_policy(catalog(conn),policy)
            script=root/'publish.sql'
            script.write_bytes(b"SET session_replication_role=replica;\n"+
                b"DO $$ DECLARE r record; BEGIN FOR r IN SELECT schemaname,tablename FROM pg_tables WHERE schemaname='public' OR (schemaname='auth' AND tablename<>'schema_migrations') OR (schemaname='storage' AND tablename IN ('objects','buckets')) LOOP EXECUTE format('DELETE FROM %I.%I',r.schemaname,r.tablename); END LOOP; END $$;\n"+
                data.read_bytes()+b'\n'+Path(config['personas_file']).read_bytes()+b"\nSET session_replication_role=origin;\nNOTIFY pgrst,'reload schema';\n")
            run(['psql','-X','--single-transaction','--variable','ON_ERROR_STOP=1','--file',str(script)],env=pg_environment(test))
            # Generate storage objects from synthetic metadata only, through the local Storage API.
            headers={'apikey':settings['TEST_SERVICE_KEY'],'Authorization':'Bearer '+settings['TEST_SERVICE_KEY']}
            api='http://127.0.0.1:18785/storage/v1'
            buckets=sorted({x['bucket'] for x in result['placeholders']})
            for bucket in buckets:
                body=json.dumps({'id':bucket,'name':bucket,'public':bucket=='user-avatars'}).encode()
                req=urllib.request.Request(api+'/bucket',data=body,headers={**headers,'Content-Type':'application/json'},method='POST')
                try:
                    with urllib.request.urlopen(req,timeout=30):pass
                except urllib.error.HTTPError as error:
                    if error.code not in (400,409):raise
                    # Existing bucket is allowed; verify identity rather than ignore arbitrary errors.
                    with urllib.request.urlopen(urllib.request.Request(api+'/bucket/'+bucket,headers=headers),timeout=30) as response:
                        if json.load(response)['id']!=bucket:raise ValueError('Unexpected test bucket')
            for item in result['placeholders']:
                content,mime=placeholder(item['key'])
                route='/object/'+item['bucket']+'/'+urllib.parse.quote(item['key'],safe='/')
                req=urllib.request.Request(api+route,data=content,headers={**headers,'Content-Type':mime,'x-upsert':'true'},method='POST')
                with urllib.request.urlopen(req,timeout=30):pass
            record(config,'sanitize_refresh',tables=result['sanitized_tables'],rows=result['retained_rows'],placeholders=len(result['placeholders']),source='fixture' if fixture else 'production-backup')
            return {'sanitization':'passed','tables':result['sanitized_tables'],'placeholders':len(result['placeholders'])}
        finally:
            with psycopg.connect(admin,autocommit=True) as conn:
                conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=HERE/'config.json')
    parser.add_argument('--fixture',type=Path)
    args=parser.parse_args();config=json.loads(args.config.read_text())
    credentials={} if args.fixture else private_json(Path(config['state_directory'])/'credentials.json')
    try:
        print(json.dumps(refresh(config,credentials,Path(config['state_directory'])/'repository-password',args.fixture)))
        from smoke import main as smoke
        smoke()
        from realtime_smoke import main as realtime_smoke
        realtime_smoke()
    except Exception:
        record(config,'failure',action='sanitize-refresh')
        raise SystemExit('Test refresh failed. Quarantine removed; inspect diagnostics privately. No ready-dataset receipt written.') from None


if __name__=='__main__':main()
