#!/usr/bin/env python3
"""Copy completed local Restic snapshots to a separately encrypted offsite repository."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from urllib.parse import urlsplit


def timestamp():
    return datetime.now(timezone.utc)


def select_snapshots(snapshots, groups, max_age_hours, current=None):
    current = current or timestamp()
    selected = []
    for group in groups:
        candidates = [s for s in snapshots if set(group['required_paths']) <= set(s['paths'])
                      and s.get('hostname') == group['hostname']]
        if not candidates:
            raise ValueError('Missing successful local snapshot for ' + group['name'])
        latest = max(candidates, key=lambda s: datetime.fromisoformat(s['time'].replace('Z', '+00:00')))
        age = (current - datetime.fromisoformat(latest['time'].replace('Z', '+00:00'))).total_seconds()
        if age < -300 or age > max_age_hours * 3600:
            raise ValueError('Local snapshot stale or future-dated for ' + group['name'])
        selected.append(latest)
    return list({s['id']: s for s in selected}.values())


def validate_repository(repository):
    # The supported cloud target is explicitly EU B2, without URL credentials.
    if not repository.startswith('s3:https://'):
        raise ValueError('Expected an HTTPS S3 repository in Backblaze EU')
    parsed = urlsplit(repository.removeprefix('s3:'))
    if not re.fullmatch(r's3\.eu-[a-z]+-\d+\.backblazeb2\.com', parsed.netloc):
        raise ValueError('Use the bucket actual EU Backblaze endpoint')
    if len(parsed.path.strip('/').split('/')) < 2 or parsed.query or parsed.fragment:
        raise ValueError('Specify a bucket and repository prefix without URL secrets')
    if any(x in repository for x in ['REPLACE', '<', '>']):
        raise ValueError('Configure the actual bucket and endpoint before activation')


def private_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Credential file must be a private regular file')
    if os.geteuid() == 0 and path.stat().st_uid != 0:
        raise ValueError('Credential file must be root-owned')
    return path


class Backup:
    def __init__(self, config):
        self.config = config
        self.state = Path(config['state_directory'])
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)

    def command(self, source, *arguments, raw=False):
        prefix = 'source' if source else 'destination'
        password = private_file(self.config[prefix + '_password_file'])
        args = [self.config.get('restic', 'restic'), '--no-cache', '--repo', self.config[prefix + '_repository'],
                '--password-file', str(password), *arguments]
        result = subprocess.run(args, capture_output=True, timeout=7200)
        if result.returncode:
            # No arbitrary provider diagnostics, filenames or credentials in journals.
            raise RuntimeError('Restic ' + arguments[0] + ' failed; inspect credentials, endpoint and repository health')
        return result.stdout if raw else result.stdout.decode()

    def selected(self):
        snapshots = json.loads(self.command(True, 'snapshots', '--json'))
        return select_snapshots(snapshots, self.config['groups'], self.config['max_source_age_hours'])

    def record(self, event, **fields):
        path = self.state / 'status.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        state[event] = {'at': timestamp().isoformat(), **fields}
        with tempfile.NamedTemporaryFile(mode='w', dir=self.state, delete=False) as stream:
            json.dump(state, stream, indent=2)
            temporary = Path(stream.name)
        temporary.chmod(0o600)
        temporary.replace(path)

    def execute(self, action, *, ignore_failure=None):
        if action == 'plan':
            selected = self.selected()
            return {'snapshots': len(selected), 'logical_bytes': sum(s.get('summary', {}).get('total_bytes_processed', 0) for s in selected)}
        if action == 'health':
            status = json.loads((self.state / 'status.json').read_text())
            for name, budget in [('copy', 36 * 3600), ('check', 8 * 86400), ('restore_test', 35 * 86400)]:
                age = (timestamp() - datetime.fromisoformat(status[name]['at'])).total_seconds()
                if age < -300 or age > budget:
                    raise ValueError('Offsite ' + name + ' receipt is stale')
            failure = status.get('failure')
            if failure:
                name = failure['action'].replace('-', '_')
                if name != ignore_failure and failure['at'] > status.get(name, {}).get('at', ''):
                    raise ValueError('Latest offsite action failed')
            return {'health': 'passed'}
        validate_repository(self.config['destination_repository'])
        if action == 'init':
            self.command(False, 'init', '--copy-chunker-params', '--from-repo', self.config['source_repository'],
                         '--from-password-file', str(private_file(self.config['source_password_file'])))
            return {'initialized': True}
        source_id = json.loads(self.command(True, 'cat', 'config'))['id']
        destination_id = json.loads(self.command(False, 'cat', 'config'))['id']
        if source_id == destination_id:
            raise ValueError('Offsite repository must be independent of local recovery')
        if action == 'retention':
            self.execute('health', ignore_failure='retention')
            self.command(False, 'check')
            self.command(False, 'forget', '--keep-daily', '30', '--keep-weekly', '8',
                         '--keep-monthly', '6', '--prune', '--max-repack-size', '1G')
            self.record('retention')
            return {'retention': 'passed'}
        if action == 'copy':
            selected = self.selected()
            self.command(False, 'copy', '--from-repo', self.config['source_repository'],
                         '--from-password-file', str(private_file(self.config['source_password_file'])),
                         *[s['id'] for s in selected])
            destination = json.loads(self.command(False, 'snapshots', '--json'))
            for source in selected:
                assert any(s['time'] == source['time'] and s['paths'] == source['paths'] and s['hostname'] == source['hostname']
                           for s in destination), 'Copied snapshot not readable in destination'
            self.record('copy', snapshots=len(selected))
            return {'copied_or_already_present': len(selected)}
        if action == 'check':
            self.command(False, 'check', '--read-data-subset=5%')
            self.record('check', read_data_subset='5%')
            return {'integrity_check': 'passed', 'read_data_subset': '5%'}
        if action == 'restore-test':
            source = self.selected()[0]
            destination = json.loads(self.command(False, 'snapshots', '--json'))
            target = next(s for s in destination if s['time'] == source['time'] and s['paths'] == source['paths']
                          and s['hostname'] == source['hostname'])
            filename = self.config['restore_sample']
            if not filename.startswith('/') or '..' in Path(filename).parts:
                raise ValueError('Restore sample must be an absolute path without traversal')
            expected = self.command(True, 'dump', source['id'], filename, raw=True)
            with tempfile.TemporaryDirectory(prefix='restore-', dir=self.state) as temp:
                self.command(False, 'restore', target['id'], '--target', temp, '--include', filename)
                restored = Path(temp) / filename.lstrip('/')
                if hashlib.sha256(restored.read_bytes()).digest() != hashlib.sha256(expected).digest():
                    raise ValueError('Restored sample differs from the source snapshot')
            self.record('restore_test', verified=True)
            return {'restore_test': 'passed'}
        raise ValueError('Unknown operator action')


def install_credentials(directory):
    if os.geteuid() != 0:
        raise ValueError('Run the credential installer as the NAS operator/root')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    key_id = getpass.getpass('B2 application key ID (hidden): ').strip()
    key = getpass.getpass('B2 application key (hidden): ').strip()
    if not re.fullmatch(r'[A-Za-z0-9]+', key_id) or not re.fullmatch(r'[A-Za-z0-9+/=_-]+', key):
        raise ValueError('Unexpected application-key format')
    path = directory / 'homelab.env'
    if path.exists():
        raise ValueError('Credential file already exists; rotate deliberately with an operator backup')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('AWS_ACCESS_KEY_ID=' + key_id + '\nAWS_SECRET_ACCESS_KEY=' + key + '\n')
    password = directory / 'homelab-password'
    if not password.exists():
        fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(48) + '\n')
    return {'installed': True, 'recovery_password': 'Save the password privately outside the NAS before initialization'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'init', 'copy', 'check', 'restore-test', 'retention', 'health', 'install-credentials'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--secrets-directory', type=Path, default=Path('/var/lib/offsite-backup/secrets'))
    args = parser.parse_args()
    if args.action == 'install-credentials':
        result = install_credentials(args.secrets_directory)
    else:
        if not args.config:
            parser.error('--config is required')
        backup = Backup(json.loads(args.config.read_text()))
        try:
            result = backup.execute(args.action)
        except (OSError, ValueError, RuntimeError, AssertionError, StopIteration, KeyError, subprocess.SubprocessError):
            backup.record('failure', action=args.action)
            raise SystemExit('Offsite backup failed; inspect source freshness, credentials and destination health')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
