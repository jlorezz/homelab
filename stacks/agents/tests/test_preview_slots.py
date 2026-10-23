import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('nas_preview_run',Path(__file__).parents[1]/'previews/run.py')
preview=importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

class PreviewSlotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.state=self.root/'state'
        self.state.mkdir()
        self.patcher=patch.object(preview,'STATE',self.state);self.patcher.start()
        self.config_patcher=patch.object(preview,'CONFIG',self.state);self.config_patcher.start()
        for slot in (1,2):(self.state/f'{slot}.json').write_text(json.dumps({'slot':slot}))
    def tearDown(self):self.config_patcher.stop();self.patcher.stop();self.temp.cleanup()
    def test_status_does_not_allocate(self):
        self.assertIsNone(preview.claim(self.root/'a',create=False))
        self.assertFalse((self.state/'claims.json').exists())
    def test_worktree_ownership_persists_and_capacity_does_not_reassign_data(self):
        self.assertEqual(preview.claim(self.root/'a')['slot'],1)
        self.assertEqual(preview.claim(self.root/'a')['slot'],1)
        self.assertEqual(preview.claim(self.root/'b')['slot'],2)
        with self.assertRaisesRegex(RuntimeError,'Both NAS preview backends'):preview.claim(self.root/'c')
        self.assertEqual(preview.claim(self.root/'a')['slot'],1)
    def test_existing_environment_is_preserved(self):
        (self.root/'.env').write_text('USER_SETTING=preserve\n')
        config={'slot':1,'origin':'https://nas.example.ts.net:4101','proxy':{},'env':{'DATABASE_URL':'postgresql://local/preview','KV_REST_API_URL':'http://redis:80','KV_REST_API_TOKEN':'fixture'}}
        with self.assertRaisesRegex(RuntimeError,'not owned'):preview.environment(config,self.root)
        self.assertEqual((self.root/'.env').read_text(),'USER_SETTING=preserve\n')
    def test_preview_environment_does_not_inherit_hosted_credentials(self):
        config={'slot':1,'origin':'https://nas.example.ts.net:4101','proxy':{},'env':{'DATABASE_URL':'postgresql://local/preview','KV_REST_API_URL':'http://redis:80','KV_REST_API_TOKEN':'fixture'}}
        with patch.dict(os.environ,{'STRIPE_SECRET_KEY':'do-not-copy','SUPABASE_ACCESS_TOKEN':'do-not-copy'}):env=preview.environment(config,self.root)
        self.assertNotIn('STRIPE_SECRET_KEY',env);self.assertNotIn('SUPABASE_ACCESS_TOKEN',env)
        self.assertEqual((self.root/'.env').stat().st_mode & 0o777,0o600)
