from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('offsite', Path(__file__).parents[1] / 'offsite.py')
offsite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(offsite)


class SelectionTests(unittest.TestCase):
    def test_missing_stale_and_wrong_host_fail_closed(self):
        now = datetime.now(timezone.utc)
        group = {'name': 'nas', 'hostname': 'nas', 'required_paths': ['/vault', '/database-staging']}
        fresh = {'id': 'one', 'hostname': 'nas', 'time': now.isoformat(), 'paths': ['/vault', '/database-staging']}
        for snapshots in [[], [{**fresh, 'hostname': 'other'}], [{**fresh, 'paths': ['/vault']}],
                          [{**fresh, 'time': (now - timedelta(hours=37)).isoformat()}]]:
            with self.assertRaises(ValueError):
                offsite.select_snapshots(snapshots, [group], 36, now)
        self.assertEqual(offsite.select_snapshots([fresh], [group, group], 36, now), [fresh])

    def test_only_explicit_eu_endpoint_without_credentials_is_accepted(self):
        offsite.validate_repository('s3:https://s3.eu-central-003.backblazeb2.com/private-bucket/homelab')
        for url in ['s3:https://s3.us-west-004.backblazeb2.com/bucket/homelab',
                    's3:http://s3.eu-central-003.backblazeb2.com/bucket/homelab',
                    's3:https://key:secret@s3.eu-central-003.backblazeb2.com/bucket/homelab',
                    's3:https://s3.eu-central-003.backblazeb2.com/bucket/homelab?secret=value',
                    's3:https://REPLACE_EU_ENDPOINT/REPLACE_PRIVATE_BUCKET/homelab']:
            with self.assertRaises(ValueError):
                offsite.validate_repository(url)

    def test_health_requires_all_receipts_and_reports_newer_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = offsite.Backup({'state_directory': temp})
            state = {name: {'at': datetime.now(timezone.utc).isoformat()} for name in ['copy', 'check', 'restore_test']}
            path = Path(temp) / 'status.json'
            path.write_text(json.dumps(state))
            self.assertEqual(backup.execute('health'), {'health': 'passed'})
            state['failure'] = {'at': datetime.now(timezone.utc).isoformat(), 'action': 'copy'}
            path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):
                backup.execute('health')
            del state['failure']; del state['restore_test']
            path.write_text(json.dumps(state))
            with self.assertRaises(KeyError):
                backup.execute('health')


RESTIC = os.environ.get('RESTIC_BINARY') or shutil.which('restic')


@unittest.skipUnless(RESTIC, 'Set RESTIC_BINARY for a real encrypted copy/restore test')
class RealRepositoryTests(unittest.TestCase):
    def test_independent_encryption_copy_restore_and_source_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data'; data.mkdir()
            note = data / 'note.md'; note.write_text('durable knowledge\n')
            passwords = []
            for name, value in [('local-password', 'fixture-local-key'), ('remote-password', 'fixture-independent-key')]:
                p = root / name; p.write_text(value); p.chmod(0o600); passwords.append(p)
            config = {'restic': RESTIC, 'source_repository': str(root / 'local'),
                      'source_password_file': str(passwords[0]), 'destination_repository': str(root / 'offsite'),
                      'destination_password_file': str(passwords[1]), 'state_directory': str(root / 'state'),
                      'max_source_age_hours': 36, 'restore_sample': str(note),
                      'groups': [{'name': 'fixture', 'hostname': 'nas', 'required_paths': [str(data)]}]}
            backup = offsite.Backup(config)
            backup.command(True, 'init')
            backup.command(True, 'backup', '--host', 'nas', str(data))
            first = backup.selected()[0]['id']
            # A local repository stands in for S3 only at the backend boundary;
            # real Restic encryption, copy, metadata checks and restore still run.
            with patch.object(offsite, 'validate_repository', return_value=None):
                backup.execute('init')
                backup.execute('copy')
                backup.execute('copy')
                self.assertEqual(len(json.loads(backup.command(False, 'snapshots', '--json'))), 1)
                backup.execute('check')
                backup.execute('restore-test')
                self.assertEqual(backup.execute('health')['health'], 'passed')
                self.assertNotEqual(json.loads(backup.command(True, 'cat', 'config'))['id'],
                                    json.loads(backup.command(False, 'cat', 'config'))['id'])
                note.write_text('updated knowledge\n')
                backup.command(True, 'backup', '--host', 'nas', str(data))
                backup.execute('copy')
                backup.command(True, 'forget', first)
                remote = json.loads(backup.command(False, 'snapshots', '--json'))
                self.assertEqual(len(remote), 2)
                self.assertIn(b'durable knowledge\n', [backup.command(False, 'dump', s['id'], str(note), raw=True) for s in remote])
