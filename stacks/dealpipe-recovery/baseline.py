#!/usr/bin/env python3
"""Pin the migration-built testing schema; never accept SQL definitions from production data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from backup import private_json,write_private

RECEIPT=Path('/var/lib/dealpipe-testing/schema-baseline.json')


def fingerprint(conn):
    definitions=conn.execute("""select json_build_object(
      'columns',(select json_agg(row_to_json(c) order by table_schema,table_name,ordinal_position) from
        (select table_schema,table_name,column_name,ordinal_position,udt_name,is_nullable,column_default,is_generated
         from information_schema.columns where table_schema in ('public','dealpipe_authz')) c),
      'functions',(select json_agg(pg_get_functiondef(p.oid) order by n.nspname,p.proname,pg_get_function_identity_arguments(p.oid))
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','dealpipe_authz') and p.prokind='f'),
      'policies',(select json_agg(row_to_json(p) order by schemaname,tablename,policyname) from pg_policies p where schemaname in ('public','storage')),
      'privileges',(select json_agg(json_build_array(n.nspname,c.relname,c.relacl::text,c.relrowsecurity,pg_get_userbyid(c.relowner)) order by n.nspname,c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname in ('public','storage') and c.relkind in ('r','p','v','m')),
      'triggers',(select json_agg(pg_get_triggerdef(t.oid) order by n.nspname,c.relname,t.tgname)
        from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace
        where not t.tgisinternal and n.nspname in ('public','auth','storage'))
      )""").fetchone()[0]
    return hashlib.sha256(json.dumps(definitions,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def verify(conn):
    baseline=private_json(RECEIPT)
    if baseline['definitions_sha256']!=fingerprint(conn):
        raise ValueError('Testing schema changed. Apply/review repository migrations and reseal the baseline before refreshing data.')


def main():
    import psycopg
    from refresh import local_dsn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--replace-after-reviewed-migrations',action='store_true')
    args=parser.parse_args()
    if len(args.source_commit)!=40 or any(c not in '0123456789abcdef' for c in args.source_commit):raise ValueError('Supply the exact reviewed source commit')
    settings=private_json('/var/lib/dealpipe-testing/settings.json')
    with psycopg.connect(local_dsn(settings,False)) as conn:
        result={'source_commit':args.source_commit,'sealed_at':datetime.now(timezone.utc).isoformat(),'definitions_sha256':fingerprint(conn)}
    if RECEIPT.exists():
        if not args.replace_after_reviewed_migrations:raise ValueError('Baseline exists. Reseal only after reviewing migration changes.')
        RECEIPT.unlink()
    write_private(RECEIPT,json.dumps(result,indent=2))
    print('Migration-built testing schema sealed; production SQL is excluded from refreshes')


if __name__=='__main__':main()
