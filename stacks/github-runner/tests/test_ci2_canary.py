"""Mocked Docker contracts only: no engine, registration, host service or network."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('ci2_canary', ROOT / 'ci2-canary.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
IMAGE = 'sha256:' + 'a' * 64


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'ci2'
        self.root.mkdir(mode=0o700)
        for name in ('runner', 'home'):
            (self.root / name).mkdir(mode=0o700)
        self.socket = self.base / 'docker.sock'
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(self.socket))
        self.addCleanup(sock.close)
        self.policy = self.base / 'policy.json'
        self.policy_data = dict(enabled=True, evidence='OFFLINE fixture, never a measured policy',
                                verifiedAt=time.time()-1, expiresAt=time.time()+3600,
                                reserveMiB=11865, measuredHeadroomMiB=34797,
                                maxConcurrentMiB=22932, cpuPercent=800,
                                profiles={'ci2': dict(memoryMiB=6000, cpuPercent=400)})
        self.save_policy()
        self.info = dict(DockerRootDir='/var/lib/dealpipe-ci/docker',
                         SecurityOptions=['name=rootless'], CgroupDriver='systemd',
                         CgroupVersion='2', Containers=0)
        self.image = dict(Id=IMAGE, Config=dict(Entrypoint=['/usr/local/bin/ci-runner'], Env=[
            'LD_PRELOAD=/usr/local/lib/agent-platform/worker-custody.so',
            'ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/ci-job-started.sh',
            'ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/ci-job-completed.sh']))
        self.token = 'SECRET-registration-token'
        self.calls = []
        self.inventory = b''
        self.failure = None
        self.config_running = False
        self.bad_registration = False
        original_stat = Path.stat
        original_lstat = Path.lstat

        def owned(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            parts = list(value)
            parts[4] = 1102
            return os.stat_result(parts)

        def owned_lstat(path, *args, **kwargs):
            value = original_lstat(path, *args, **kwargs)
            parts = list(value)
            parts[4] = 1102
            return os.stat_result(parts)

        # Only mocked filesystem admission sources; no host files are accessed.
        for name, value in [('ROOT', self.root), ('SOCKET', self.socket), ('POLICY', self.policy)]:
            self.patch(m, name, value)
        self.patch(m.os, 'geteuid', lambda: 1102)
        self.patch(Path, 'stat', owned)
        self.patch(Path, 'lstat', owned_lstat)
        self.patch(m.subprocess, 'run', self.engine)
        self.real_lane = m.lane
        # Substitute temporary fixture paths for the global admission bind sources.
        self.binds = []
        for index, volume in enumerate(self.real_lane()['volumes'][4:]):
            source, destination, *mode = volume.split(':')
            fixture = self.base / ('admission-' + str(index))
            if volume in m.ADMISSION:
                fixture.mkdir()
            else:
                fixture.touch()
            self.binds.append(':'.join([str(fixture), destination, *mode]))
        self.patch(m, 'ADMISSION', self.binds[1:4])
        def lane():
            value = self.real_lane()
            value['volumes'][4:] = self.binds
            return value
        self.patch(m, 'lane', lane)

    def patch(self, target, name, value):
        handle = patch.object(target, name, value)
        handle.start()
        self.addCleanup(handle.stop)

    def save_policy(self):
        self.policy.write_text(json.dumps(self.policy_data))

    def engine(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[:2], ['docker', '--host=unix://' + str(self.socket)])
        self.assertNotIn(self.token, ' '.join(argv))
        self.assertNotIn(self.token, json.dumps(kwargs['env']))
        self.assertEqual(kwargs['env']['DOCKER_CONFIG'], '/nonexistent')
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        args = argv[2:]
        output = None
        if args[0] in ('stop', 'kill', 'rm', 'restart') or any(a in ('stop', 'kill', 'rm', 'restart') for a in args[:2]):
            self.fail('No cleanup or existing worker lifecycle action is authorized')
        if self.failure and self.failure(args):
            raise subprocess.TimeoutExpired(argv, 30, output=self.token, stderr=self.token)
        if args[0] == 'info':
            output = self.info
        elif args[:2] == ['container', 'ls']:
            return SimpleNamespace(returncode=0, stdout=self.inventory)
        elif args[:2] == ['image', 'inspect']:
            output = [self.image]
        elif args[:2] == ['container', 'inspect']:
            output = dict(Running=self.config_running, ExitCode=0)
        elif args[0] == 'exec':
            self.assertEqual(args[:3], ['exec', '-i', m.CONFIG])
            script = args[-1]
            self.assertIn('IFS= read -r ACTIONS_RUNNER_INPUT_TOKEN', script)
            self.assertIn('export ACTIONS_RUNNER_INPUT_TOKEN', script)
            self.assertIn('--name dealpipe-nas-ci-2-canary --labels dealpipe-ci2-canary', script)
            self.assertNotIn('--replace', script)
            self.assertNotIn('--token', script)
            self.assertNotIn('run.sh', script)
            self.assertNotIn('Runner.Listener', script)
            self.assertEqual(kwargs['input'], (self.token + '\n').encode())
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            data = dict(agentName='wrong' if self.bad_registration else m.NAME,
                        gitHubUrl='https://github.com/dealpipe/dealpipe', workFolder=m.WORK)
            (self.root / 'runner/.runner').write_text(json.dumps(data))
            (self.root / 'runner/.credentials').write_text('PRIVATE fixture')
        elif args[:2] not in (['container', 'create'], ['container', 'start'], ['container', 'wait']):
            self.fail('Unexpected Docker operation: ' + repr(args))
        return SimpleNamespace(returncode=0, stdout=json.dumps(output).encode())

    def mutations(self):
        return [a[2:] for a, _ in self.calls if a[2:4] in
                (['container', 'create'], ['container', 'start']) or a[2] == 'exec']

    def test_dryrun_is_readonly_and_does_not_consume_token(self):
        stream = io.StringIO(self.token)
        self.assertIn('DRY RUN', m.run(IMAGE, stream=stream))
        self.assertEqual(stream.tell(), 0)
        self.assertEqual(self.mutations(), [])
        self.assertFalse((self.root / '.canary-attempt').exists())

    def test_apply_registers_once_without_token_in_metadata_and_only_then_starts_listener(self):
        class Once(io.StringIO):
            reads = 0
            def read(inner, size):
                inner.reads += 1
                self.assertEqual(inner.reads, 1)
                return super(Once, inner).read(size)
        stream = Once(self.token + '\n')
        self.assertIn('Parent must verify', m.run(IMAGE, True, stream))
        steps = self.mutations()
        self.assertEqual([(s[0], s[1]) for s in steps],
                         [('container', 'create'), ('container', 'start'), ('exec', '-i'),
                          ('container', 'create'), ('container', 'start')])
        self.assertEqual(steps[-1], ['container', 'start', m.NAME])
        temporary, permanent = steps[0], steps[3]
        for command in (temporary, permanent):
            for key, value in [('--memory', '12g'), ('--memory-swap', '12g'), ('--cpus', '4'),
                               ('--pids-limit', '4096'), ('--cgroup-parent', 'dealpipe-ci2.slice'),
                               ('--network', 'host'), ('--user', '0:0')]:
                self.assertEqual(command[command.index(key)+1], value)
            self.assertIn('--init', command)
            self.assertEqual(command.count('--env'), 2 if command is temporary else 10)
            self.assertFalse(any('registration-token:' in a or 'github-token' in a for a in command))
        self.assertEqual(temporary.count('--mount'), 2)
        self.assertNotIn('docker.sock', ' '.join(temporary))
        self.assertEqual(temporary[temporary.index('--log-driver')+1], 'none')
        self.assertIn('RUNNER_LABEL=dealpipe-ci2-canary', permanent)
        self.assertNotIn('RUNNER_LABEL=dealpipe-nas-ci', permanent)
        self.assertIn('AGENT_CI_PROFILE=ci2', permanent)
        self.assertIn('AGENT_CI_EXCLUSIVE=0', permanent)
        self.assertIn('AGENT_CI_EXPIRED_POLICY_FALLBACK=1', permanent)
        self.assertIn('AGENT_CI_ENGINE_LOCK=/run/ci-engine.lock', permanent)
        self.assertIn('type=bind,src=' + m.WORK + ',dst=' + m.WORK, permanent)
        self.assertIn('type=bind,src=' + str(self.socket) + ',dst=/var/run/docker.sock', permanent)
        self.assertTrue((self.root / '.canary-attempt').exists())
        self.assertEqual(stat.S_IMODE((self.root / '.canary-attempt').stat().st_mode), 0o600)
        # Re-running cannot reuse persistent credentials or create another worker.
        before = len(self.calls)
        with self.assertRaises(m.Refused):
            m.run(IMAGE, True, io.StringIO(self.token))
        self.assertEqual(len(self.calls), before)

    def test_mounts_match_parallel_contract_without_host_token_file(self):
        self.patch(m, 'lane', self.real_lane)
        spec = m.lane()
        expected = {f'{self.root}/runner:/runner', f'{self.root}/home:/home/ci',
                    f'{m.WORK}:{m.WORK}', f'{self.socket}:/var/run/docker.sock',
                    '/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock',
                    '/var/lib/agent-workloads/ci-admission:/run/ci-admission',
                    '/var/lib/agent-workloads/ci-policy:/run/ci-policy:ro',
                    '/sys/fs/cgroup/user.slice/user-1102.slice:/run/ci-pool:ro',
                    '/var/lib/agent-workloads/ci-2.lock:/run/ci-engine.lock'}
        self.assertEqual(set(spec['volumes']), expected)
        self.assertEqual(len(spec['volumes']), len(expected))

    def test_engine_and_image_boundaries_refuse_before_creation(self):
        for field, value in [('DockerRootDir', '/host/docker'), ('SecurityOptions', []),
                             ('CgroupDriver', 'cgroupfs'), ('CgroupVersion', '1'), ('Containers', 1)]:
            saved = copy.deepcopy(self.info)
            self.info[field] = value
            with self.subTest(field=field), self.assertRaises(m.Refused):
                m.run(IMAGE, True, io.StringIO(self.token))
            self.info = saved
            self.assertEqual(self.mutations(), [])
        for modify in (lambda d: d.update(Id='different'),
                       lambda d: d['Config'].update(Entrypoint=['/bin/bash']),
                       lambda d: d['Config'].update(Env=[]),
                       lambda d: d['Config']['Env'].append('GITHUB_TOKEN=private'),
                       lambda d: d['Config'].update(Volumes={'/controller': {}})):
            saved = copy.deepcopy(self.image)
            modify(self.image)
            with self.assertRaises(m.Refused):
                m.run(IMAGE, True, io.StringIO(self.token))
            self.image = saved
        self.assertEqual(self.mutations(), [])
        self.inventory = b'"existing-canary"\n'
        with self.assertRaises(m.Refused):
            m.run(IMAGE, True, io.StringIO(self.token))
        self.assertEqual(self.mutations(), [])

    def test_policy_failure_never_consumes_token_or_creates(self):
        for field, value in [('enabled', False), ('expiresAt', time.time()-1),
                             ('reserveMiB', 1), ('profiles', {'ci2': {'memoryMiB': None, 'cpuPercent': 400}})]:
            old = copy.deepcopy(self.policy_data)
            self.policy_data[field] = value
            self.save_policy()
            stream = io.StringIO(self.token)
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                m.run(IMAGE, True, stream)
            self.assertEqual(stream.tell(), 0)
            self.assertEqual(self.mutations(), [])
            self.policy_data = old
        self.policy.unlink()
        with self.assertRaises(FileNotFoundError):
            m.run(IMAGE)

    def test_private_state_socket_identity_and_immutable_image(self):
        for image in ('latest', 'repo@' + IMAGE, m.OLD_IMAGE, 'sha256:' + 'A'*64):
            with self.assertRaises(m.Refused):
                m.run(image)
        self.patch(m.os, 'geteuid', lambda: 0)
        with self.assertRaises(m.Refused):
            m.run(IMAGE)
        self.patch(m.os, 'geteuid', lambda: 1102)
        (self.root / 'runner').chmod(0o755)
        with self.assertRaises(m.Refused):
            m.run(IMAGE)
        (self.root / 'runner').chmod(0o700)
        (self.root / 'runner/.credentials').touch()
        with self.assertRaises(m.Refused):
            m.run(IMAGE)
        (self.root / 'runner/.credentials').unlink()
        (self.root / 'home').rmdir()
        (self.root / 'home').symlink_to(self.root / 'runner')
        with self.assertRaises(m.Refused):
            m.run(IMAGE)
        (self.root / 'home').unlink()
        (self.root / 'home').mkdir(mode=0o700)
        self.socket.unlink()
        self.socket.touch()
        with self.assertRaises(m.Refused):
            m.run(IMAGE)
        self.assertEqual(self.mutations(), [])

    def test_failure_retains_state_never_stops_even_a_running_permanent_canary(self):
        for stage in ('create', 'exec', 'wait', 'permanent-start'):
            with self.subTest(stage=stage):
                self.calls.clear()
                for p in (self.root / 'runner').iterdir():
                    p.unlink()
                (self.root / '.canary-attempt').unlink(missing_ok=True)
                self.failure = lambda a: (a[:2] == ['container', stage] if stage == 'create' else
                                          a[0] == 'exec' if stage == 'exec' else
                                          a[:2] == ['container', 'wait'] if stage == 'wait' else
                                          a == ['container', 'start', m.NAME])
                with self.assertRaises(m.Refused) as failure:
                    m.run(IMAGE, True, io.StringIO(self.token))
                self.assertNotIn(self.token, str(failure.exception))
                self.assertTrue((self.root / '.canary-attempt').exists())
                self.assertFalse(any(a[2] in ('stop', 'rm', 'kill') for a, _ in self.calls))
                if stage == 'permanent-start':
                    # Simulate daemon starting Listener but client timing out.
                    self.assertEqual(self.calls[-1][0][-1], m.NAME)
                else:
                    self.assertFalse(any(a[2:] == ['container', 'start', m.NAME] for a, _ in self.calls))

    def test_running_config_or_wrong_registration_never_starts_listener(self):
        for running, bad in ((True, False), (False, True)):
            self.config_running, self.bad_registration = running, bad
            self.calls.clear()
            for p in (self.root / 'runner').iterdir():
                p.unlink()
            (self.root / '.canary-attempt').unlink(missing_ok=True)
            with self.assertRaises(m.Refused):
                m.run(IMAGE, True, io.StringIO(self.token))
            self.assertFalse(any(a[2:] == ['container', 'start', m.NAME] for a, _ in self.calls))

    def test_claim_collision_and_stdin_deadline_never_mutate_docker(self):
        (self.root / '.canary-attempt').touch()
        with self.assertRaises(m.Refused):
            m.run(IMAGE, True, io.StringIO(self.token))
        (self.root / '.canary-attempt').unlink()
        # Model a producer that never closes: invoke the installed alarm handler.
        class Stalled:
            def read(inner, size):
                m.signal.getsignal(m.signal.SIGALRM)(m.signal.SIGALRM, None)
        previous = m.signal.getsignal(m.signal.SIGALRM)
        with self.assertRaises(m.Refused):
            m.run(IMAGE, True, Stalled())
        self.assertEqual(m.signal.getsignal(m.signal.SIGALRM), previous)
        self.assertEqual(self.mutations(), [])
        self.assertTrue((self.root / '.canary-attempt').exists())

    def test_failed_exit_and_invalid_docker_output_are_redacted(self):
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout=self.token.encode(), stderr=self.token.encode())):
            with self.assertRaises(m.Refused) as error:
                m.docker(['info'], output=True)
            self.assertNotIn(self.token, str(error.exception))
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=self.token.encode())):
            with self.assertRaises(m.Refused) as error:
                m.docker(['info'], output=True)
            self.assertNotIn(self.token, str(error.exception))

    def test_invalid_stdin_and_redaction(self):
        for token in ('', 'one\ntwo', 'x'*4097, 'token with spaces'):
            (self.root / '.canary-attempt').unlink(missing_ok=True)
            with self.assertRaises(m.Refused):
                m.run(IMAGE, True, io.StringIO(token))
            self.assertEqual(self.mutations(), [])
        with patch.object(m, 'run', side_effect=RuntimeError(self.token)), \
             patch.object(sys, 'argv', ['helper', '--image', IMAGE, '--apply']), \
             patch.object(sys, 'stderr', new_callable=io.StringIO) as error:
            self.assertEqual(m.main(), 1)
            self.assertNotIn(self.token, error.getvalue())
            self.assertIn('retain ALL state', error.getvalue())


if __name__ == '__main__':
    unittest.main()
