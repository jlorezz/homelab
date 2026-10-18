#!/usr/bin/env python3
"""Operator-only consistent Supabase export plus Storage bytes, encrypted by Restic."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent


def file_hash(path):
    with open(path,'rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def private_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_uid != os.geteuid():
        raise ValueError('Expected an operator-owned private regular credential file')
    return json.loads(path.read_text())


def write_private(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f: f.write(data)


class RecoveryCommandError(RuntimeError):
    def __init__(self,stderr):
        super().__init__('Recovery command failed; inspect only in the private operator context')
        self.private_stderr=stderr


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=7200, **kwargs)
    if result.returncode:
        # Provider errors, SQL errors and pg_dump diagnostics may contain private data.
        raise RecoveryCommandError(result.stderr)
    return result.stdout


def pg_environment(dsn):
    from psycopg.conninfo import conninfo_to_dict
    fields = conninfo_to_dict(dsn)
    allowed = {'host':'PGHOST','port':'PGPORT','user':'PGUSER','password':'PGPASSWORD','dbname':'PGDATABASE',
               'sslmode':'PGSSLMODE','sslrootcert':'PGSSLROOTCERT'}
    if set(fields) - set(allowed): raise ValueError('Unsupported connection options')
    return {**os.environ, **{allowed[k]:v for k,v in fields.items()}}


def source_connection(credentials, config):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    dsn = credentials['database_url']
    fields = conninfo_to_dict(dsn)
    ref = config['project_ref']
    if fields.get('port','5432') != '5432': raise ValueError('Use direct/session port 5432, never transaction pooling')
    host = fields.get('host','')
    if not (host == 'db.'+ref+'.supabase.co' or
            (host.endswith('.pooler.supabase.com') and fields.get('user','').endswith('.'+ref))):
        raise ValueError('Unexpected production project connection')
    if fields.get('sslmode') != 'verify-full' or not fields.get('sslrootcert'):
        raise ValueError('Require verify-full TLS and the trusted Supabase CA or system CA')
    connection = psycopg.connect(dsn, connect_timeout=15)
    role = connection.execute('select rolname,rolbypassrls,rolsuper from pg_roles where rolname=current_user').fetchone()
    if not role[0].startswith('dealpipe_backup_reader') or not role[1] or role[2]:
        connection.close(); raise ValueError('Use the dedicated non-superuser BYPASSRLS backup reader')
    connection.commit()
    return connection


class Storage:
    def __init__(self, url, token):
        self.url = url.rstrip('/')
        self.headers = {'apikey':token, 'Authorization':'Bearer '+token}

    def download(self, bucket, key, path):
        # No redirects: credentials must never follow an arbitrary redirect host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        route = '/object/authenticated/' + urllib.parse.quote(bucket,safe='') + '/' + urllib.parse.quote(key,safe='/')
        request = urllib.request.Request(self.url+route, headers=self.headers)
        digest=hashlib.sha256();size=0
        with urllib.request.build_opener(NoRedirect).open(request,timeout=120) as response, open(path,'xb') as output:
            os.chmod(path,0o600)
            while block:=response.read(1024*1024):
                output.write(block);digest.update(block);size+=len(block)
        return {'bytes':size,'sha256':digest.hexdigest()}


def restic(config, credentials, password, *args):
    env={**os.environ,'AWS_ACCESS_KEY_ID':credentials['b2_key_id'],'AWS_SECRET_ACCESS_KEY':credentials['b2_application_key']}
    return run(['restic','--no-cache','--repo',config['repository'],'--password-file',str(password),*args],env=env)


def record(config, event, **fields):
    path=Path(config['state_directory'])/'status.json'
    state=json.loads(path.read_text()) if path.exists() else {}
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    state[event]={'at':datetime.now(timezone.utc).isoformat(),**fields}
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        json.dump(state,f,indent=2);temp=Path(f.name)
    temp.chmod(0o600);temp.replace(path)


def private_ram(config):
    workspace=Path(config['workspace'])
    workspace.mkdir(parents=True,exist_ok=True,mode=0o700);workspace.chmod(0o700)
    mount=json.loads(run(['findmnt','--json','--target',str(workspace),'--output','FSTYPE,OPTIONS']).decode())['filesystems'][0]
    if mount['fstype']!='tmpfs' or 'noswap' not in mount['options'].split(','):
        raise ValueError('Raw recovery work requires a private noswap tmpfs')
    group=next(line.split(':',2)[2].strip() for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    if (Path('/sys/fs/cgroup')/group.lstrip('/')/'memory.swap.max').read_text().strip()!='0':
        raise ValueError('Run raw-data jobs through the reviewed systemd units with MemorySwapMax=0')
    return workspace


def backup(config, credentials, password):
    import psycopg
    from psycopg import sql
    workspace=private_ram(config)
    from cache import Cache
    with Cache(config.get('cache_directory','/storage/backups/dealpipe-encrypted-object-cache'),Path(config['state_directory'])/'cache-key') as cache, tempfile.TemporaryDirectory(prefix='export-',dir=workspace) as temp:
        root=Path(temp); objects=root/'objects';objects.mkdir(mode=0o700)
        with source_connection(credentials,config) as conn:
            conn.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only=True
            snapshot=conn.execute('select pg_export_snapshot()').fetchone()[0]
            extensions=conn.execute("select json_agg(json_build_object('name',e.extname,'schema',n.nspname,'version',e.extversion)) from pg_extension e join pg_namespace n on n.oid=e.extnamespace").fetchone()[0]
            version=conn.execute("select current_setting('server_version_num')::int").fetchone()[0]
            if version//10000 != 17:raise ValueError('Review source Postgres major before exporting')
            large=conn.execute('select count(*) from pg_largeobject_metadata').fetchone()[0]
            if large:raise ValueError('Grant and verify large-object coverage before activation')
            schemas=[r[0] for r in conn.execute("select nspname from pg_namespace where nspname !~ '^pg_' and nspname not in ('information_schema','pgbouncer','graphql','graphql_public','realtime')")]
            if set(schemas)!=set(config['schemas']):raise ValueError('Unreviewed schema drift; export scope needs review')
            rows=conn.execute('select bucket_id,name,version,metadata,updated_at from storage.objects order by bucket_id,name').fetchall()
            buckets=conn.execute('select to_jsonb(b) from storage.buckets b order by id').fetchall()
            env=pg_environment(credentials['database_url'])
            args=['pg_dump','--format=custom','--no-password','--snapshot',snapshot,'--lock-wait-timeout','10s','--file',str(root/'database.dump')]
            args += [arg for schema in config['schemas'] for arg in ['--schema',schema]]
            run(args,env=env)
            run(['pg_restore','--list',str(root/'database.dump')])
            (root/'roles.sql').write_bytes(run(['pg_dumpall','--roles-only','--no-role-passwords','--no-password'],env=env))
            (root/'buckets.json').write_text(json.dumps([r[0] for r in buckets]))
            manifest=[]
            storage=Storage(config['storage_url'],credentials['storage_read_key'])
            downloaded=0;reused=0
            for bucket,key,version,metadata,updated_at in rows:
                name=hashlib.sha256((bucket+'\0'+key).encode()).hexdigest()
                identity=cache.identity(bucket,key,version,metadata,updated_at) if version and updated_at else None
                from cryptography.exceptions import InvalidTag
                try:item=cache.restore(identity,objects/name) if identity else None
                except (InvalidTag,ValueError):
                    (objects/name).unlink(missing_ok=True);cache.invalidate(identity);item=None
                if item is None:
                    item=storage.download(bucket,key,objects/name);downloaded+=1
                    if identity:cache.stage(identity,objects/name)
                else:reused+=1
                expected=(metadata or {}).get('size')
                if expected is not None and int(expected)!=item['bytes']:raise ValueError('Storage byte size changed during export')
                manifest.append({'bucket':bucket,'key':key,'version':version,'updated_at':updated_at.isoformat() if updated_at else None,'file':name,**item})
                if len(manifest)%100==0:print(json.dumps({'storage_objects_processed':len(manifest),'total':len(rows),'downloaded':downloaded,'reused':reused}),flush=True)
            # A fresh read catches replacement/deletion while files were transferred.
            with source_connection(credentials,config) as verify:
                latest=verify.execute('select bucket_id,name,version,metadata,updated_at from storage.objects order by bucket_id,name').fetchall()
            if rows!=latest:raise ValueError('Storage changed during export; retry rather than publish mixed generations')
            cache.commit()  # Only stable inventory generations become reusable.
            import shutil
            shutil.copyfile(config['policy_file'],root/'sanitization-policy.json')
            shutil.copyfile(config['personas_file'],root/'testing-personas.sql')
            (root/'manifest.json').write_text(json.dumps({'format':1,'project_ref':config['project_ref'],'postgres_version_num':version,
                'schemas':schemas,'extensions':extensions,'objects':manifest,'database_sha256':file_hash(root/'database.dump')},indent=2))
            for p in root.rglob('*'):
                if p.is_file():p.chmod(0o600)
        operator=Path(config['state_directory'])/'operator-recovery'
        if operator.exists():
            import shutil
            shutil.copytree(operator,root/'operator-recovery',symlinks=False)
        # Restic sees stable paths across runs despite temporary export directories.
        env={**os.environ,'AWS_ACCESS_KEY_ID':credentials['b2_key_id'],'AWS_SECRET_ACCESS_KEY':credentials['b2_application_key']}
        run(['restic','--no-cache','--repo',config['repository'],'--password-file',str(password),'backup','--host','nas','--tag','dealpipe-production','.'],env=env,cwd=root)
        record(config,'backup',objects=len(manifest),object_bytes=sum(x['bytes'] for x in manifest),downloaded=downloaded,reused=reused)
        return {'backup':'passed','objects':len(manifest)}


def verify_database_artifact(root,config):
    root=Path(root); manifest=json.loads((root/'manifest.json').read_text())
    if manifest['project_ref']!=config['project_ref']:raise ValueError('Unexpected project in recovery snapshot')
    if file_hash(root/'database.dump')!=manifest['database_sha256']:raise ValueError('Database checksum mismatch')
    run(['pg_restore','--list',str(root/'database.dump')])
    return manifest


def verify_artifacts(root,config):
    root=Path(root);manifest=verify_database_artifact(root,config)
    for item in manifest['objects']:
        filename=item['file']
        if len(filename)!=64 or any(c not in '0123456789abcdef' for c in filename):raise ValueError('Invalid object filename')
        path=root/'objects'/filename
        if path.stat().st_size!=item['bytes'] or file_hash(path)!=item['sha256']:raise ValueError('Storage checksum mismatch')
    return manifest


def verify_restore(config,credentials,password):
    workspace=private_ram(config)
    with tempfile.TemporaryDirectory(prefix='restore-',dir=workspace) as temp:
        restic(config,credentials,password,'restore','latest','--host','nas','--tag','dealpipe-production','--target',temp)
        manifest=verify_artifacts(Path(temp),config)
        record(config,'restore_verify',objects=len(manifest['objects']))
        return {'restored_artifacts':'passed','objects':len(manifest['objects']),'database_import':'separate quarantine rehearsal required'}


def install(config):
    if os.geteuid()!=0:raise ValueError('Install on the NAS as root')
    state=Path(config['state_directory']);state.mkdir(mode=0o700,parents=True,exist_ok=True);state.chmod(0o700)
    target=state/'credentials.json'
    if target.exists():raise ValueError('Credentials already exist; rotate deliberately')
    values={key:getpass.getpass(label+': ').strip() for key,label in [
        ('database_url','Dedicated backup-reader session/direct URL with verify-full TLS (hidden)'),
        ('storage_read_key','Production Supabase Storage read credential (hidden)'),
        ('b2_key_id','Separate production B2 key ID (hidden)'),('b2_application_key','Separate production B2 application key (hidden)')]}
    with source_connection(values,config):pass
    write_private(target,json.dumps(values))
    write_private(state/'repository-password',secrets.token_urlsafe(48)+'\n')
    print('Installed privately. Save repository-password outside the NAS before init.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install-credentials','init','backup','check','restore-verify','retention'])
    parser.add_argument('--config',type=Path,default=HERE/'config.json')
    args=parser.parse_args();config=json.loads(args.config.read_text())
    if args.action=='install-credentials':install(config);return
    credentials=private_json(Path(config['state_directory'])/'credentials.json')
    password=Path(config['state_directory'])/'repository-password'
    try:
        if args.action=='init':
            copy=private_json(Path(config['state_directory'])/'recovery-password-copy.json')
            if copy['sha256']!=hashlib.sha256(password.read_bytes()).hexdigest():raise ValueError('Independent recovery-password copy must be verified before initialization')
            restic(config,credentials,password,'init');result={'initialized':True}
        elif args.action=='backup':result=backup(config,credentials,password)
        elif args.action=='retention':
            from health import check
            check(Path(config['state_directory'])/'status.json',ignore_failure='retention')
            restic(config,credentials,password,'check')
            restic(config,credentials,password,'forget','--host','nas','--tag','dealpipe-production',
                   '--keep-daily','30','--keep-weekly','8','--keep-monthly','6','--prune','--max-repack-size','1G')
            record(config,'retention');result={'retention':'passed'}
        elif args.action=='check':
            restic(config,credentials,password,'check','--read-data-subset=5%');record(config,'check');result={'check':'passed'}
        else:result=verify_restore(config,credentials,password)
    except Exception:
        record(config,'failure',action=args.action)
        raise SystemExit('Production recovery failed; no success receipt written. Inspect privately; never paste raw diagnostics.') from None
    print(json.dumps(result))


if __name__=='__main__':main()
