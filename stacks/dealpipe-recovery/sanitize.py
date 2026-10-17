#!/usr/bin/env python3
"""Transform a quarantine DB. Never export production SQL definitions or file bytes."""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import uuid

HERE=Path(__file__).resolve().parent


class Sanitizer:
    def __init__(self, policy, key=None):
        self.policy=policy;self.key=key or secrets.token_bytes(32)

    def digest(self,value):
        return hmac.new(self.key,str(value).encode(),hashlib.sha256).hexdigest()

    def identifier(self,value):
        return uuid.UUID(self.digest(value)[:32],version=4)

    def path(self,value):
        parts=[]
        for part in str(value).split('/'):
            try:parts.append(str(self.identifier(uuid.UUID(part))))
            except ValueError:
                ext=Path(part).suffix.lower()
                if ext not in {'.pdf','.png','.jpg','.jpeg','.webp','.txt','.csv','.docx','.xlsx'}:ext=''
                parts.append('synthetic-'+self.digest(part)[:24]+ext)
        return '/'.join(parts)

    def value(self,table,column,value):
        if value is None:return None
        spec=self.policy['tables'][table]['columns'][column];kind=spec['strategy']
        if kind=='null':return None
        if kind=='reference_period':
            if table!='public.market_data_series' or column!='period' or spec['type']!='date' or type(value) is not date:
                raise ValueError('Only the reviewed public market-data reference period may be retained')
            return value
        if table=='auth.users':
            if column=='id':return self.identifier(value)
            if column=='instance_id':return uuid.UUID(int=0)
            if column in ['aud','role']:return 'authenticated'
            if column=='email':return 'user-'+self.digest(value)[:20]+'@example.invalid'
            if column=='email_change_confirm_status':return 0
            if column=='phone':return None
            if kind in ['json_object','json_array']:return '{}' if kind=='json_object' else '[]'
            if kind=='boolean':return False
            if column in ['confirmed_at','email_confirmed_at','created_at','updated_at']:return datetime(2000,1,1,tzinfo=timezone.utc)
            if kind=='date':return None
            if kind=='text':return ''
        if kind=='enum':
            if value not in self.policy['enums'][spec['type']]:raise ValueError('Unreviewed enum value')
            return value
        if kind=='uuid':return self.identifier(value)
        if kind=='text':
            static=self.policy.get('static_values',{}).get(table+'.'+column)
            if static:
                if value not in static:raise ValueError('Unexpected constrained label')
                return value
            if re.search(r'(secret|token|password|credential|api_key|oauth|signature)',column):return ''
            try:return str(self.identifier(uuid.UUID(value)))
            except ValueError:pass
            if column in ['currency','waehrung']:return 'EUR'
            if column in ['country','country_code','land']:return 'DE'
            if column in ['timezone','time_zone']:return 'Europe/Berlin'
            if column in ['locale','language']:return 'de'
            if column=='unit_number':return str(int(self.digest(value)[:12],16))
            if 'email' in column:return 'user-'+self.digest(value)[:20]+'@example.invalid'
            if any(s in column for s in ['url','uri','website','domain']):return 'https://example.invalid/'+self.digest(value)[:20]
            if any(s in column for s in ['phone','telefon','mobile','fax','iban','bic']):return 'TEST-'+self.digest(value)[:16]
            if any(s in column for s in ['path','file_name','filename','storage_key']):return self.path(value)
            return 'synthetic-'+self.digest(value)[:24]
        if kind=='number':return Decimal(spec.get('fixture',1)) if isinstance(value,Decimal) else type(value)(spec.get('fixture',1))
        if kind=='boolean':return False
        if kind=='json_array':return '[]'
        if kind=='json_object':return '{}'
        if kind=='array':return []
        if kind=='date':
            year=2001 if any(s in column for s in ['end','expires','expiry','until']) else 2000
            return date(year,1,1) if spec['type']=='date' else datetime(year,1,1,tzinfo=timezone.utc)
        if kind=='inet':return '127.0.0.1'
        if kind=='bytes':return b''
        raise ValueError('Unreviewed transformation')


def catalog(conn):
    rows=conn.execute("""select c.table_schema,c.table_name,c.column_name,c.udt_name,c.is_generated
      from information_schema.columns c join information_schema.tables t using(table_schema,table_name)
      where t.table_type='BASE TABLE' and (c.table_schema='public' or (c.table_schema='auth' and c.table_name='users'))
      order by c.table_schema,c.table_name,c.ordinal_position""").fetchall()
    tables={}
    for schema,table,column,kind,generated in rows:
        tables.setdefault(schema+'.'+table,{})[column]={'type':kind,'generated':generated!='NEVER'}
    return tables


def validate_policy(actual,policy):
    if set(actual)!=set(policy['tables']):raise ValueError('Unreviewed table drift')
    for table,columns in actual.items():
        expected=policy['tables'][table]['columns']
        if set(columns)!=set(expected):raise ValueError('Unreviewed column drift in '+table)
        if any(spec['type']!=expected[col]['type'] for col,spec in columns.items()):raise ValueError('Unreviewed column type drift')


