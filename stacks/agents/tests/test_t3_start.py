from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml

AGENTS = Path(__file__).resolve().parents[1]


def fake(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


class T3StartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pinned = self.root / "pinned"
        fake(self.pinned, 'echo "pinned $*"')
        self.local = self.root / "local-t3"

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, *args):
        env = {**os.environ, "T3_PINNED": str(self.pinned), "T3_LOCAL_BIN": str(self.local), "T3_CHECK_TIMEOUT": "5"}
        return subprocess.run(["sh", str(AGENTS / "t3-start.sh"), *args], env=env, capture_output=True, text=True, check=True)

    def test_working_user_install_is_preferred(self):
        fake(self.local, '[ "$1" = --version ] && echo "t3 v9" || echo "local $*"')
        result = self.start("serve", "--port", "3773")
        self.assertEqual("local serve --port 3773\n", result.stdout)
        self.assertIn("using user-managed t3 v9", result.stderr)

    def test_broken_user_install_falls_back_to_pin(self):
        fake(self.local, "exit 1")
        result = self.start("serve")
        self.assertEqual("pinned serve\n", result.stdout)
        self.assertIn("failed its version check", result.stderr)

    def test_missing_user_install_uses_pin(self):
        self.assertEqual("pinned serve\n", self.start("serve").stdout)

    def test_update_rejects_unsafe_versions(self):
        for value in ("1;rm -rf /", "$(id)", "a b", "--prefix=/"):
            result = subprocess.run(["sh", str(AGENTS / "update-t3.sh"), value], capture_output=True, text=True,
                                    env={**os.environ, "AGENT_WORKLOAD": "/bin/false"})
            self.assertEqual(2, result.returncode, value)

    def test_update_installs_requested_channel_into_prefix(self):
        log = self.root / "log"
        workload = self.root / "agent-workload"
        prefix = self.root / "prefix"
        (prefix / "bin").mkdir(parents=True)
        fake(prefix / "bin/t3", 'echo "t3 v9"')
        fake(workload, f'echo "$*" > {log}')
        env = {**os.environ, "AGENT_WORKLOAD": str(workload), "T3_PREFIX": str(prefix)}
        result = subprocess.run(["sh", str(AGENTS / "update-t3.sh"), "latest"], env=env, capture_output=True, text=True, check=True)
        self.assertIn(f"npm install --global --prefix {prefix} t3@latest", log.read_text())
        self.assertIn("Installed t3 v9", result.stdout)

    def test_compose_and_image_start_through_wrapper(self):
        service = yaml.safe_load((AGENTS / "compose.yaml").read_text())["services"]["t3"]
        self.assertEqual("t3-start", service["command"][0])
        self.assertEqual("/home/agent/.local/bin:/bin", service["environment"]["PATH"])
        image = (AGENTS.parents[1] / "packages/agent-image.nix").read_text()
        self.assertIn('"${t3Start}/bin/t3-start"', image)


if __name__ == "__main__":
    unittest.main()
