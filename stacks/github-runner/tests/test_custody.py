"""Offline native custody tests; actual .NET/auto-update remain live gates."""
import json
import os
from pathlib import Path
import select
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
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'agents')]
import custody
import workload


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.native = Path(cls.build.name)
        compiler = shlex.split(os.environ.get('CC', 'gcc'))
        if not shutil.which(compiler[0]):
            raise RuntimeError('Native custody tests require a C compiler (set CC)')
        # This is the actual started hook, with protocol/death fault injection at
        # the transfer boundary. There is no pre-startup admission helper.
        cls.shim = cls.native / 'hook.py'
        cls.shim.write_text('''import os, sys, socket, time, array, runpy
from pathlib import Path
sys.path[:0] = [os.environ['TEST_RUNNER_SOURCE'], os.environ['TEST_AGENT_SOURCE']]
Path(os.environ['TEST_HELPER_PID']).write_text(str(os.getpid()))
mode = os.environ.get('TEST_PROTOCOL', '')
if mode == 'death': os._exit(9)
if mode == 'before':
    Path(os.environ['TEST_HELPER_WAITING']).touch()
    time.sleep(60)
base = socket.socket
class FaultSocket(base):
    def sendmsg(self, buffers, ancillary):
        if mode == 'pre-transfer-death': os._exit(9)
        if mode == 'hang': time.sleep(60)
        if mode in ('bad', 'oversized', 'few', 'many', 'truncated', 'no-rights', 'not-regular', 'unlocked'):
            count = 20 if mode == 'truncated' else 4 if mode == 'many' else 1 if mode == 'few' else 3
            path = '/dev/null' if mode == 'not-regular' else os.environ['AGENT_WORKLOAD_LOCK']
            fds = [os.open(path, os.O_RDONLY) for _ in range(count)]
            try:
                return super().sendmsg([b'BAD!' if mode == 'bad' else b'CIC1extra' if mode == 'oversized' else b'CIC1'],
                    [] if mode == 'no-rights' else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', fds))])
            finally:
                for fd in fds: os.close(fd)
        return super().sendmsg(buffers, ancillary)
    def recv(self, size):
        ack = super().recv(size)
        if mode == 'after': os._exit(9)
        if mode == 'ack-hang': time.sleep(60)
        return ack
socket.socket = FaultSocket
sys.argv = ['job-hook', 'started']
runpy.run_path(str(Path(os.environ['TEST_RUNNER_SOURCE']) / 'job-hook.py'), run_name='__main__')
''')
        compile_base = compiler + ['-O2', '-Wall', '-Wextra', '-Werror']
        subprocess.run(compile_base + ['-shared', '-fPIC', '-pthread',
                       str(ROOT / 'worker-custody.c'), '-o', str(cls.native / 'custody.so')],
                       check=True, timeout=120)
        source = cls.native / 'worker.c'
        source.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/wait.h>
