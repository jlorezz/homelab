"""Offline source-script guards only: every command is mocked; no mount/unshare.

The Nix literal is adapted to fixed temporary paths at test generation time.
There is no deployed environment override for paths or guard commands.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
NIX = ROOT / 'machines/nas/modules/github-runner-ci2.nix'


class NamespaceGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'dealpipe-ci'
        self.backing = self.base / '.ro-test'
        for name in ('docker', 'work', 'ci2/docker', 'ci2/work'):
            path = self.backing / name
            path.mkdir(parents=True)
            (path / 'sentinel').write_text(name)
        for name in ('docker', 'work', 'ci2'):
            (self.base / name).symlink_to('.ro-test/' + name)
        self.log = self.root / 'commands'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        mock = self.root / 'mock'
        mock.write_text('#!' + sys.executable + '\n' + r'''
import os, pathlib, sys
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['MOCK_LOG'], 'a') as f: f.write(command + ' ' + ' '.join(args) + '\n')
if command == os.environ.get('MOCK_FAIL_COMMAND'): sys.exit(91)
if command == 'id': print(os.environ.get('MOCK_UID', '0'))
elif command == 'readlink':
    path = args[0]
    if path.startswith('/proc/'):
        ns = path.rsplit('/', 1)[-1]
        print(ns + (':[1]' if os.environ.get('MOCK_HOST_NS') == ns else ':[2]'))
    else: print(os.readlink(path))
elif command == 'findmnt':
    if 'TARGET,FSTYPE' in args: print(os.environ['MOCK_BASE'] + ' ' + os.environ.get('MOCK_FS', 'tmpfs'))
    elif 'PROPAGATION' in args: print(os.environ.get('MOCK_PROPAGATION', 'slave'))
elif command == 'stat':
    print('123:999' if os.environ.get('MOCK_MISMATCH') and '/ci2/' not in args[-1] else '123:456')
elif command in ('unlink', 'mkdir', 'mount', 'dockerd'): pass  # NEVER invoke real mutation commands
else: sys.exit(90)
''')
        mock.chmod(0o700)
        for command in ('id', 'readlink', 'findmnt', 'stat', 'unlink', 'mkdir', 'mount', 'dockerd'):
            (self.bin / command).symlink_to(mock)
        source = NIX.read_text().split('namespaceScript = \'\'\n', 1)[1].split("\n  '';", 1)[0]
        self.source = source
        source = source.replace("''${", '${').replace('/var/lib/dealpipe-ci', str(self.base))
        source = re.sub(r'\$\{(?:pkgs.coreutils|pkgs.util-linux|docker)\}/bin/', str(self.bin) + '/', source)
        self.script = self.root / 'script'
        self.script.write_text(source)
        self.env = dict(os.environ, MOCK_LOG=str(self.log), MOCK_BASE=str(self.base), _DOCKERD_ROOTLESS_CHILD='1',
                        CI2_PARENT_USER_NS='user:[1]', CI2_PARENT_MNT_NS='mnt:[1]')

    def run_script(self):
        return subprocess.run(['bash', str(self.script), '--config-file=fixture'], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def refused(self, reason):
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(reason, result.stderr)
        commands = self.log.read_text().splitlines() if self.log.exists() else []
        self.assertFalse(any(line.split()[0] in ('unlink', 'mkdir', 'mount', 'dockerd') for line in commands), commands)
        for name in ('docker', 'work'):
            self.assertEqual((self.backing / name / 'sentinel').read_text(), name)

    def test_accidental_host_invocation(self):
        self.env.pop('_DOCKERD_ROOTLESS_CHILD')
        self.refused('not a RootlessKit child')

    def test_real_uid_required(self):
        self.env['MOCK_UID'] = '1102'
        self.refused('real UID')

    def test_both_namespaces_must_differ(self):
        for ns in ('user', 'mnt'):
            with self.subTest(ns=ns):
                self.env['MOCK_HOST_NS'] = ns
                self.refused('host ' + ns + ' namespace')

    def test_copyup_mount_required(self):
        self.env['MOCK_FS'] = 'ext4'
        self.refused('missing copy-up tmpfs')

    def test_missing_or_invalid_parent_snapshot_refuses_before_mutation(self):
        for field in ('CI2_PARENT_USER_NS', 'CI2_PARENT_MNT_NS'):
            saved = self.env.pop(field)
            self.refused('invalid parent')
            self.env[field] = 'unverified'
            self.refused('invalid parent')
            self.env[field] = saved

    def test_shared_propagation_refused(self):
        self.env['MOCK_PROPAGATION'] = 'shared,slave'
        self.refused('outward mount propagation')

    def test_real_directories_never_removed_even_with_spoofed_marker(self):
        # Validate both entries before changing either one, including second-entry failure.
        for name in ('docker', 'work'):
            with self.subTest(name=name):
                path = self.base / name
                path.unlink()
                path.mkdir()
                (path / 'host-sentinel').write_text('untouched')
                self.refused('not a copy-up symlink')
                self.assertEqual((path / 'host-sentinel').read_text(), 'untouched')
                (path / 'host-sentinel').unlink()
                path.rmdir()
                path.symlink_to('.ro-test/' + name)

    def test_unexpected_copyup_target(self):
        (self.base / 'work').unlink()
        (self.base / 'work').symlink_to('.ro-other/work')
        self.refused('unexpected work copy-up target')

    def test_physical_source_must_be_directory(self):
        (self.backing / 'ci2/work/sentinel').unlink()
        (self.backing / 'ci2/work').rmdir()
        (self.backing / 'ci2/work').symlink_to(self.backing / 'work')
        self.refused('invalid physical work source')

    def test_failed_mount_or_identity_check_never_execs_daemon(self):
        for command in ('mount', 'stat'):
            with self.subTest(command=command):
                self.env['MOCK_FAIL_COMMAND'] = command
                self.log.unlink(missing_ok=True)
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('dockerd ', self.log.read_text())

    def test_mismatched_bind_identity_never_execs_daemon(self):
        self.env['MOCK_MISMATCH'] = '1'
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('bind identity mismatch', result.stderr)
        self.assertNotIn('dockerd ', self.log.read_text())

    def test_mocked_success_command_contract(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        for name in ('docker', 'work'):
            self.assertIn(f'unlink {self.base}/{name}', commands)
            self.assertIn(f'mount --bind {self.base}/ci2/{name} {self.base}/{name}', commands)
            self.assertIn(f'mount --make-private {self.base}/{name}', commands)
        self.assertEqual(commands[-1], 'dockerd --config-file=fixture')
        # Actual files were never mutated by the mocked commands.
        self.assertTrue((self.base / 'docker').is_symlink())
        self.assertNotRegex(self.source, r'\brm\b')
        self.assertNotIn('eval ', self.source)
        self.assertIn('stat -Lc', self.source)


if __name__ == '__main__':
    unittest.main()
