#!/usr/bin/env python3
"""Operator-owned source drift report. No network, note rewriting or approvals."""
import argparse
import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote

LINK = re.compile(r'https://github\.com/([^/\s]+)/([^/\s]+)/blob/([0-9a-f]{40})/([^\s)\]>#?]+)')
MARKER = '<!-- shared-brain:source-review-generated -->'


def git(repo, *args):
    return subprocess.run(['git', '-c', 'safe.directory=' + str(repo), '-C', str(repo), *args],
                          check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def blob(repo, revision, path):
    # ls-tree distinguishes an absent path from an unavailable commit/repository.
    value = git(repo, 'ls-tree', revision, '--', path)
    if not value:
        return None
    fields = value.split('\t', 1)[0].split()
    if len(fields) != 3 or fields[1] != 'blob':
        raise ValueError('Source is not a file')
    return fields[2]


def inspect(config, brain):
    repos = {s['url'].removeprefix('https://github.com/').rstrip('/'): s
             for s in config['sources'] if s['type'] == 'repo'}
    findings, errors, checked = [], [], 0
    cached_blob = lru_cache(maxsize=None)(blob)
    heads = {}
    for note in sorted((brain / 'Projects').rglob('*.md')):
        if note.is_symlink() or not note.resolve().is_relative_to(brain.resolve()):
            continue
        relative = note.relative_to(brain).as_posix()
        if any(p in {'History', 'Plans', 'Archive'} for p in note.relative_to(brain).parts):
            continue
        for owner, repository, revision, encoded in sorted(set(LINK.findall(note.read_text()))):
            source = repos.get(owner + '/' + repository)
            if not source:
                continue
            path = unquote(encoded)
            if path.startswith('/') or '..' in Path(path).parts:
                errors.append({'note': relative, 'error': 'Invalid source path'})
                continue
            checked += 1
            try:
                repo = Path(source['path'])
                refresh = repo.parent / (source['project'] + '-refresh.json')
                if refresh.exists():
                    health = json.loads(refresh.read_text())
                    if health.get('state') != 'ok':
                        raise ValueError('Upstream refresh unavailable')
                    stamp = datetime.fromisoformat(health['checked_at'])
                    if (datetime.now(timezone.utc) - stamp).total_seconds() > 10800:
                        raise ValueError('Upstream refresh older than three hours')
                else:
                    raise ValueError('Upstream refresh status missing')
                key = (str(repo), source['ref'])
                if key not in heads:
                    heads[key] = git(repo, 'rev-parse', '--verify', source['ref'] + '^{commit}')
                head = heads[key]
                before, after = cached_blob(repo, revision, path), cached_blob(repo, head, path)
                if before is None:
                    raise ValueError('Pinned source missing')
                if before != after:
                    findings.append({'note': relative, 'source': source['url'] + '/blob/' + revision + '/' + encoded,
                                     'current': source['url'] + '/blob/' + head + '/' + encoded,
                                     'change': 'deleted' if after is None else 'changed'})
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                errors.append({'note': relative, 'source': owner + '/' + repository + '/' + path,
                               'error': 'Source unavailable or stale; inspect collector and pinned revision'})
    return {'checked_links': checked, 'findings': findings, 'errors': errors}


def publish(path, content, vault=False):
    """Group-readable from creation for the LiveSync watcher, atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ('.brain-review-' + uuid.uuid4().hex)
    mask = os.umask(0o007)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660 if vault else 0o600)
    finally:
        os.umask(mask)
    try:
        with os.fdopen(fd, 'w') as stream:
            if vault and os.geteuid() == 0:
                import pwd
                os.fchown(stream.fileno(), pwd.getpwnam('obsidian-bridge').pw_uid, 100)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run(config, brain, state):
    report = inspect(config, brain)
    queue = brain / 'Inbox/Source Review.md'
    if queue.is_symlink() or (queue.exists() and MARKER not in queue.read_text()):
        raise ValueError('Refusing to replace an operator-owned note')
    signature = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    previous = json.loads(state.read_text()) if state.exists() else {}
    if previous.get('signature') != signature or not queue.exists():
        lines = ['---', 'title: Source Review', 'kind: source-review', 'status: proposed', '---',
                 MARKER, '# Source Review', '',
                 'Generated review queue. Entries are drift signals, not decisions or edit authorization.',
                 'Do not edit this generated note; update the cited canonical summary after human review.',
                 'A refreshed citation clears its finding on the next run. See [[Brain/System/Source Change Review]].', '',
                 f"Checked {report['checked_links']} pinned source links; {len(report['findings'])} changed; {len(report['errors'])} unavailable.", '']
        for item in report['findings']:
            lines += [f"- [[Brain/{item['note']}]] — {item['change']}: [pinned]({item['source']}) → [current]({item['current']})"]
        for item in report['errors']:
            lines += [f"- Unavailable: [[Brain/{item['note']}]] — {item['error']}"]
        if not report['findings'] and not report['errors']:
            lines += ['No changes detected in the pinned files covered by this check.']
        publish(queue, '\n'.join(lines) + '\n', vault=True)
    publish(state, json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), 'signature': signature, **report}, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--brain', type=Path, default=Path('/storage/obsidian/Brain'))
    parser.add_argument('--state', type=Path, default=Path('/var/lib/agent-platform/knowledge/review/state.json'))
    args = parser.parse_args()
    result = run(json.loads(args.config.read_text()), args.brain, args.state)
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in result.items()}))
    raise SystemExit(1 if result['errors'] else 0)