#include <signal.h>
int main(int argc, char **argv) {
    if (getenv("TEST_OPEN_ONLY")) {
        if (open(getenv("AGENT_WORKLOAD_LOCK"), O_RDWR | O_CLOEXEC) < 0 ||
            open(getenv("AGENT_CI_ENGINE_LOCK"), O_RDWR | O_CLOEXEC) < 0 ||
            open(getenv("TEST_RECORD"), O_RDWR | O_CLOEXEC) < 0) return 5;
    }
    puts("IPC_READY"); fflush(stdout);
    char buf[8192];
    size_t left = 1024 * 1024;
    while (left) {
        ssize_t n = read(0, buf, left < sizeof(buf) ? left : sizeof(buf));
        if (n <= 0) return 6;
        left -= (size_t)n;
    }
    puts("BODY"); fflush(stdout);
    if (argc > 2) {
        if (read(0, buf, 1) != 1) return 7;
        pid_t child = fork();
        if (child < 0) return 3;
        if (!child) { execl(argv[1], argv[1], argv[2], (char *)0); _exit(2); }
        int status = 0, done = 0;
        for (int i = 0; i < 400; i++) {
            if (waitpid(child, &status, WNOHANG) == child) { done = 1; break; }
            usleep(10000);
        }
        if (!done) { kill(child, SIGKILL); waitpid(child, &status, 0); }
        puts(status ? "HOOK_FAILED" : "JOB"); fflush(stdout);
        if (!status) {
            child = fork();
            if (!child) {
                execl(argv[1], argv[1], "-c", "import os; from pathlib import Path; "
                      "paths=[os.readlink(p) for p in Path('/proc/self/fd').iterdir() if p.exists()]; "
                      "assert not any('lease-' in p or p.endswith('.lock') for p in paths), paths", (char *)0);
                _exit(2);
            }
            if (waitpid(child, &status, 0) != child || status) return 4;
            puts("EXEC_CLEAN"); fflush(stdout);
        }
    }
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
        (pool / 'memory.stat').write_text('anon 1073741824\nkernel 0\nfile 0\nshmem 0\n'
                                          'inactive_file 0\nfile_dirty 0\nfile_writeback 0\n')
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
        # A fake Worker is not a runner supervisor: clean up any test hook after
        # killing it, without claiming the real supervisor kill races are solved.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdin:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()

    def start(self, hook=True, binary='Runner.Worker', **changes):
        (self.root / 'helper.pid').unlink(missing_ok=True)
        command = [str(self.native / binary)]
        if hook:
            command += [sys.executable, str(self.shim)]
        p = subprocess.Popen(command, env=dict(self.env, **changes), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.addCleanup(self.finish, p)
        self.line(p, 'IPC_READY', timeout=1)
        # Far larger than an anonymous pipe's buffer: constructor admission would
        # prevent this complete JobRequest from reaching the fake managed body.
        payload = b'x' * (1024 * 1024)
        while payload:
            payload = payload[p.stdin.write(payload):]
        self.line(p, 'BODY', timeout=1)
        return p

    def line(self, p, expected, timeout=6):
        self.assertTrue(select.select([p.stdout], [], [], timeout)[0], 'Worker/hook hung')
        actual = p.stdout.readline()
        if actual == b'CI kernel admission verified\n':
            self.assertTrue(select.select([p.stdout], [], [], timeout)[0])
            actual = p.stdout.readline()
        self.assertEqual(actual, (expected + '\n').encode())

    def trigger(self, p, expected='JOB'):
        p.stdin.write(b'G')
        self.line(p, expected)
        if expected == 'JOB':
            self.line(p, 'EXEC_CLEAN')

    def blocked(self, path, shared=False):
        with self.assertRaises(TimeoutError):
            with workload.lock(path, .03, shared=shared):
                self.fail('Lease unexpectedly released')

    def verify(self, p, **changes):
        with patch.dict(os.environ, dict(self.env, **changes), clear=True):
            custody.verify_worker(p.pid)

    def helper(self):
        deadline = time.monotonic() + 2
        while not (self.root / 'helper.pid').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        return int((self.root / 'helper.pid').read_text())

    def test_transfer_hook_exit_worker_custody_release_and_exec_isolation(self):
        p = self.start()
        self.trigger(p)
        self.verify(p)
        self.assertFalse(Path(f'/proc/{self.helper()}').exists())
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

    def test_ipc_body_before_any_admission_and_global_exclusion(self):
        with workload.lock(self.root / 'heavy.lock', .1):
            p = self.start(AGENT_CI_QUEUE_TIMEOUT='1800')
            self.assertFalse((self.root / 'helper.pid').exists())
            self.assertEqual(list(self.root.glob('lease-*')), [])
            p.stdin.write(b'G')
            self.helper()
            self.assertFalse(select.select([p.stdout], [], [], .15)[0])
        self.line(p, 'JOB')
        self.line(p, 'EXEC_CLEAN')
        self.verify(p)

    def test_independent_engines_overlap_same_engine_hook_fails(self):
        a = self.start()
        self.trigger(a)
        b = self.start(AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'), AGENT_CI_PROFILE='ci2')
        self.trigger(b)
        self.verify(a)
        self.verify(b, AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'), AGENT_CI_PROFILE='ci2')
        c = self.start()
        self.trigger(c, 'HOOK_FAILED')
        self.blocked(self.root / 'heavy.lock')

    def test_missing_malformed_policy_and_configuration_fail_before_job(self):
        for value in ('{', '{}', json.dumps(dict(self.data, enabled=False)),
                      json.dumps(dict(self.data, expiresAt=time.time()-1)), None):
            with self.subTest(value=value):
                if value is None:
                    self.policy.unlink()
                else:
                    self.policy.write_text(value)
                self.trigger(self.start(), 'HOOK_FAILED')
        for changes in ({'AGENT_CI_ENGINE_LOCK': ''}, {'AGENT_CI_EXCLUSIVE': '1'},
                        {'AGENT_CI_QUEUE_TIMEOUT': 'nan'}, {'AGENT_CI_QUEUE_TIMEOUT': '1801'}):
            self.trigger(self.start(**changes), 'HOOK_FAILED')

    def test_malformed_transfers_fail_closed_and_release_all_fds(self):
        for mode in ('bad', 'oversized', 'few', 'many', 'truncated', 'no-rights', 'not-regular', 'hang'):
            with self.subTest(mode=mode):
                p = self.start(TEST_PROTOCOL=mode, AGENT_CI_QUEUE_TIMEOUT='5')
                p.stdin.write(b'G')
                self.assertEqual(p.wait(timeout=5), 125)
                helper = self.helper()
                # No orphan test processes, even when a malformed Worker exits.
                try:
                    os.kill(helper, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
                while workload.process_identity(helper) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertIsNone(workload.process_identity(helper))
                for path in (self.root / 'heavy.lock', self.root / 'a.lock', *self.root.glob('lease-*')):
                    with workload.lock(path, .1):
                        pass

    def test_hook_death_before_transfer_never_starts_job(self):
        for mode in ('death', 'before'):
            p = self.start(TEST_PROTOCOL=mode)
            p.stdin.write(b'G')
            helper = self.helper()
            if mode == 'before':
                os.kill(helper, signal.SIGKILL)
            self.line(p, 'HOOK_FAILED')
            with self.assertRaises(RuntimeError):
                self.verify(p)
            with workload.lock(self.root / 'heavy.lock', .03):
                pass

    def test_hook_death_or_hang_after_ack_keeps_worker_custody(self):
        for mode in ('after', 'ack-hang'):
            p = self.start(TEST_PROTOCOL=mode)
            self.trigger(p, 'HOOK_FAILED')
            self.verify(p)
            self.blocked(self.root / 'a.lock')
            p.kill()
            p.wait(timeout=3)
            with workload.lock(self.root / 'a.lock', .1):
                pass

    def test_worker_exit_during_queue_prevents_transfer(self):
        with workload.lock(self.root / 'heavy.lock', .1):
            p = self.start(AGENT_CI_QUEUE_TIMEOUT='5')
            p.stdin.write(b'G')
            helper = self.helper()
            p.kill()
            p.wait(timeout=3)
            deadline = time.monotonic() + 2
            while workload.process_identity(helper) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIsNone(workload.process_identity(helper))
        self.assertEqual(list(self.root.glob('lease-*')), [])

    def test_nonworker_and_unconfigured_worker_are_inert(self):
        for changes in ({'binary': 'Runner.Listener'}, {'AGENT_CI_PROFILE': ''}):
            p = self.start(hook=False, **changes)
            self.assertFalse((self.root / 'helper.pid').exists())
            self.assertIsNone(p.poll())

    def test_wrong_inode_and_record_are_insufficient(self):
        p = self.start()
        self.trigger(p)
        self.verify(p)
        with self.assertRaises(RuntimeError):
            self.verify(p, AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'), AGENT_CI_CUSTODY='ready')
        self.data['profiles']['ci1']['memoryMiB'] += 1
        self.policy.write_text(json.dumps(self.data))
        # Policy renewal cannot revoke or alter a live pinned reservation.
        self.verify(p)
        record = next(self.root.glob('lease-*'))
        item = json.loads(record.read_text())
        item['_profile'] = 'wrong-profile'
        record.write_text(json.dumps(item))
        with self.assertRaises(RuntimeError):
            self.verify(p)

    def test_expired_evidence_falls_back_to_exclusive_and_blocks_other_engines(self):
        self.data.update(verifiedAt=time.time()-3600, expiresAt=time.time()-1)
        self.policy.write_text(json.dumps(self.data))
        a = self.start(AGENT_CI_EXPIRED_POLICY_FALLBACK='1')
        self.trigger(a)
        self.verify(a, AGENT_CI_EXPIRED_POLICY_FALLBACK='1')
        self.blocked(self.root / 'heavy.lock', shared=True)
        b = self.start(AGENT_CI_ENGINE_LOCK=str(self.root / 'b.lock'),
                       AGENT_CI_PROFILE='ci2', AGENT_CI_EXPIRED_POLICY_FALLBACK='1')
        self.trigger(b, 'HOOK_FAILED')

    def test_expiry_during_engine_wait_releases_shared_lock_before_fallback(self):
        self.data['expiresAt'] = time.time()+.2
        self.policy.write_text(json.dumps(self.data))
        with workload.lock(self.root / 'a.lock', 1):
            p = self.start(AGENT_CI_EXPIRED_POLICY_FALLBACK='1', AGENT_CI_QUEUE_TIMEOUT='5')
            p.stdin.write(b'G')
            self.helper()
            time.sleep(.25)
        self.line(p, 'JOB')
        self.line(p, 'EXEC_CLEAN')
        self.verify(p, AGENT_CI_EXPIRED_POLICY_FALLBACK='1')
        self.blocked(self.root / 'heavy.lock', shared=True)

    def test_completion_keeps_pinned_custody_after_policy_expiry_or_removal(self):
        p = self.start()
        self.trigger(p)
        self.data.update(verifiedAt=time.time()-3600, expiresAt=time.time()-1)
        self.policy.write_text(json.dumps(self.data))
        self.verify(p)
        self.policy.unlink()
        self.verify(p)

    def test_open_fds_and_marker_without_flocks_do_not_prove_custody(self):
        record = self.root / 'lease-forged'
        record.write_text(json.dumps(self.data['profiles']['ci1']))
        p = self.start(hook=False, TEST_OPEN_ONLY='1', TEST_RECORD=str(record), AGENT_CI_CUSTODY='ready')
        with self.assertRaises(RuntimeError):
            self.verify(p)

    def test_static_lane_holds_global_and_capacity_without_engine(self):
        p = self.start(AGENT_CI_ENGINE_LOCK='', AGENT_CI_SOCKET_REQUIRED='0')
        self.trigger(p)
        self.verify(p, AGENT_CI_ENGINE_LOCK='', AGENT_CI_SOCKET_REQUIRED='0')
        self.blocked(self.root / 'heavy.lock')
        for path in self.root.glob('lease-*'):
            self.blocked(path)
        with workload.lock(self.root / 'a.lock', .03):
            pass

    def test_completed_hook_only_verifies_without_reacquisition(self):
        p = self.start()
        self.trigger(p)
        import importlib.util
        spec = importlib.util.spec_from_file_location('custody_hook', ROOT / 'job-hook.py')
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        with patch.object(hook, 'worker_ancestor', return_value=p.pid), \
             patch.dict(os.environ, self.env, clear=True), \
             patch.object(sys, 'argv', ['hook', 'completed']), \
             patch.object(hook, 'claim') as claim, \
             patch.object(hook.subprocess, 'Popen') as spawn:
            hook.main()
            claim.assert_not_called()
            spawn.assert_not_called()
        self.blocked(self.root / 'heavy.lock')

    def test_missing_library_never_falls_back_to_legacy_guardian(self):
        p = self.start(LD_PRELOAD=str(self.native / 'missing.so'), AGENT_CI_CUSTODY_ENDPOINT='')
        self.trigger(p, 'HOOK_FAILED')
        with workload.lock(self.root / 'heavy.lock', .1):
            pass
        with self.assertRaises(RuntimeError):
            self.verify(p)

    def test_correct_count_without_kernel_lock_proof_never_starts_job(self):
        p = self.start(TEST_PROTOCOL='unlocked')
        self.trigger(p, 'HOOK_FAILED')
        with self.assertRaises(RuntimeError):
            self.verify(p)
        with workload.lock(self.root / 'a.lock', .1):
            pass

    def test_receiver_rejects_nondescendant_peer(self):
        import socket
        # /proc/environ does not expose setenv additions; capture the constructor
        # marker via a fixture child instead, using the fake hook below.
        # A descendant exports only a local endpoint name, never credentials.
        endpoint_file = self.root / 'endpoint'
        shim = self.native / 'export-endpoint.py'
        shim.write_text("import os; from pathlib import Path; "
                        "Path(os.environ['TEST_ENDPOINT_FILE']).write_text(os.environ['AGENT_CI_CUSTODY_ENDPOINT'])")
        old = self.shim
        self.shim = shim
        try:
            p = self.start(TEST_ENDPOINT_FILE=str(endpoint_file))
            p.stdin.write(b'G')
            self.line(p, 'JOB')
            self.line(p, 'EXEC_CLEAN')
        finally:
            self.shim = old
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as channel:
            channel.connect('\0' + endpoint_file.read_text())
            self.assertEqual(p.wait(timeout=3), 125)

    def test_forged_endpoint_peer_receives_no_admission_fds(self):
        import socket
        shim = self.native / 'forged-endpoint.py'
        shim.write_text(self.shim.read_text().replace(
            "sys.argv = ['job-hook', 'started']",
            "os.environ['AGENT_CI_CUSTODY_ENDPOINT'] = 'ci-custody-' + str(os.getppid()) + '-forged'\n"
            "sys.argv = ['job-hook', 'started']"))
        old = self.shim
        self.shim = shim
        try:
            p = self.start()
        finally:
            self.shim = old
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
            server.bind('\0' + f'ci-custody-{p.pid}-forged')
            server.listen(1)
            server.settimeout(3)
            p.stdin.write(b'G')
            with server.accept()[0] as channel:
                channel.settimeout(3)
                payload, ancillary, _, _ = channel.recvmsg(16, socket.CMSG_SPACE(12))
                self.assertEqual(payload, b'')
                self.assertEqual(ancillary, [])
            self.line(p, 'HOOK_FAILED')
        with workload.lock(self.root / 'heavy.lock', .1):
            pass

    def test_missing_started_hook_never_starts_job(self):
        old = self.shim
        self.shim = self.native / 'missing-hook.py'
        try:
            p = self.start()
        finally:
            self.shim = old
        self.trigger(p, 'HOOK_FAILED')
        with self.assertRaises(RuntimeError):
            self.verify(p)

    def test_hook_death_with_acquired_fds_before_send_fails_closed(self):
        p = self.start(TEST_PROTOCOL='pre-transfer-death')
        p.stdin.write(b'G')
        self.assertEqual(p.wait(timeout=3), 125)
        for path in (self.root / 'heavy.lock', self.root / 'a.lock', *self.root.glob('lease-*')):
            with workload.lock(path, .1):
                pass

    def test_measured_hook_failure_never_spawns_legacy_guardian(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('custody_failure_hook', ROOT / 'job-hook.py')
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        for stage in ('started', 'completed'):
            with patch.object(hook, 'worker_ancestor', return_value=os.getpid()), \
                 patch.dict(os.environ, self.env, clear=True), \
                 patch.object(sys, 'argv', ['hook', stage]), \
                 patch.object(hook, 'claim', side_effect=RuntimeError('missing custody')) as claim, \
                 patch.object(hook, 'verify_worker', side_effect=RuntimeError('missing custody')), \
                 patch.object(hook.subprocess, 'Popen') as spawn:
                with self.assertRaises(RuntimeError):
                    hook.main()
                spawn.assert_not_called()
                if stage == 'completed':
                    claim.assert_not_called()
