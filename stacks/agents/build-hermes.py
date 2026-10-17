#!/usr/bin/env python3
"""Build the pinned Hermes launcher fix through Portainer's Docker API."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
from urllib.parse import urlencode

from deploy import Portainer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://nas.tailb80645.ts.net:9443')
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--connect-address')
    parser.add_argument('--endpoint', type=int, default=2)
    args = parser.parse_args()
    context = io.BytesIO()
    with tarfile.open(fileobj=context, mode='w') as archive:
        for name in ['Dockerfile', 'patch-launcher.py']:
            data = (Path(__file__).parent / 'hermes' / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    content = context.getvalue()
    tag = 'localhost/homelab-hermes:' + hashlib.sha256(content).hexdigest()
    api = Portainer(args.url, args.token_file, args.connect_address)
    images = api.request('GET', f'/endpoints/{args.endpoint}/docker/images/json')
    if not any(tag in (image.get('RepoTags') or []) for image in images):
        with tempfile.NamedTemporaryFile(suffix='.tar') as archive:
            archive.write(content)
            archive.flush()
            query = urlencode({'t': tag, 'dockerfile': 'Dockerfile', 'rm': 'true', 'pull': 'false'})
            response = api.request('POST', f'/endpoints/{args.endpoint}/docker/build?{query}', archive=archive.name)
        events = response.splitlines() if isinstance(response, str) else [json.dumps(response)]
        for line in events:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and (event.get('error') or event.get('errorDetail')):
                raise RuntimeError('Hermes image build failed; inspect the Portainer build logs')
        images = api.request('GET', f'/endpoints/{args.endpoint}/docker/images/json')
        if not any(tag in (image.get('RepoTags') or []) for image in images):
            raise RuntimeError('Portainer did not produce the expected Hermes image')
    print(tag)


if __name__ == '__main__':
    main()