def sanitize(conn, policy):
    from psycopg import sql
    actual=catalog(conn);validate_policy(actual,policy)
    current=conn.execute('select current_database()').fetchone()[0]
    if not current.startswith('quarantine_'):raise ValueError('Only a quarantine_ database may be transformed')
    s=Sanitizer(policy)
    conn.execute('set local session_replication_role=replica')
    auth_tables=conn.execute("select tablename from pg_tables where schemaname='auth' and tablename not in ('users','schema_migrations')").fetchall()
    for (table,) in auth_tables:conn.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier('auth',table)))
    counts={}
    for table,columns in actual.items():
        rel=sql.Identifier(*table.split('.'))
        if policy['tables'][table]['action']=='empty':
            conn.execute(sql.SQL('DELETE FROM {}').format(rel));counts[table]=0;continue
        selected=[c for c in columns if not columns[c]['generated']]
        rows=conn.execute(sql.SQL('SELECT ctid::text,{} FROM {}').format(sql.SQL(',').join(map(sql.Identifier,selected)),rel)).fetchall()
        assignments=sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(c)) for c in selected)
        statement=sql.SQL('UPDATE {} SET {} WHERE ctid=%s::tid').format(rel,assignments)
        with conn.cursor() as cursor:
            cursor.executemany(statement,[tuple(s.value(table,c,v) for c,v in zip(selected,row[1:]))+(row[0],) for row in rows])
    conn.execute("""insert into auth.identities(id,user_id,provider_id,provider,identity_data,created_at,updated_at)
      select id,id,id::text,'email',jsonb_build_object('sub',id::text,'email',email,'email_verified',true),'2000-01-01'::timestamptz,'2000-01-01'::timestamptz
      from auth.users where email is not null""")
    fks=conn.execute("""select n.nspname,r.relname,pn.nspname,p.relname,
      array_agg(a.attname order by x.ord),array_agg(pa.attname order by x.ord)
      from pg_constraint c join pg_class r on r.oid=c.conrelid join pg_namespace n on n.oid=r.relnamespace
      join pg_class p on p.oid=c.confrelid join pg_namespace pn on pn.oid=p.relnamespace
      cross join lateral unnest(c.conkey,c.confkey) with ordinality x(child,parent,ord)
      join pg_attribute a on a.attrelid=r.oid and a.attnum=x.child
      join pg_attribute pa on pa.attrelid=p.oid and pa.attnum=x.parent
      where c.contype='f' and n.nspname in ('public','auth') group by c.oid,n.nspname,r.relname,pn.nspname,p.relname""").fetchall()
    while True:
        removed=0
        for schema,table,pschema,parent,children,parents in fks:
            present=sql.SQL(' AND ').join(sql.SQL('c.{} IS NOT NULL').format(sql.Identifier(c)) for c in children)
            matches=sql.SQL(' AND ').join(sql.SQL('p.{}=c.{}').format(sql.Identifier(p),sql.Identifier(c)) for c,p in zip(children,parents))
            query=sql.SQL('DELETE FROM {} c WHERE {} AND NOT EXISTS(SELECT 1 FROM {} p WHERE {})').format(sql.Identifier(schema,table),present,sql.Identifier(pschema,parent),matches)
            removed+=conn.execute(query).rowcount
        if not removed:break
    placeholders=[]
    allowed_buckets=set(policy['allowed_buckets'])
    for bucket,key in conn.execute('select bucket_id,name from storage.objects'):
        if bucket not in allowed_buckets:raise ValueError('Unreviewed Storage bucket')
        placeholders.append({'bucket':bucket,'key':s.path(key)})
    conn.execute('set local session_replication_role=origin')
    for table in actual:
        counts[table]=conn.execute(sql.SQL('select count(*) from {}').format(sql.Identifier(*table.split('.')))).fetchone()[0]
    conn.execute('create schema recovery_sanitized')
    conn.execute('create table recovery_sanitized.receipt(version int not null, completed_at timestamptz not null)')
    conn.execute('insert into recovery_sanitized.receipt values(1,now())')
    return {'sanitized_tables':len(actual),'retained_rows':sum(counts.values()),'counts':counts,'placeholders':placeholders}


def main():
    from backup import private_json
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection-file',type=Path,required=True)
    parser.add_argument('--policy',type=Path,default=Path('/var/lib/dealpipe-recovery/sanitization-policy.json'))
    args=parser.parse_args()
    dsn=private_json(args.connection_file)['database_url'];parts=conninfo_to_dict(dsn)
    if parts.get('host')!='127.0.0.1' or parts.get('port')!='18766' or not parts.get('dbname','').startswith('quarantine_'):
        raise SystemExit('Sanitizer requires the NAS loopback quarantine endpoint')
    try:
        with psycopg.connect(dsn) as conn:result=sanitize(conn,private_json(args.policy))
    except Exception:
        raise SystemExit('Sanitization failed; transaction rolled back. No test artifact may be published.') from None
    print(json.dumps({k:v for k,v in result.items() if k not in ['counts','placeholders']}))


if __name__=='__main__':main()
