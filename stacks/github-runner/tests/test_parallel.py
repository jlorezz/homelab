"""Real offline flock leases and renderer boundaries; no Docker or host mutation."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'agents'))
import parallel
import workload


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('heavy', 'a', 'b', 'mutex'):
            (self.root / (name + '.lock')).touch()
        self.pool = self.root / 'pool'
        self.pool.mkdir()
        for name, value in [('memory.max', 32768 * 1024**2), ('memory.current', 1024**3),
                            ('memory.swap.max', 0), ('cpu.max', '800000 100000')]:
            (self.pool / name).write_text(str(value))
        self.meminfo = self.root / 'meminfo'
        self.meminfo.write_text('MemTotal: 48596992 kB\nMemAvailable: 35632128 kB\n')
        self.policy = self.root / 'policy.json'
        self.data = dict(enabled=True, evidence='offline test fixture ONLY', verifiedAt=time.time()-1,
                         expiresAt=time.time()+3600, reserveMiB=11865, measuredHeadroomMiB=34797,
                         maxConcurrentMiB=22932, cpuPercent=800,
                         profiles={p: dict(memoryMiB=6000, cpuPercent=200)
                                   for p in ('ci1', 'ci2', 'browser', 'static', 'static-2')})
        self.save()
        self.owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.addCleanup(self.finish, self.owner)
        self.number = 0

    def save(self):
        self.policy.write_text(json.dumps(self.data))

    def finish(self, process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)

    def lease(self, engine, profile='ci1'):
        self.number += 1
        ready = self.root / f'ready-{self.number}'
        command = [sys.executable, workload.__file__, '--lock', str(self.root / 'heavy.lock'),
                   'lease', '--shared', '--owner-pid', str(self.owner.pid), '--owner-identity',
                   workload.process_identity(self.owner.pid), '--queue-timeout', '.3',
                   '--ready', str(ready), '--stop', str(self.root / f'stop-{self.number}'),
                   '--engine-lock', str(self.root / (engine + '.lock')),
                   '--admission-profile', profile, '--admission-policy', str(self.policy),
                   '--admission-directory', str(self.root), '--ci-pool', str(self.pool),
                   '--meminfo', str(self.meminfo)]
        child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.finish, child)
        deadline = time.monotonic() + 3
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        return child, ready

    def test_hook_defaults_exclusive_and_requires_measured_engine_configuration(self):
        spec = importlib.util.spec_from_file_location('ci_hook', ROOT / 'job-hook.py')
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        with patch.object(hook, 'worker_ancestor', return_value=123), \
             patch.object(hook, 'process_identity', return_value='fixture'), \
             patch.object(hook, 'Path', return_value=self.root), \
             patch.object(sys, 'argv', ['hook', 'started']), \
             patch.object(hook.subprocess, 'Popen') as spawn:
            spawn.return_value.poll.return_value = 1
            for environment in ({}, {'AGENT_CI_EXCLUSIVE': '0', 'AGENT_CI_PROFILE': 'ci2',
                                     'AGENT_CI_SOCKET_REQUIRED': '1'}):
                with patch.dict(hook.os.environ, environment, clear=True):
                    spawn.reset_mock()
                    with self.assertRaises(RuntimeError):
                        hook.main()
                    if environment:
                        spawn.assert_not_called()
                    else:
                        self.assertNotIn('--shared', spawn.call_args.args[0])

    def test_two_engines_overlap_but_same_engine_and_global_exclusive_wait(self):
        a, ar = self.lease('a')
        b, br = self.lease('b', 'ci2')
        self.assertTrue(ar.exists() and br.exists())
        # Completion marker must not release a still-live worker's resources.
        (self.root / 'stop-1').touch()
        time.sleep(.3)
        self.assertIsNone(a.poll())
        blocked, ready = self.lease('a')
        self.assertNotEqual(blocked.wait(timeout=2), 0)
        self.assertFalse(ready.exists())
        with self.assertRaises(TimeoutError):
            with workload.lock(self.root / 'heavy.lock', .05):
                self.fail('Production/agent exclusive admission overlapped CI')
        self.finish(a)
        with self.assertRaises(TimeoutError):
            with workload.lock(self.root / 'heavy.lock', .05):
                self.fail('Global lock released before last engine')
        self.finish(b)
        with workload.lock(self.root / 'heavy.lock', .1):
            pass
        # Killed holders leave files, but kernel locks reclaim capacity safely.
        replacement, ready = self.lease('a')
        self.assertTrue(ready.exists())
        self.owner.terminate()
        self.owner.wait(timeout=2)
        self.assertEqual(replacement.wait(timeout=2), 0)

    def test_combined_working_sets_refuse_before_ready(self):
        self.data['profiles']['ci2']['memoryMiB'] = 18000
        self.save()
        first, ready = self.lease('a')
        self.assertTrue(ready.exists())
        second, ready = self.lease('b', 'ci2')
        self.assertNotEqual(second.wait(timeout=2), 0)
        self.assertFalse(ready.exists())

    def test_fail_closed_on_stale_missing_disabled_or_low_live_headroom(self):
        for change in ('stale', 'missing', 'disabled', 'memory', 'parent'):
            with self.subTest(change=change):
                self.meminfo.write_text('MemTotal: 48596992 kB\nMemAvailable: 35632128 kB\n')
                (self.pool / 'memory.swap.max').write_text('0')
                saved = copy.deepcopy(self.data)
                if change == 'stale':
                    self.data['expiresAt'] = time.time()-1
                if change == 'disabled':
                    self.data['enabled'] = False
                self.save()
                if change == 'missing':
                    self.policy.unlink()
                if change == 'memory':
                    self.meminfo.write_text('MemTotal: 48596992 kB\nMemAvailable: 12000000 kB\n')
                if change == 'parent':
                    (self.pool / 'memory.swap.max').write_text('1')
                child, ready = self.lease('a')
                self.assertNotEqual(child.wait(timeout=2), 0)
                self.assertFalse(ready.exists())
                self.data = saved

    def test_render_keeps_limits_isolates_engine_and_requires_inventory(self):
        original = {'services': {}}
        for name, state in [('runner', ''), ('browser', '/browser'), ('static', '/static')]:
            original['services'][name] = dict(
                image='old', cpus=2 if name == 'static' else 4, mem_limit='12g', memswap_limit='12g',
                volumes=[f'/var/lib/dealpipe-ci{state}/runner:/runner',
                         f'/var/lib/dealpipe-ci{state}/home:/home/ci',
                         f'/var/lib/dealpipe-ci{state}/work:/var/lib/dealpipe-ci{state}/work',
                         '/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock'],
                environment={})
            if name != 'static':
                original['services'][name]['volumes'].append('/run/user/1102/docker.sock:/var/run/docker.sock')
        with self.assertRaises(ValueError):
            parallel.render(original, 'sha256:'+'1'*64, self.policy)
        original['services']['browser']['volumes'] = [v.replace('/run/user/1102/docker.sock:',
            '/run/user/1102/dealpipe-browser/docker.sock:') for v in original['services']['browser']['volumes']]
        static = copy.deepcopy(original['services']['static'])
        static['volumes'] = [v.replace('/static/', '/static-2/') for v in static['volumes']]
        static['mem_limit'] = static['memswap_limit'] = '8g'
        static['environment']['RUNNER_NAME'] = 'dealpipe-nas-static-2'
        original['services']['static-2'] = static
        existing, second = parallel.render(original, 'sha256:'+'1'*64, self.policy)
        lane = second['services']['runner']
        for unsafe in ('/var/run/docker.sock:/host.sock', '/var/lib/dealpipe-deploy/github-token:/secret:ro'):
            bad = copy.deepcopy(original)
            bad['services']['runner']['volumes'].append(unsafe)
            with self.assertRaises(ValueError):
                parallel.render(bad, 'sha256:'+'1'*64, self.policy)
        self.assertEqual(lane['environment']['RUNNER_LABEL'], 'dealpipe-nas-ci')
        self.assertEqual(lane['environment']['RUNNER_NAME'], 'dealpipe-nas-ci-2')
        self.assertIn('/run/user/1102/dealpipe-ci2/docker.sock:/var/run/docker.sock', lane['volumes'])
        self.assertIn('/var/lib/dealpipe-ci/ci2/work:/var/lib/dealpipe-ci/ci2/work', lane['volumes'])
        self.assertFalse(any(v.startswith('/run/user/1102/docker.sock:') or '/var/run/docker.sock:' in v
                             or 'github-token' in v for v in lane['volumes']))
        for name, old in original['services'].items():
            new = existing['services'][name]
            for key in ('mem_limit', 'memswap_limit', 'cpus'):
                self.assertEqual(old[key], new[key])
            self.assertEqual(new['environment']['AGENT_CI_EXCLUSIVE'], '0')
            self.assertIn('/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock', new['volumes'])
            self.assertEqual(any('ci-engine.lock' in v for v in new['volumes']), name in ('runner', 'browser'))
