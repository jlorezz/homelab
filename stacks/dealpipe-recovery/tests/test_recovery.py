import hashlib
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from sanitize import Sanitizer,validate_policy
from backup import private_json,record,verify_artifacts


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads((HERE/'tests/fixtures/policy.json').read_text())
        self.s=Sanitizer(self.policy,b'fixture-only-randomization-key')

    def test_identifiers_remain_consistent_without_publishing_source_ids(self):
        original=uuid.UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
        first=self.s.value('public.users','id',original)
        second=self.s.value('auth.users','id',original)
        self.assertEqual(first,second)
        self.assertNotEqual(first,original)
        self.assertNotEqual(first,Sanitizer(self.policy,b'another-refresh').identifier(original))

    def test_nested_payloads_tokens_and_contact_details_do_not_survive(self):
        self.assertEqual(self.s.value('auth.users','raw_user_meta_data',{'secret':'CANARY','nested':{'email':'real@example.com'}}),'{}')
        self.assertEqual(self.s.value('auth.users','encrypted_password','CANARY-password'),'')
        email=self.s.value('public.users','email','real-customer@example.com')
        self.assertTrue(email.endswith('@example.invalid'))
        self.assertNotIn('real-customer',email)
        self.assertEqual(self.s.value('auth.users','email','real-customer@example.com'),email)

    def test_paths_preserve_joined_ids_but_remove_source_names(self):
        original=uuid.UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
        path=self.s.path(str(original)+'/Jane-private-contract.pdf')
        self.assertTrue(path.startswith(str(self.s.identifier(original))+'/'))
        self.assertTrue(path.endswith('.pdf'))
        self.assertNotIn('Jane',path)
        self.assertNotIn(str(original),path)

    def test_unknown_columns_types_tables_and_enum_labels_fail_closed(self):
        policy={'tables':{'public.demo':{'columns':{'id':{'type':'uuid'}}}}}
        validate_policy({'public.demo':{'id':{'type':'uuid'}}},policy)
        for actual in [{'public.demo':{'id':{'type':'uuid'},'new_pii':{'type':'text'}}},
                       {'public.demo':{'id':{'type':'text'}}},
                       {'public.demo':{'id':{'type':'uuid'}},'public.new_pii':{}}]:
            with self.assertRaises(ValueError):validate_policy(actual,policy)
        with self.assertRaises(ValueError):self.s.value('public.users','status','NEW_UNREVIEWED_CUSTOMER_LABEL')

    def test_outbound_and_identity_material_is_explicitly_empty(self):
        for name in ['webhooks','webhook_deliveries','email_provider_connections','telephony_accounts','org_api_tokens','coworker_runs','crm_workflow_runs','account_erasure_requests']:
            self.assertEqual(self.policy['tables']['public.'+name]['action'],'empty')


class RecoveryTests(unittest.TestCase):
    def test_activation_requires_real_recovery_and_matching_dataset_proofs(self):
        from health import check
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'status.json'
            now=datetime.now(timezone.utc).isoformat()
            state={name:{'at':now} for name in ['backup','check','restore_verify','sanitize_refresh']}
            state['sanitize_refresh']['source']='production-backup'
            for name in ['testing_smoke','testing_realtime']:state[name]={'at':now,'sanitized_at':now}
            path.write_text(json.dumps(state));self.assertEqual(check(path),{'production_recovery':'healthy'})
            state['sanitize_refresh']['source']='fixture';path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):check(path)
            state['sanitize_refresh']['source']='production-backup'
            state['testing_realtime']['sanitized_at']='previous-dataset';path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):check(path)
            state['testing_realtime']['sanitized_at']=now
            state['backup']['at']=(datetime.now(timezone.utc)-timedelta(hours=37)).isoformat()
            path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):check(path)
            state['backup']['at']=now
            state['failure']={'at':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),'action':'sanitize-refresh'}
            path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):check(path)

    def test_failures_are_recorded_without_colliding_with_action_field(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'state_directory':temp}
            record(config,'failure',action='backup')
            self.assertEqual(json.loads((Path(temp)/'status.json').read_text())['failure']['action'],'backup')
            self.assertEqual((Path(temp)/'status.json').stat().st_mode&0o777,0o600)

    def test_credential_permissions_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'credentials';path.write_text('{}');path.chmod(0o600)
            self.assertEqual(private_json(path),{})
            path.chmod(0o644)
            with self.assertRaises(ValueError):private_json(path)
            link=Path(temp)/'link';link.symlink_to(path)
            with self.assertRaises(ValueError):private_json(link)

    def test_restored_database_and_object_bytes_are_both_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'objects').mkdir()
            (root/'database.dump').write_bytes(b'fixture database')
            object_name='a'*64;(root/'objects'/object_name).write_bytes(b'fixture document')
            manifest={'project_ref':'fixture','database_sha256':hashlib.sha256(b'fixture database').hexdigest(),
                      'objects':[{'file':object_name,'bytes':16,'sha256':hashlib.sha256(b'fixture document').hexdigest()}]}
            (root/'manifest.json').write_text(json.dumps(manifest))
            with patch('backup.run',return_value=b'archive catalog'):
                verify_artifacts(root,{'project_ref':'fixture'})
                (root/'objects'/object_name).write_bytes(b'damaged document')
                with self.assertRaises(ValueError):verify_artifacts(root,{'project_ref':'fixture'})


if __name__=='__main__':unittest.main()


class EncryptedCacheTests(unittest.TestCase):
    def test_uncommitted_objects_are_not_reused_and_committed_objects_authenticate(self):
        from cache import Cache
        try:import cryptography
        except ImportError:self.skipTest('Run with the NAS cryptography runtime')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.write_bytes(b'CANARY-private-object-content'*100)
            cache=Cache(root/'cache',root/'key')
            identity='f'*64
            cache.stage(identity,source)
            self.assertIsNone(cache.restore(identity,root/'not-committed'))
            encrypted=next((root/'cache').iterdir()).read_bytes()
            self.assertNotIn(b'CANARY',encrypted)
            cache.commit()
            receipt=cache.restore(identity,root/'restored')
            self.assertEqual(receipt['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual((root/'restored').read_bytes(),source.read_bytes())
            encrypted=root/'cache'/(identity+'.cache')
            data=bytearray(encrypted.read_bytes());data[-1]^=1;encrypted.write_bytes(data)
            with self.assertRaises(Exception):cache.restore(identity,root/'tampered')

    def test_aborted_generations_leave_no_reusable_cache_entry(self):
        from cache import Cache
        try:import cryptography
        except ImportError:self.skipTest('Run with the NAS cryptography runtime')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.write_bytes(b'private fixture')
            with Cache(root/'cache',root/'key') as cache:cache.stage('f'*64,source)
            self.assertEqual(list((root/'cache').iterdir()),[])
