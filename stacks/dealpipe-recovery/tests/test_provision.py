import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import psycopg
    from provision import stage
except ImportError:
    psycopg=None


@unittest.skipUnless(psycopg, 'Run with the NAS psycopg runtime')
class ProvisionDiagnosticsTests(unittest.TestCase):
    def test_provider_errors_never_publish_credentials_or_response_bodies(self):
        cases=[urllib.error.HTTPError('https://example.invalid/CANARY-secret',401,'CANARY-secret',{},None),
               psycopg.errors.InsufficientPrivilege('CANARY-password and SQL'),
               ValueError('CANARY-unexpected-secret')]
        for error in cases:
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as temp:
                state=Path(temp)
                with self.assertRaises(SystemExit) as stopped:
                    with stage(state,'test provisioning step'):raise error
                receipt=state/'provision-status.json'
                self.assertNotIn('CANARY',str(stopped.exception)+receipt.read_text())
                self.assertEqual(receipt.stat().st_mode&0o777,0o600)
                details=json.loads(receipt.read_text())
                self.assertEqual(details['stage'],'test provisioning step')
                self.assertEqual(set(details)-{'stage','at'}, {'http_status'} if isinstance(error,urllib.error.HTTPError) else {'sqlstate'} if isinstance(error,psycopg.Error) else set())

    def test_success_leaves_no_failure_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            with stage(Path(temp),'successful validation'):pass
            self.assertFalse((Path(temp)/'provision-status.json').exists())
