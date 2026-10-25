"""Root-free tests for the agent-deploy dispatcher's allowlist, arm and audit."""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_deploy as ad  # noqa: E402

SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64
CONTAINER = "c" * 64


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = {
            "user": "agent-deploy",
            "source": str(Path(__file__).resolve().parents[3]),
            "stateDir": str(root / "state"),
            "auditLog": str(root / "log" / "audit.log"),
            "path": os.environ.get("PATH", "/usr/bin:/bin"),
            "sudo": "/run/wrappers/bin/sudo",
            "repository": "https://example.invalid/homelab.git",
            "toplevelAttr": "x",
            "productionUnit": str(root / "live" / "dealpipe-deploy-production.service"),
            "healthUnits": [],
            "confirmMinutes": 15,
            "heavyLock": str(root / "heavy.lock"),
            "ciPolicy": str(root / "policy.json"),
        }
        self.config_file = root / "config.json"
        self.config_file.write_text(json.dumps(self.config))
        self.d = ad.Dispatcher(self.config)

    def tearDown(self):
        self.tmp.cleanup()


class Allowlist(Base):
    def test_accepts_exact_actions(self):
        self.assertEqual(ad.parse_request("inventory"), ("inventory", []))
        self.assertEqual(ad.parse_request(f"nix-build {SHA}"), ("nix-build", [SHA]))
        self.assertEqual(ad.parse_request(f"image-check {IMAGE}"), ("image-check", [IMAGE]))
        self.assertEqual(ad.parse_request("image-build preview"), ("image-build", ["preview"]))
        self.assertEqual(ad.parse_request(f"static2-dryrun {CONTAINER}"), ("static2-dryrun", [CONTAINER]))

    def test_refuses_shell_syntax_and_free_form(self):
        for raw in [
            None, "", "inventory; id", "inventory && id", "inventory | sh", "$(id)", "`id`",
            "inventory 'x'", 'inventory "x"', "INVENTORY", "inventory\nid", "bash", "sh -c id",
            f"nix-build {SHA} --impure", f"nix-build {SHA[:-1]}", f"nix-build {SHA.upper()}",
            "image-build deploy preview", "image-build other", f"image-check {IMAGE}x",
            "nixos-switch ../../etc", "inventory extra", "help me", "x" * 201,
        ]:
            with self.subTest(raw=raw), self.assertRaises(ad.Refused):
                ad.parse_request(raw)

    def test_every_mutating_action_needs_arm(self):
        armed = {name for name, spec in ad.ACTIONS.items() if spec[1]}
        self.assertEqual(armed, {"nixos-switch", "image-build", "static2-policy", "static2-apply",
                                 "ci2-canary-apply"})

    def test_every_action_has_an_implementation(self):
        for name in ad.ACTIONS:
            if name in ad.LOCAL_ONLY:
                continue
            self.assertTrue(hasattr(ad.Dispatcher, "do_" + name.replace("-", "_")), name)


