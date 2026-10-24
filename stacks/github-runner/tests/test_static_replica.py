"""Offline safety regression tests. No engine, host service or network access."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('static_replica', Path(__file__).parents[1] / 'static-replica.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class ReplicaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls, self.containers = [], {}
        self.token = 'short-lived-SECRET'
        self.info = {'SecurityOptions': ['name=rootless']}
        self.source = {'Name': '/' + m.SOURCE, 'Image': m.IMAGE, 'State': {'Running': True},
                       'Config': {'Labels': {'com.docker.compose.service': 'static'},
                                  'Env': ['RUNNER_LABEL=dealpipe-nas-static'], 'User': '0:0',
                                  'Entrypoint': ['/usr/local/bin/ci-runner']},
                       'HostConfig': {'NetworkMode': 'host'}, 'Mounts': []}
        self.containers[m.SOURCE] = self.source
        self.busy = set()
        self.fail = None
        for key, value in {'ROOT': self.root / 'static-2', 'POOL': self.root / 'pool',
                           'LOCK': self.root / 'heavy.lock'}.items():
            patcher = patch.object(m, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        m.LOCK.touch()
        m.POOL.mkdir()
        for name, value in {'memory.max': str(32*m.GIB), 'memory.swap.max': '0', 'cpu.max': '800000 100000',
                            'memory.current': str(26*m.GIB),
                            'memory.stat': f'anon {4*m.GIB}\nfile {22*m.GIB}\nkernel {m.GIB}\n',
                            'memory.events': 'oom 0\noom_kill 0\n'}.items():
            (m.POOL / name).write_text(value)
        self.meminfo = 'MemTotal: 48597644 kB\nMemAvailable: 34603008 kB\n'
        original_read = Path.read_text
        def read(path, *args, **kwargs):
            if str(path) == '/proc/meminfo':
                return self.meminfo
            return original_read(path, *args, **kwargs)
        original_stat = Path.stat
        def filesystem_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            # Temp dirs model the CI account's private local state.
            values = list(result)
            values[4] = 1102
            return type(result)(values)
        for patcher in (patch.object(m.os, 'geteuid', return_value=1102),
                        patch.object(Path, 'read_text', read), patch.object(Path, 'stat', filesystem_stat),
                        patch.object(m.subprocess, 'run', side_effect=self.engine)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def replica(self, name):
        return {'Name': '/' + name, 'Image': m.IMAGE, 'State': {'Running': True},
                'Mounts': [{'Source': k, 'Destination': v, 'Type': 'bind', 'RW': True}
                           for k, v in m.mounts().items()],
                'Config': {'Labels': {m.LABEL: m.NAME, 'com.docker.compose.project': 'dealpipe-ci',
                                     'com.docker.compose.service': 'static-2'},
                           'Env': [f'{k}={v}' for k, v in m.ENV.items()],
                           'User': '0:0', 'Entrypoint': ['/usr/local/bin/ci-runner']},
                'HostConfig': {'NetworkMode': 'host', 'Privileged': False, 'Memory': 8*m.GIB,
                               'MemorySwap': 8*m.GIB, 'NanoCpus': 2_000_000_000, 'PidsLimit': 2048,
                               'Init': True, 'RestartPolicy': {'Name': 'unless-stopped'}}}

    def engine(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[:2], ['docker', '--host=unix:///run/user/1102/docker.sock'])
        self.assertNotIn(self.token, ' '.join(argv))
        self.assertNotIn(self.token, json.dumps(kwargs['env']))
        args, output = argv[2:], ''
        if args[:1] == ['info']:
            output = json.dumps(self.info)
        elif args[:2] == ['container', 'ls']:
            output = '\n'.join(self.containers)
        elif args[:2] == ['container', 'inspect']:
            output = json.dumps([self.containers[args[2]]])
        elif args[:2] == ['image', 'inspect']:
            output = json.dumps([{'Config': {}}])
        elif args[0] == 'top':
            output = 'Runner.Worker' if args[1] in self.busy else 'Runner.Listener'
        elif args[0] in ('run', 'create'):
            name = args[args.index('--name')+1]
            self.containers[name] = self.replica(name)
            for i, arg in enumerate(args):
                if arg == '--label':
                    key, value = args[i+1].split('=', 1)
                    self.containers[name]['Config']['Labels'][key] = value
            self.containers[name]['State']['Running'] = args[0] == 'run'
            if args[0] == 'run':
                self.assertEqual(kwargs['input'], self.token+'\n')
                self.assertIn('--log-driver=none', args)
                if self.fail == 'configure':
                    return SimpleNamespace(returncode=1, stdout=self.token, stderr=self.token)
                (m.ROOT / 'runner' / '.runner').write_text('registration metadata')
                del self.containers[name]
        elif args[0] == 'start':
            self.containers[m.NAME]['State']['Running'] = True
            if self.fail == 'start':
                return SimpleNamespace(returncode=1, stdout=self.token, stderr=self.token)
        elif args[0] == 'rm':
            del self.containers[args[-1]]
        elif args[0] != 'stop':
            self.failTest(f'Unexpected operation: {args}')
        return SimpleNamespace(returncode=0, stdout=output, stderr='')

    def execute(self, apply=False, rollback=False):
        with patch.object(m.sys, 'stdin', io.StringIO(self.token+'\n')), patch('sys.stdout', io.StringIO()):
            m.execute(SimpleNamespace(expected_image=m.IMAGE, apply=apply, rollback=rollback))

    def mutations(self):
        return [a[2:] for a, _ in self.calls if a[2] in ('run', 'create', 'start', 'stop', 'rm')]

    def test_dry_run_read_only(self):
        self.execute()
        self.assertFalse(m.ROOT.exists())
        self.assertEqual(self.mutations(), [])

    def test_apply_exact_mounts_caps_shared_hook_and_idempotency(self):
        before = m.LOCK.stat().st_ino
        self.execute(apply=True)
        self.assertEqual(m.LOCK.stat().st_ino, before)
        self.assertTrue(m.ROOT.joinpath('runner/.runner').exists())
        for args in self.mutations():
            if args[0] in ('run', 'create'):
                binds = [args[i+1] for i, v in enumerate(args) if v == '--mount']
                self.assertEqual(binds, [f'type=bind,src={k},dst={v}' for k, v in m.mounts().items()])
                self.assertFalse(any('.sock' in b or 'registration-token' in b for b in binds))
                for flag in ('--memory=8g', '--memory-swap=8g', '--cpus=2', '--pids-limit=2048',
                             '--pull=never', '--user=0:0', 'AGENT_CI_EXCLUSIVE=0'):
                    self.assertIn(flag, args)
        self.calls.clear()
        self.execute(apply=True)
        self.assertEqual(self.mutations(), [])

    def test_wrong_rootless_uid_source_image_socket_and_caps(self):
        cases = [lambda: self.info.update(SecurityOptions=[]),
                 lambda: self.source.update(Image='sha256:'+'0'*64),
                 lambda: self.source['Config'].update(User='1102'),
                 lambda: self.source.update(Mounts=[{'Source': '/docker.sock', 'Destination': '/socket'}]),
                 lambda: (m.POOL/'memory.max').write_text(str(64*m.GIB)),
                 lambda: (m.POOL/'cpu.max').write_text('900000 100000'),
                 lambda: (m.POOL/'memory.swap.max').write_text(str(m.GIB)),
                 lambda: (m.POOL/'memory.events').write_text('oom 1\noom_kill 0\n')]
        for change in cases:
            old_source, old_info = copy.deepcopy(self.source), copy.deepcopy(self.info)
            files = {p: p.read_text() for p in m.POOL.iterdir()}
            change()
            with self.assertRaises((RuntimeError, FileNotFoundError)):
                self.execute(apply=True)
            self.assertEqual(self.mutations(), [])
            self.source.clear(); self.source.update(old_source)
            self.info.clear(); self.info.update(old_info)
            for path, value in files.items():
                path.write_text(value)
        with patch.object(m.os, 'geteuid', return_value=0), self.assertRaises(RuntimeError):
            self.execute()

    def test_memory_gates_include_anon_kernel_and_available_reserve(self):
        (m.POOL/'memory.stat').write_text(f'anon {25*m.GIB}\nfile {m.GIB}\nkernel {m.GIB}\n')
        with self.assertRaisesRegex(RuntimeError, 'headroom'):
            self.execute()
        (m.POOL/'memory.stat').write_text(f'anon {4*m.GIB}\nfile {22*m.GIB}\nkernel {m.GIB}\n')
        self.meminfo = 'MemTotal: 48597644 kB\nMemAvailable: 8388608 kB\n'
        with self.assertRaisesRegex(RuntimeError, 'MemAvailable'):
            self.execute()
        self.meminfo = 'MemTotal: 33554432 kB\nMemAvailable: 33554432 kB\n'
        with self.assertRaisesRegex(RuntimeError, 'Physical memory'):
            self.execute()

    def test_refuse_unknown_container_state_and_mounts(self):
        self.containers[m.NAME] = self.replica(m.NAME)
        del self.containers[m.NAME]['Config']['Labels'][m.LABEL]
        with self.assertRaisesRegex(RuntimeError, 'Unknown container'):
            self.execute(apply=True)
        del self.containers[m.NAME]
        m.ROOT.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, 'Unknown previous state'):
            self.execute(apply=True)
        self.assertEqual(self.mutations(), [])

    def test_source_idle_only_before_configure(self):
        self.busy.add(m.SOURCE)
        with self.assertRaisesRegex(RuntimeError, 'Runner.Worker'):
            self.execute(apply=True)
        self.assertFalse(any(a[0] == 'run' for a in self.mutations()))
        self.assertTrue(all(m.SOURCE not in a for a in self.mutations()))

    def test_config_or_start_failure_cleans_only_new_container_retains_state(self):
        for failure in ('configure', 'start'):
            self.fail = failure
            with self.assertRaises(RuntimeError):
                self.execute(apply=True)
            self.assertEqual(set(self.containers), {m.SOURCE})
            self.assertTrue(m.ROOT.is_dir())
            self.assertTrue(all(m.SOURCE not in a for a in self.mutations()))
            if failure == 'start':
                self.assertTrue((m.ROOT/'runner/.runner').exists())

    def test_rollback_refuses_busy_unknown_mounts_then_removes_only_idle_owned(self):
        self.execute(apply=True)
        self.calls.clear()
        self.busy.add(m.NAME)
        with self.assertRaisesRegex(RuntimeError, 'Runner.Worker'):
            self.execute(rollback=True)
        self.assertEqual(self.mutations(), [])
        self.busy.clear()
        self.containers[m.NAME]['Mounts'].append({'Source': '/bad', 'Destination': '/bad', 'Type': 'bind', 'RW': True})
        with self.assertRaisesRegex(RuntimeError, 'Unknown mounts'):
            self.execute(rollback=True)
        self.containers[m.NAME]['Mounts'].pop()
        # Insufficient available capacity should never prevent removing an idle replica.
        (m.POOL/'memory.max').write_text('1')
        del self.containers[m.SOURCE]  # Source health must not block safe rollback.
        self.execute(rollback=True)
        self.assertEqual(self.mutations(), [['stop', m.NAME], ['rm', m.NAME]])
        self.assertTrue(m.ROOT.is_dir())

    def test_symlink_and_private_directory_mode_refused(self):
        m.ROOT.symlink_to(self.root / 'unknown')
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            self.execute(apply=True)
        m.ROOT.unlink()
        self.execute(apply=True)
        (m.ROOT / 'home').chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'ownership/mode'):
            self.execute()

    def test_changed_oom_during_config_cleans_new_state_without_start(self):
        original = self.engine
        def engine(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[2] == 'run':
                (m.POOL / 'memory.events').write_text('oom 1\noom_kill 1\n')
            return result
        with patch.object(m.subprocess, 'run', side_effect=engine), self.assertRaises(RuntimeError):
            self.execute(apply=True)
        self.assertEqual(set(self.containers), {m.SOURCE})
        self.assertFalse(any(a[0] == 'start' for a in self.mutations()))

    def test_concurrent_helper_cannot_remove_another_attempt(self):
        m.state(m.IMAGE, create=True)
        with (m.ROOT / '.static-replica.json').open() as lease:
            m.fcntl.flock(lease, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.execute(apply=True)
        self.assertEqual(self.mutations(), [])

    def test_post_start_busy_job_is_retained_on_failed_verification(self):
        original = self.engine
        def engine(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[2] == 'start':
                self.busy.add(m.NAME)
                (m.POOL / 'memory.events').write_text('oom 1\noom_kill 0\n')
            return result
        with patch.object(m.subprocess, 'run', side_effect=engine), \
             patch('sys.stderr', io.StringIO()) as stderr, self.assertRaises(RuntimeError):
            self.execute(apply=True)
        self.assertIn('rollback gated', stderr.getvalue())
        self.assertIn(m.NAME, self.containers)
        self.assertFalse(any(a[0] in ('stop', 'rm') and m.NAME in a for a in self.mutations()))

    def test_cleanup_does_not_remove_another_attempts_config_container(self):
        original = self.engine
        self.fail = 'configure'
        def engine(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[2] == 'run':
                self.containers[m.CONFIG]['Config']['Labels'][m.ATTEMPT] = 'someone-else'
            return result
        with patch.object(m.subprocess, 'run', side_effect=engine), \
             patch('sys.stderr', io.StringIO()), self.assertRaises(RuntimeError):
            self.execute(apply=True)
        self.assertIn(m.CONFIG, self.containers)
        self.assertFalse(any(a[0] == 'rm' for a in self.mutations()))

    def test_cli_never_prints_secret_on_failure(self):
        self.fail = 'configure'
        with patch.object(m.sys, 'argv', ['static-replica', '--apply']), \
             patch.object(m.sys, 'stdin', io.StringIO(self.token+'\n')), \
             patch('sys.stdout', io.StringIO()) as stdout, patch('sys.stderr', io.StringIO()) as stderr:
            self.assertEqual(m.main(), 1)
        self.assertNotIn(self.token, stdout.getvalue()+stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
