import json
import hashlib
from pathlib import Path
import sys
import tempfile
import ssl
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import psycopg
    import provision
    from provision import stage,validate_b2
except ImportError:
    psycopg=None


@unittest.skipUnless(psycopg, 'Run with the NAS psycopg runtime')
class ProvisionDiagnosticsTests(unittest.TestCase):
    def test_supabase_ca_is_the_published_root_certificate(self):
        ca=(provision.HERE/'supabase-prod-ca-2021.crt').read_text()
        self.assertEqual(hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest(),
                         '807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa')

    def test_failed_database_preflight_never_requests_other_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            config=json.loads((provision.HERE/'config.json').read_text())
            config['state_directory']=str(root/'state')
            (root/'config.json').write_text(json.dumps(config))
            url='postgresql://postgres.'+config['project_ref']+':CANARY@aws-1-eu-central-1.pooler.supabase.com:5432/postgres'
            with patch.object(provision,'HERE',root),patch('provision.os.geteuid',return_value=0),\
                 patch('provision.getpass.getpass',return_value=url) as prompt,\
                 patch('provision.psycopg.connect',side_effect=psycopg.errors.InvalidPassword('CANARY')) as connect:
                with self.assertRaises(SystemExit) as stopped:provision.main()
                self.assertEqual(prompt.call_count,1)
                self.assertIn('production database connection',str(stopped.exception))
                self.assertNotIn('CANARY',str(stopped.exception))
                options=psycopg.conninfo.conninfo_to_dict(connect.call_args.args[0])
                self.assertEqual(options['sslmode'],'verify-full')
                self.assertEqual(options['sslrootcert'],str(root/'supabase-prod-ca-2021.crt'))
            self.assertFalse((root/'state/credentials.json').exists())

    def test_bucket_id_accepts_restricted_keys_without_disclosing_bucket_names(self):
        config={'b2_bucket_id':'expected-id'}
        scope={'buckets':[{'id':'expected-id','name':None}],'namePrefix':'production/',
               'capabilities':['listFiles','readFiles','writeFiles']}
        validate_b2({'apiInfo':{'storageApi':{'allowed':scope}}},config)
        legacy={k:v for k,v in scope.items() if k!='buckets'}
        legacy.update(bucketId='expected-id',bucketName=None)
        validate_b2({'allowed':legacy},config)
        for buckets in [None,[],[{'id':'homelab-id','name':'qnoxslab-dealpipe-production-backups'}],
                        [{'id':'expected-id'},{'id':'another-id'}]]:
            with self.assertRaises(SystemExit):
                validate_b2({'apiInfo':{'storageApi':{'allowed':{**scope,'buckets':buckets}}}},config)
        for change in [{'namePrefix':None},{'namePrefix':'homelab/'},{'capabilities':['writeFiles']}]:
            with self.assertRaises(SystemExit):
                validate_b2({'apiInfo':{'storageApi':{'allowed':{**scope,**change}}}},config)
        with self.assertRaises(SystemExit):validate_b2({'unexpected':'CANARY-secret'},config)

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
