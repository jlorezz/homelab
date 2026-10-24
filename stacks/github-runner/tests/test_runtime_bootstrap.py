"""Offline missing-runtime repair: ownership, no traversal and no replacement."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_bootstrap as bootstrap


class RuntimeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.source = self.root / 'public-bun'
        self.data = b'public runtime fixture, never executed\n'
        self.source.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.bin = self.home / '.bun/bin'
        self.dest = self.bin / 'bun'

    def seed(self, apply=True):
        return bootstrap.seed(str(self.home), str(self.source), self.digest, apply)

    def dirs(self):
        self.bin.mkdir(parents=True, mode=0o755)

    def test_seed_owned_executable_and_cleanup(self):
        self.assertEqual(self.seed(), 'seeded missing bun')
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.dest.stat().st_uid, os.geteuid())
        self.assertEqual(list(self.bin.iterdir()), [self.dest])

    def test_existing_different_executable_is_preserved_idempotently(self):
        self.dirs()
        self.dest.write_bytes(b'other legitimate version')
        self.dest.chmod(0o755)
        before = self.dest.stat()
        for _ in range(2):
            self.assertEqual(self.seed(), 'preserved existing bun')
        self.assertEqual(self.dest.stat().st_ino, before.st_ino)
        self.assertEqual(self.dest.read_bytes(), b'other legitimate version')

    def test_dry_run_missing_directories_no_mutation(self):
        self.assertEqual(self.seed(False), 'would seed missing bun')
        self.assertEqual(list(self.home.iterdir()), [])

    def test_dry_run_existing_directories_no_mutation(self):
        self.dirs()
        before = self.bin.stat().st_mtime_ns
        self.seed(False)
        self.assertEqual(list(self.bin.iterdir()), [])
        self.assertEqual(before, self.bin.stat().st_mtime_ns)

    def test_cli_defaults_to_dry_run(self):
        result = subprocess.run([sys.executable, bootstrap.__file__, '--home', str(self.home),
                                 '--source', str(self.source), '--sha256', self.digest],
                                capture_output=True, text=True, check=True)
        self.assertIn('would seed', result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_checksum_failure_precedes_any_install(self):
        self.digest = '0' * 64
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.seed()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_source_symlink_refused(self):
        link = self.root / 'source-link'
        link.symlink_to(self.source)
        self.source = link
        with self.assertRaises(OSError):
            self.seed()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_symlink_home_or_ancestor_refused(self):
        for home in (self.root / 'home-link', self.root / 'parent-link/home'):
            if home.name == 'home-link':
                home.symlink_to(self.home)
            else:
                home.parent.symlink_to(self.root)
            with self.assertRaises(OSError):
                bootstrap.seed(str(home), str(self.source), self.digest, True)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_tool_parent_symlinks_refused(self):
        for part in ('.bun', 'bin'):
            with self.subTest(part=part):
                target = self.home / '.bun'
                if part == 'bin':
                    target.mkdir()
                    target = target / 'bin'
                target.symlink_to(self.root)
                with self.assertRaises(OSError):
                    self.seed()
                target.unlink()
                if part == 'bin':
                    target.parent.rmdir()

    def test_existing_symlink_and_nonexecutables_refused(self):
        self.dirs()
        self.dest.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.seed()
        self.dest.unlink()
        self.dest.write_bytes(b'incomplete installer')
        self.dest.chmod(0o644)
        with self.assertRaises(ValueError):
            self.seed()
        self.assertEqual(self.dest.read_bytes(), b'incomplete installer')

    def test_private_home_and_tool_permissions(self):
        self.home.chmod(0o755)
        with self.assertRaises(ValueError):
            self.seed()
        self.home.chmod(0o700)
        self.dirs()
        for directory in (self.bin.parent, self.bin):
            directory.chmod(0o775)
            with self.assertRaises(ValueError):
                self.seed()
            directory.chmod(0o755)

    def test_unsafe_ancestor_refused(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'Unsafe ancestor'):
            self.seed()

    def test_current_uid_required_for_home_and_tool_dirs(self):
        self.dirs()
        real = os.fstat
        for directory in (self.home, self.bin.parent, self.bin):
            inode = directory.stat().st_ino
            def wrong_owner(fd):
                info = real(fd)
                if info.st_ino == inode:
                    values = list(info)
                    values[4] = os.geteuid() + 123
                    return os.stat_result(values)
                return info
            with self.subTest(directory=directory), patch.object(bootstrap.os, 'fstat', side_effect=wrong_owner):
                with self.assertRaises(ValueError):
                    self.seed()

    def test_concurrent_installer_wins_atomic_publish(self):
        real = os.link
        def concurrent(*args, **kwargs):
            self.dest.write_bytes(b'concurrent legitimate version')
            self.dest.chmod(0o755)
            return real(*args, **kwargs)
        with patch.object(bootstrap.os, 'link', side_effect=concurrent):
            self.assertEqual(self.seed(), 'preserved concurrently installed bun')
        self.assertEqual(self.dest.read_bytes(), b'concurrent legitimate version')
        self.assertEqual(list(self.bin.iterdir()), [self.dest])

    def test_temporary_collision_never_unlinks_another_file(self):
        self.dirs()
        collision = self.bin / '.bun-bootstrap-fixed'
        collision.symlink_to(self.source)
        with patch.object(bootstrap.secrets, 'token_hex', return_value='fixed'):
            with self.assertRaises(FileExistsError):
                self.seed()
        self.assertTrue(collision.is_symlink())
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertFalse(self.dest.exists())

    def test_concurrent_symlink_refused_without_removal(self):
        real = os.link
        def concurrent(*args, **kwargs):
            self.dest.symlink_to(self.source)
            return real(*args, **kwargs)
        with patch.object(bootstrap.os, 'link', side_effect=concurrent):
            with self.assertRaises(ValueError):
                self.seed()
        self.assertTrue(self.dest.is_symlink())
        self.assertEqual(list(self.bin.iterdir()), [self.dest])

    def test_copy_failure_cleans_only_own_temporary(self):
        self.dirs()
        other = self.bin / '.bun-bootstrap-other-installer'
        other.write_bytes(b'leave alone')
        with patch.object(bootstrap.os, 'fchmod', side_effect=OSError('copy failure')):
            with self.assertRaises(OSError):
                self.seed()
        self.assertEqual(list(self.bin.iterdir()), [other])

    def test_source_mutation_during_copy_refused(self):
        self.dirs()
        real = os.open
        def mutate(path, flags, *args, **kwargs):
            if str(path).startswith('.bun-bootstrap-'):
                self.source.write_bytes(b'changed after verification')
            return real(path, flags, *args, **kwargs)
        with patch.object(bootstrap.os, 'open', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'Source changed'):
                self.seed()
        self.assertEqual(list(self.bin.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
