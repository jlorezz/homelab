"""Offline native process custody tests. No Docker, services or runner credentials.

A real .NET Runner.Worker must additionally pass the documented live runtime gate;
this ELF fixture cannot establish the managed runtime's descriptor behavior.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'agents'))
import custody
import workload


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.native = Path(cls.build.name)
        compiler = shlex.split(os.environ.get('CC', 'gcc'))
        if not shutil.which(compiler[0]):
            raise RuntimeError('Native custody tests require a C compiler (set CC); do not skip this gate')
        cls.shim = cls.native / 'claimer.py'
        cls.shim.write_text('''import os, sys, socket, time, array
from pathlib import Path
sys.path[:0] = [os.environ['TEST_RUNNER_SOURCE'], os.environ['TEST_AGENT_SOURCE']]
import custody
Path(os.environ['TEST_HELPER_PID']).write_text(str(os.getpid()))
s = socket.socket(fileno=int(sys.argv[1]))
mode = os.environ.get('TEST_PROTOCOL', '')
if mode == 'death': os._exit(9)
if mode == 'hang': time.sleep(60)
if mode == 'before':
    Path(os.environ['TEST_HELPER_WAITING']).touch()
    time.sleep(60)
if mode in ('bad', 'few', 'many', 'truncated', 'no-rights', 'after', 'ack-hang'):
    fds = [os.open('/dev/null', os.O_RDONLY) for _ in range(20 if mode == 'truncated' else 4 if mode == 'many' else 1 if mode == 'few' else 3)]
    s.sendmsg([b'BAD!' if mode == 'bad' else b'CIC1'], [] if mode == 'no-rights' else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', fds))])
    if mode in ('after', 'ack-hang'):
        s.recv(16)
        if mode == 'ack-hang': time.sleep(60)
        os._exit(9)
    os._exit(0)
custody.claim(s)
''')
        compile_base = compiler + ['-O2', '-Wall', '-Wextra', '-Werror']
        subprocess.run(compile_base + ['-shared', '-fPIC',
                       '-DCUSTODY_HELPER=' + json.dumps(str(cls.shim)),
                       '-DCUSTODY_PYTHON=' + json.dumps(sys.executable),
                       str(ROOT / 'worker-custody.c'), '-o', str(cls.native / 'custody.so')], check=True, timeout=120)
        subprocess.run(compile_base + ['-shared', '-fPIC',
                       '-DCUSTODY_HELPER=' + json.dumps(str(cls.native / 'missing-helper.py')),
                       '-DCUSTODY_PYTHON=' + json.dumps(sys.executable),
                       str(ROOT / 'worker-custody.c'), '-o', str(cls.native / 'missing-helper.so')],
                       check=True, timeout=120)
        source = cls.native / 'worker.c'
        source.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
    if (getenv("TEST_OPEN_ONLY")) {
        if (open(getenv("AGENT_WORKLOAD_LOCK"), O_RDWR | O_CLOEXEC) < 0 ||
            open(getenv("AGENT_CI_ENGINE_LOCK"), O_RDWR | O_CLOEXEC) < 0 ||
            open(getenv("TEST_RECORD"), O_RDWR | O_CLOEXEC) < 0) return 5;
    }
    if (argc > 1) {
        pid_t child = fork();
        if (child < 0) return 3;
        if (!child) {
            execl(argv[1], argv[1], "-c", "import os; from pathlib import Path; "
                  "paths=[os.readlink(p) for p in Path('/proc/self/fd').iterdir() if p.exists()]; "
                  "assert not any('lease-' in p or p.endswith('.lock') for p in paths), paths", (char *)0);
            _exit(2);
        }
        int status;
        if (waitpid(child, &status, 0) != child || status) return 4;
    }
    puts("BODY"); fflush(stdout);
    for (;;) pause();
}
''')
        subprocess.run(compile_base + [str(source), '-o', str(cls.native / 'Runner.Worker')], check=True, timeout=120)
        shutil.copy2(cls.native / 'Runner.Worker', cls.native / 'Runner.Listener')

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('heavy.lock', 'a.lock', 'b.lock', 'mutex.lock'):
            (self.root / name).touch()
        pool = self.root / 'pool'
        pool.mkdir()
        for name, value in [('memory.max', 32768 * 1024**2), ('memory.current', 1024**3),
                            ('memory.swap.max', 0), ('cpu.max', '800000 100000')]:
            (pool / name).write_text(str(value))
        (self.root / 'meminfo').write_text('MemTotal: 48596992 kB\nMemAvailable: 35632128 kB\n')
        self.data = dict(enabled=True, evidence='offline fixture only', verifiedAt=time.time()-1,
                         expiresAt=time.time()+3600, reserveMiB=11865, measuredHeadroomMiB=34797,
                         maxConcurrentMiB=22932, cpuPercent=800,
                         profiles={'ci1': dict(memoryMiB=6000, cpuPercent=200),
                                   'ci2': dict(memoryMiB=6000, cpuPercent=200)})
        self.policy = self.root / 'policy.json'
        self.policy.write_text(json.dumps(self.data))
        self.env = dict(os.environ, LD_PRELOAD=str(self.native / 'custody.so'),
                        AGENT_CI_EXCLUSIVE='0', AGENT_CI_PROFILE='ci1', AGENT_CI_SOCKET_REQUIRED='1',
                        AGENT_WORKLOAD_LOCK=str(self.root / 'heavy.lock'),
                        AGENT_CI_ENGINE_LOCK=str(self.root / 'a.lock'), AGENT_CI_QUEUE_TIMEOUT='.3',
                        AGENT_CI_ADMISSION_DIRECTORY=str(self.root), AGENT_CI_ADMISSION_POLICY=str(self.policy),
                        AGENT_CI_MEMINFO=str(self.root / 'meminfo'), AGENT_CI_POOL=str(pool),
                        TEST_HELPER_PID=str(self.root / 'helper.pid'),
                        TEST_HELPER_WAITING=str(self.root / 'helper.waiting'),
                        TEST_RUNNER_SOURCE=str(ROOT), TEST_AGENT_SOURCE=str(ROOT.parent / 'agents'))

    def finish(self, process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()

    def start(self, **changes):
        env = dict(self.env, **changes)
        p = subprocess.Popen([str(self.native / 'Runner.Worker'), sys.executable], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.finish, p)
        return p

    def body(self, p):
        # select supplies a finite startup gate even if a regression hangs constructor.
        import select
        self.assertTrue(select.select([p.stdout], [], [], 5)[0], 'Worker startup hung')
        self.assertEqual(p.stdout.readline(), 'BODY\n', p.stderr.read() if p.poll() is not None else '')
        self.assertIsNone(p.poll())

    def blocked(self, path, shared=False):
        with self.assertRaises(TimeoutError):
            with workload.lock(path, .03, shared=shared):
                self.fail('Lease unexpectedly released')

    def verify(self, p, **changes):
        with patch.dict(os.environ, dict(self.env, **changes), clear=True):
            custody.verify_worker(p.pid)

    def test_transfer_helper_exit_worker_custody_release_and_exec_isolation(self):
        p = self.start()
        self.body(p)  # fake body also execs Python and asserts no lease FDs leaked
        self.verify(p)
        helper = int((self.root / 'helper.pid').read_text())
        self.assertFalse(Path(f'/proc/{helper}').exists(), 'claimer must exit before body')
        self.blocked(self.root / 'heavy.lock')
        self.blocked(self.root / 'a.lock')
        records = list(self.root.glob('lease-*'))
        self.assertEqual(len(records), 1)
        self.blocked(records[0])
        p.kill()
        p.wait(timeout=3)
        for path in (self.root / 'heavy.lock', self.root / 'a.lock', records[0]):
            with workload.lock(path, .03):
                pass

    def test_independent_engines_overlap_same_engine_fails_before_body(self):
        a = self.start()
        self.body(a)
        b = self.start(AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'), AGENT_CI_PROFILE='ci2')
        self.body(b)
        self.verify(a)
        self.verify(b, AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'), AGENT_CI_PROFILE='ci2')
        c = self.start()
        self.assertEqual(c.wait(timeout=4), 125)
        self.assertEqual(c.stdout.read(), '')
        self.blocked(self.root / 'heavy.lock')

    def test_missing_malformed_policy_and_configuration_fail_before_body(self):
        for value in ('{', '{}', json.dumps(dict(self.data, enabled=False)),
                      json.dumps(dict(self.data, expiresAt=time.time()-1)), None):
            with self.subTest(value=value):
                if value is None:
                    self.policy.unlink()
                else:
                    self.policy.write_text(value)
                p = self.start()
                self.assertEqual(p.wait(timeout=4), 125)
                self.assertEqual(p.stdout.read(), '')
        for changes in ({'AGENT_CI_ENGINE_LOCK': ''}, {'AGENT_CI_EXCLUSIVE': '1'},
                        {'AGENT_CI_QUEUE_TIMEOUT': 'nan'}, {'AGENT_CI_QUEUE_TIMEOUT': '1801'}):
            p = self.start(**changes)
            self.assertEqual(p.wait(timeout=4), 125)
            self.assertEqual(p.stdout.read(), '')

    def test_protocol_errors_helper_death_and_timeout_are_bounded(self):
        for mode in ('death', 'bad', 'few', 'many', 'truncated', 'no-rights', 'after', 'hang', 'ack-hang'):
            with self.subTest(mode=mode):
                p = self.start(TEST_PROTOCOL=mode)
                self.assertEqual(p.wait(timeout=4), 125)
                self.assertEqual(p.stdout.read(), '')
                helper = int((self.root / 'helper.pid').read_text())
                self.assertFalse(Path(f'/proc/{helper}').exists())

    def test_missing_helper_fails_before_worker_body(self):
        p = self.start(LD_PRELOAD=str(self.native / 'missing-helper.so'))
        self.assertEqual(p.wait(timeout=4), 125)
        self.assertEqual(p.stdout.read(), '')

    def test_killed_helper_never_starts_worker(self):
        p = self.start(TEST_PROTOCOL='before')
        deadline = time.monotonic() + 3
        while not (self.root / 'helper.waiting').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        helper = int((self.root / 'helper.pid').read_text())
        os.kill(helper, signal.SIGKILL)
        self.assertEqual(p.wait(timeout=4), 125)
        self.assertEqual(p.stdout.read(), '')

    def test_worker_death_kills_queued_claimer(self):
        with workload.lock(self.root / 'heavy.lock', .1):
            p = self.start(AGENT_CI_QUEUE_TIMEOUT='5')
            deadline = time.monotonic() + 3
            while not (self.root / 'helper.pid').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            helper = int((self.root / 'helper.pid').read_text())
            p.kill()
            p.wait(timeout=3)
            deadline = time.monotonic() + 3
            while workload.process_identity(helper) is not None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIsNone(workload.process_identity(helper))
        with workload.lock(self.root / 'heavy.lock', .05):
            pass

    def test_nonworker_and_unconfigured_worker_are_inert(self):
        for binary, env in [('Runner.Listener', self.env),
                            ('Runner.Worker', {k: v for k, v in self.env.items() if k != 'AGENT_CI_PROFILE'})]:
            p = subprocess.Popen([str(self.native / binary)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(self.finish, p)
            self.body(p)
            self.assertFalse((self.root / 'helper.pid').exists())

    def test_wrong_inode_and_record_are_insufficient(self):
        p = self.start()
        self.body(p)
        self.verify(p)
        # Wrong expected engine and altered reservation policy cannot be papered
        # over by an inherited marker. Kernel inode/mode and record must match.
        with self.assertRaises(RuntimeError):
            self.verify(p, AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'), AGENT_CI_CUSTODY='ready')
        self.data['profiles']['ci1']['memoryMiB'] += 1
        self.policy.write_text(json.dumps(self.data))
        with self.assertRaises(RuntimeError):
            self.verify(p)

    def test_open_fds_and_env_marker_without_flocks_do_not_prove_custody(self):
        record = self.root / 'lease-forged'
        record.write_text(json.dumps(self.data['profiles']['ci1']))
        env = dict(self.env, TEST_OPEN_ONLY='1', TEST_RECORD=str(record), AGENT_CI_CUSTODY='ready')
        del env['AGENT_CI_PROFILE']  # Start actual ELF fixture with no native admission.
        p = subprocess.Popen([str(self.native / 'Runner.Worker')], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.finish, p)
        self.body(p)
        with self.assertRaises(RuntimeError):
            self.verify(p)

    def test_static_lane_holds_global_and_capacity_without_engine(self):
        p = self.start(AGENT_CI_ENGINE_LOCK='', AGENT_CI_SOCKET_REQUIRED='0')
        self.body(p)
        self.verify(p, AGENT_CI_ENGINE_LOCK='', AGENT_CI_SOCKET_REQUIRED='0')
        self.blocked(self.root / 'heavy.lock')
        for path in self.root.glob('lease-*'):
            self.blocked(path)
        with workload.lock(self.root / 'a.lock', .03):
            pass

    def test_successful_measured_hooks_only_verify_existing_custody(self):
        p = self.start()
        self.body(p)
        spec = importlib.util.spec_from_file_location('custody_success_hook', ROOT / 'job-hook.py')
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        with patch.object(hook, 'worker_ancestor', return_value=p.pid), \
             patch.dict(os.environ, self.env, clear=True), \
             patch.object(hook.subprocess, 'Popen') as spawn:
            for stage in ('started', 'completed'):
                with patch.object(sys, 'argv', ['hook', stage]):
                    hook.main()
            spawn.assert_not_called()
        self.blocked(self.root / 'heavy.lock')

    def test_measured_hook_never_starts_legacy_guardian(self):
        spec = importlib.util.spec_from_file_location('custody_hook', ROOT / 'job-hook.py')
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        with patch.object(hook, 'worker_ancestor', return_value=123), \
             patch.object(hook, 'process_identity', return_value='fixture'), \
             patch.dict(os.environ, self.env, clear=True), \
             patch.object(hook, 'verify_worker', side_effect=RuntimeError('missing custody')), \
             patch.object(hook.subprocess, 'Popen') as spawn:
            with self.assertRaises(RuntimeError):
                hook.main()
            spawn.assert_not_called()
