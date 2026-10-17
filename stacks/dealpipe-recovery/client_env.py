#!/usr/bin/env python3
"""Write only isolated testing credentials to a private file; never print their values."""
import argparse
import json
from pathlib import Path
import secrets
from urllib.parse import quote

from backup import private_json,write_private


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('/var/lib/dealpipe-testing/client.env'))
    args=parser.parse_args();s=private_json('/var/lib/dealpipe-testing/settings.json')
    database='postgresql://postgres:'+quote(s['TEST_DB_PASSWORD'],safe='')+'@127.0.0.1:18786/postgres'
    values={'DATABASE_URL':database,'DIRECT_DATABASE_URL':database,'NEXT_PUBLIC_SUPABASE_URL':'http://127.0.0.1:18785',
        'NEXT_PUBLIC_SUPABASE_ANON_KEY':s['TEST_ANON_KEY'],'SUPABASE_SECRET_KEY':s['TEST_SERVICE_KEY'],
        'SUPABASE_SERVICE_ROLE_KEY':s['TEST_SERVICE_KEY'],'SUPABASE_JWT_SECRET':s['TEST_JWT_SECRET'],
        'TOKEN_ENCRYPTION_KEY':secrets.token_hex(32),'BILLING_ENABLED':'false','RESEND_API_KEY':'',
        'STRIPE_SECRET_KEY':'','GMAIL_CLIENT_ID':'','GMAIL_CLIENT_SECRET':'','OUTLOOK_CLIENT_ID':'',
        'OUTLOOK_CLIENT_SECRET':'','PANDADOC_CLIENT_ID':'','PANDADOC_CLIENT_SECRET':'','TWILIO_ACCOUNT_SID':'',
        'TWILIO_AUTH_TOKEN':'','WHATSAPP_ACCESS_TOKEN':'','GOOGLE_VERTEX_PROJECT':'','AI_GATEWAY_API_KEY':''}
    write_private(args.output,'# Isolated NAS testing only. Use in a dedicated checkout with no production env files.\n'+
        '\n'.join(k+'='+json.dumps(v) for k,v in values.items())+'\n')
    print('Private test environment written; values are intentionally not displayed')


if __name__=='__main__':main()
