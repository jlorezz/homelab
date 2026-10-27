import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import configure
import doctor


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        # Immutable release directories need writable parents for test cleanup.
        for directory in self.state.rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o755)
        self.tmp.cleanup()

    def test_apply_is_idempotent_and_check_is_read_only(self):
        before = list(self.state.iterdir())
        self.assertTrue(configure.provision(self.state))
        self.assertEqual(before, list(self.state.iterdir()))
        self.assertTrue(configure.provision(self.state, apply=True))
        self.assertEqual([], configure.provision(self.state))
        self.assertEqual([], configure.provision(self.state, apply=True))
        self.assertFalse((self.state / "hermes").exists())

    def test_retired_worker_state_and_active_provider_are_preserved(self):
        home = self.state / "hermes"
        home.mkdir()
        (home / ".env").write_text("PRIVATE_KEY=do-not-copy\n")
        (home / "SOUL.md").write_text("My existing identity\n")
        (home / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "my-model"}, "discord": {"allowed_users": ["123"]}}))
        retired = {p.name: p.read_bytes() for p in home.iterdir()}
        code = self.state / "t3/.config/opencode/opencode.jsonc"
        code.parent.mkdir(parents=True)
        code.write_text('{//keep values\n"model":"my-provider/model","provider":{"mine":{"options":{"apiKey":"private-value"}}}}')
        configure.provision(self.state, apply=True)
        self.assertEqual(retired, {p.name: p.read_bytes() for p in home.iterdir()})
        self.assertEqual("PRIVATE_KEY=do-not-copy\n", (home / ".env").read_text())
        self.assertEqual("My existing identity\n", (home / "SOUL.md").read_text())
        self.assertEqual("my-model", yaml.safe_load((home / "config.yaml").read_text())["model"]["default"])
        self.assertEqual(["123"], yaml.safe_load((home / "config.yaml").read_text())["discord"]["allowed_users"])
        self.assertEqual("private-value", json.loads(code.read_text())["provider"]["mine"]["options"]["apiKey"])
        self.assertEqual([], configure.provision(self.state))
        self.assertNotIn("private-value", (self.state / "policy/current/manifest.json").read_text())

    def test_opt_in_list_is_complete_and_disables_other_servers(self):
        configure.provision(self.state, apply=True, enabled=["github"])
        code = json.loads((self.state / "t3/.config/opencode/opencode.jsonc").read_text())
        self.assertEqual(["github"], [k for k, v in code["mcp"].items() if v["enabled"]])
        self.assertEqual([], configure.provision(self.state, enabled=["github"]))
        self.assertTrue(configure.provision(self.state, enabled=[]))

    def test_skill_collision_fails_before_writes(self):
        skill = self.state / "t3/.agents/skills/agent-handoff"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("User-owned skill")
        with self.assertRaises(ValueError):
            configure.provision(self.state, apply=True)
        self.assertFalse((self.state / "policy").exists())
        self.assertEqual("User-owned skill", (skill / "SKILL.md").read_text())

    def test_skills_link_for_every_provider(self):
        configure.provision(self.state, apply=True)
        names = sorted(path.name for path in (configure.HERE / "policy/skills").iterdir())
        self.assertIn("full-stop", names)
        for worker in ("t3", "opencode"):
            for directory in (".agents/skills", ".claude/skills"):
                links = self.state / worker / directory
                self.assertEqual(names, sorted(path.name for path in links.iterdir()))
                self.assertEqual("/opt/agent-policy/current/skills/council", str((links / "council").readlink()))

    def test_claude_skill_collision_fails_before_writes(self):
        skill = self.state / "opencode/.claude/skills/council"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("User-owned skill")
        with self.assertRaises(ValueError):
            configure.provision(self.state, apply=True)
        self.assertFalse((self.state / "policy").exists())
        self.assertEqual("User-owned skill", (skill / "SKILL.md").read_text())

    def test_jsonc_preserves_urls_and_comment_like_strings(self):
        self.assertEqual({"url": "https://host/a//b", "x": "/*text*/"}, configure.jsonc('{/*comment*/"url":"https://host/a//b","x":"/*text*/",}'))

    def test_unknown_mcp_is_rejected(self):
        with self.assertRaises(ValueError):
            configure.provision(self.state, apply=True, enabled=["unknown"])
        self.assertFalse((self.state / "policy").exists())

    def test_symlinked_home_config_cannot_redirect_root_provisioning(self):
        elsewhere = self.state / "elsewhere"
        elsewhere.mkdir()
        home = self.state / "t3"
        home.mkdir()
        (home / ".config").symlink_to(elsewhere)
        with self.assertRaises(ValueError):
            configure.provision(self.state, apply=True)
        self.assertEqual([], list(elsewhere.iterdir()))
        self.assertFalse((self.state / "policy").exists())

    def test_corrupt_release_is_detected_and_not_silently_repaired(self):
        configure.provision(self.state, apply=True)
        file = self.state / "policy/current/AGENTS.md"
        file.chmod(0o644)
        file.write_text("tampered")
        self.assertIn("policy content", configure.provision(self.state))
        with self.assertRaises(ValueError):
            configure.provision(self.state, apply=True)

    def test_source_contracts(self):
        self.assertTrue(doctor.source_checks())


if __name__ == "__main__":
    unittest.main()

class KnowledgeConfigurationTests(unittest.TestCase):
    def test_claude_follows_opt_in_without_losing_other_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            try:
                claude = state / 't3/.claude.json'
                claude.parent.mkdir(parents=True)
                claude.write_text(json.dumps({'mcpServers': {'existing': {'command': 'keep-me'}}}))
                configure.provision(state, apply=True, enabled=['shared-brain'])
                self.assertIn('shared-brain', json.loads((state / 't3/.claude.json').read_text())['mcpServers'])
                self.assertIn('/opt/agent-policy/current/AGENTS.md', (state / 't3/.claude/CLAUDE.md').read_text())
                configure.provision(state, apply=True, enabled=[])
                servers = json.loads(claude.read_text())['mcpServers']
                self.assertNotIn('shared-brain', servers)
                self.assertEqual({'command': 'keep-me'}, servers['existing'])
            finally:
                for path in state.rglob('*'):
                    if path.is_dir() and not path.is_symlink(): path.chmod(0o755)