class Arm(Base):
    def test_single_use_and_action_bound(self):
        record = self.d.arm("nixos-switch", 30)
        self.assertEqual(oct(Path(self.d.arm_file).stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.d.consume_arm("nixos-switch"), record["nonce"])
        with self.assertRaises(ad.Refused):
            self.d.consume_arm("nixos-switch")

    def test_wrong_action_disarms(self):
        self.d.arm("image-build", 5)
        with self.assertRaises(ad.Refused):
            self.d.consume_arm("nixos-switch")
        self.assertFalse(self.d.arm_file.exists())

    def test_expiry_and_limits(self):
        self.d.arm("static2-apply", 1, now=time.time() - 120)
        with self.assertRaises(ad.Refused):
            self.d.consume_arm("static2-apply")
        for minutes in (0, 31):
            with self.assertRaises(ad.Refused):
                self.d.arm("nixos-switch", minutes)
        with self.assertRaises(ad.Refused):
            self.d.arm("inventory", 10)  # read-only actions are never armed
        with self.assertRaises(ad.Refused):
            self.d.arm("rm-rf", 10)

    def test_corrupt_arm_disarms(self):
        self.d.state.mkdir(parents=True, exist_ok=True)
        self.d.arm_file.write_text("{not json")
        with self.assertRaises(ad.Refused):
            self.d.consume_arm("nixos-switch")
        self.assertFalse(self.d.arm_file.exists())


class RunRoot(Base):
    def test_unarmed_mutation_is_refused_and_audited(self):
        with self.assertRaises(ad.Refused):
            self.d.run_root("static2-policy", [], client="172.18.0.5")
        lines = [json.loads(line) for line in Path(self.config["auditLog"]).read_text().splitlines()]
        self.assertEqual([line["event"] for line in lines], ["refused"])  # audited, nothing ran
        self.assertIn("arm", lines[0]["detail"])

    def test_audit_records_start_end_and_output_hash(self):
        with mock.patch.object(ad.Dispatcher, "do_status", lambda self, a, t, d: d.update(b"hello")):
            self.d.run_root("status", [], client="172.18.0.5")
        lines = [json.loads(line) for line in Path(self.config["auditLog"]).read_text().splitlines()]
        self.assertEqual([line["event"] for line in lines], ["start", "end"])
        self.assertEqual(lines[1]["status"], "ok")
        self.assertEqual(lines[1]["outputSha256"], hashlib.sha256(b"hello").hexdigest())
        self.assertEqual(lines[0]["client"], "172.18.0.5")

    def test_armed_action_consumes_arm_and_logs_nonce(self):
        nonce = self.d.arm("image-build", 10)["nonce"]
        with mock.patch.object(ad.Dispatcher, "do_image_build", lambda self, a, t, d: None):
            self.d.run_root("image-build", ["deploy"])
        self.assertFalse(self.d.arm_file.exists())
        end = json.loads(Path(self.config["auditLog"]).read_text().splitlines()[-1])
        self.assertEqual(end["arm"], nonce)

    def test_canary_token_file_is_single_use_even_on_failure(self):
        self.d.state.mkdir(parents=True, exist_ok=True)
        token = self.d.state / "registration-token"
        token.write_text("not-a-real-token\n")
        self.d.arm("ci2-canary-apply", 10)

        def failing_run(self_, argv, timeout, stdin=None, env=None, digest=None):
            raise ad.Refused("simulated failure")

        with mock.patch.object(ad.Dispatcher, "_run", failing_run), self.assertRaises(ad.Refused):
            self.d.run_root("ci2-canary-apply", [IMAGE])
        self.assertFalse(token.exists())
        self.assertNotIn("not-a-real-token", Path(self.config["auditLog"]).read_text())

    def test_static2_policy_refuses_unexpired_policy(self):
        Path(self.config["ciPolicy"]).write_text(json.dumps(
            {"expiresAt": time.time() + 3600, "profiles": {}, "maxConcurrentMiB": 20000}))
        self.d.arm("static2-policy", 5)
        with self.assertRaises(ad.Refused):
            self.d.run_root("static2-policy", [])

    def test_static2_policy_edits_expired_policy_with_backup(self):
        path = Path(self.config["ciPolicy"])
        path.write_text(json.dumps({"expiresAt": 1, "profiles": {"static": {}}, "maxConcurrentMiB": 20000}))
        self.d.arm("static2-policy", 5)
        with redirect_stdout(io.StringIO()):
            self.d.run_root("static2-policy", [])
        data = json.loads(path.read_text())
        self.assertEqual(data["profiles"]["static-2"], {"memoryMiB": 12288, "cpuPercent": 200})
        self.assertEqual(data["expiresAt"], 1)
        self.assertEqual(len(list(path.parent.glob("policy.json.before-static2-*"))), 1)


class Switch(Base):
    def test_switch_needs_build_and_no_pending(self):
        self.d.arm("nixos-switch", 5)
        with self.assertRaises(ad.Refused):
            self.d.run_root("nixos-switch", [SHA])
        self.d.state.mkdir(parents=True, exist_ok=True)
        self.d.pending_file.write_text(json.dumps({"sha": SHA, "old": "/o", "new": "/n", "until": 0}))
        self.d.arm("nixos-switch", 5)
        with self.assertRaises(ad.Refused):
            self.d.run_root("nixos-switch", [SHA])

    def test_precheck_refusal_keeps_the_arm(self):
        self.d.arm("nixos-switch", 5)
        with self.assertRaises(ad.Refused):
            self.d.run_root("nixos-switch", [SHA])  # no build yet: precheck refuses
        self.assertTrue(self.d.arm_file.exists())
        self.d.state.joinpath("builds").mkdir(parents=True, exist_ok=True)
        self.d.state.joinpath("builds", SHA).mkdir()
        with mock.patch.object(ad.Dispatcher, "_workers_running", return_value=True), \
                self.assertRaises(ad.Refused):
            self.d.run_root("nixos-switch", [SHA])
        self.assertTrue(self.d.arm_file.exists())
        lines = [json.loads(line) for line in Path(self.config["auditLog"]).read_text().splitlines()]
        self.assertEqual([line["event"] for line in lines], ["refused", "refused"])

    def test_production_unit_must_be_identical(self):
        live = Path(self.config["productionUnit"])
        live.parent.mkdir(parents=True)
        live.write_text("live unit\n")
        build = Path(self.tmp.name) / "toplevel"
        unit_dir = build / "etc" / "systemd" / "system"
        unit_dir.mkdir(parents=True)
        (unit_dir / live.name).write_text("live unit\n")
        self.d._production_unit_unchanged(build)
        (unit_dir / live.name).write_text("changed\n")
        with self.assertRaises(ad.Refused):
            self.d._production_unit_unchanged(build)
        (unit_dir / live.name).unlink()
        with self.assertRaises(ad.Refused):  # absent from the new generation = drift, refuse
            self.d._production_unit_unchanged(build)

    def test_confirm_and_rollback_need_pending(self):
        for action, args in (("nixos-confirm", [SHA]), ("nixos-rollback", [])):
            with self.subTest(action=action), self.assertRaises(ad.Refused):
                self.d.run_root(action, args)


class Main(Base):
    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(out), redirect_stderr(err):
            code = ad.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_ssh_mode_execs_exact_sudo_argv(self):
        with mock.patch.object(ad.os, "execv") as execv:
            self.run_main(["--config", str(self.config_file), "--root-helper", "/nix/store/x-agent-deploy-root",
                           "ssh"], {"SSH_ORIGINAL_COMMAND": f"nix-build {SHA}",
                                    "SSH_CONNECTION": "172.18.0.5 5555 172.18.0.1 22"})
        execv.assert_called_once_with("/run/wrappers/bin/sudo", [
            "/run/wrappers/bin/sudo", "-n", "/nix/store/x-agent-deploy-root", "172.18.0.5", "nix-build", SHA])

    def test_ssh_mode_refuses_bad_command_without_exec(self):
        with mock.patch.object(ad.os, "execv") as execv:
            code, _out, err = self.run_main(
                ["--config", str(self.config_file), "--root-helper", "/x", "ssh"],
                {"SSH_ORIGINAL_COMMAND": "inventory; rm -rf /"})
        self.assertEqual(code, 2)
        self.assertIn("allowlist", err)
        execv.assert_not_called()

    def test_ssh_help_is_local(self):
        with mock.patch.object(ad.os, "execv") as execv:
            code, out, _ = self.run_main(["--config", str(self.config_file), "--root-helper", "/x", "ssh"],
                                         {"SSH_ORIGINAL_COMMAND": "help"})
        self.assertEqual(code, 0)
        self.assertIn("nixos-switch", out)
        execv.assert_not_called()

    def test_root_mode_requires_root_and_cannot_be_reconfigured(self):
        if os.geteuid() == 0:
            self.skipTest("must run unprivileged")
        code, _out, err = self.run_main(
            ["--config", str(self.config_file), "root", "--config", "/tmp/evil.json", "inventory"], {})
        self.assertEqual(code, 2)
        self.assertIn("require root", err)

    def test_trailing_options_stay_in_remainder(self):
        parser_args = ["--config", "c.json", "root", "--config", "evil", "inventory"]
        with mock.patch.object(ad.os, "geteuid", return_value=0), \
                mock.patch.dict(os.environ, {"SUDO_USER": "agent-deploy"}), \
                mock.patch.object(ad.Path, "read_text", return_value=json.dumps(self.config)), \
                mock.patch.object(ad.Dispatcher, "run_root") as run_root:
            code = ad.main(parser_args)
        # "--config evil" landed in rest and was rejected as a malformed client field.
        self.assertEqual(code, 2)
        run_root.assert_not_called()

    def test_root_mode_rejects_other_sudo_users(self):
        with mock.patch.object(ad.os, "geteuid", return_value=0), \
                mock.patch.dict(os.environ, {"SUDO_USER": "jlorezz"}), \
                mock.patch.object(ad.Dispatcher, "run_root") as run_root, \
                redirect_stderr(io.StringIO()):
            code = ad.main(["--config", str(self.config_file), "root", "172.18.0.5", "inventory"])
        self.assertEqual(code, 2)
        run_root.assert_not_called()


if __name__ == "__main__":
    unittest.main()
