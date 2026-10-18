#!/usr/bin/env python3
"""Inspect or set only prior-version expiry in the two configured recovery buckets."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def request(url, *, token=None, body=None):
    headers = {'Authorization': token} if token else {}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None: headers['Content-Type'] = 'application/json'
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as response:
        return json.load(response)


def desired_rules(existing, prefix):
    # Refuse overlapping owner-managed rules rather than replace their intent.
    for rule in existing:
        old = rule.get('fileNamePrefix', '')
        if old != prefix and (old.startswith(prefix) or prefix.startswith(old)):
            raise ValueError('Overlapping lifecycle policy requires operator review')
    result = [rule for rule in existing if rule.get('fileNamePrefix') != prefix]
    result.append({'fileNamePrefix': prefix, 'daysFromHidingToDeleting': 90,
                   'daysFromUploadingToHiding': None, 'daysFromStartingToCancelingUnfinishedLargeFiles': 7})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['homelab', 'production']); parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0: raise ValueError('Run as NAS operator')
    if args.kind == 'homelab':
        values = dict(line.strip().split('=', 1) for line in Path('/var/lib/offsite-backup/secrets/homelab.env').read_text().splitlines() if '=' in line)
        key, secret = values['AWS_ACCESS_KEY_ID'], values['AWS_SECRET_ACCESS_KEY']
        bucket, prefix = 'qnoxslab-homelab-backups', 'homelab/'
    else:
        values = json.loads(Path('/var/lib/dealpipe-recovery/credentials.json').read_text())
        key, secret = values['b2_key_id'], values['b2_application_key']
        bucket, prefix = 'qnoxslab-dealpipe-production-backups', 'production/'
    auth = request('https://api.backblazeb2.com/b2api/v4/b2_authorize_account',
                   token='Basic ' + base64.b64encode((key + ':' + secret).encode()).decode())
    api = auth['apiInfo']['storageApi']; scope = api['allowed']
    if scope.get('namePrefix') != prefix: raise ValueError('Unexpected credential prefix')
    base = api['apiUrl'] + '/b2api/v4/'
    found = request(base + 'b2_list_buckets', token=auth['authorizationToken'],
                    body={'accountId': auth['accountId'], 'bucketName': bucket})['buckets']
    if len(found) != 1 or found[0]['bucketName'] != bucket: raise ValueError('Unexpected bucket')
    current = found[0]
    if current['bucketType'] != 'allPrivate': raise ValueError('Recovery bucket must be private')
    rules = desired_rules(current.get('lifecycleRules', []), prefix)
    if args.apply:
        current = request(base + 'b2_update_bucket', token=auth['authorizationToken'], body={
            'accountId': auth['accountId'], 'bucketId': current['bucketId'],
            'ifRevisionIs': current['revision'], 'lifecycleRules': rules})
        if current['lifecycleRules'] != rules: raise ValueError('Lifecycle verification mismatch')
    print(json.dumps({'bucket': bucket, 'applied': args.apply, 'lifecycle': current.get('lifecycleRules', []),
                      'target': rules}))


if __name__ == '__main__':
    try: main()
    except urllib.error.HTTPError as error: sys.exit('B2 lifecycle request denied: HTTP ' + str(error.code))
    except Exception as error: sys.exit('B2 lifecycle failed: ' + type(error).__name__)
