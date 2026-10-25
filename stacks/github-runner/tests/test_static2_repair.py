"""Offline repair boundaries; every engine operation and signal is mocked."""
import copy
from contextlib import nullcontext
import importlib.util
import io
import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('static2_repair', Path(__file__).parents[1] / 'repair-static2.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
OLD = '6f19fc9624c81007d64d5161b1162c2a8ac1a77638551ecafef360a01409f44c'
NEW = 'a' * 64


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.calls, self.signals = [], []
        self.fail = None
        self.worker = False
        self.checks = 0
        for key, value in dict(ROOT=root / 'static-2', POOL=root / 'pool', HEAVY=root / 'heavy',
                               POLICY=root / 'policy' / 'policy.json', LEDGER=root / 'ledger').items():
            self.patch(m, key, value)
        for path in (m.ROOT, m.POOL, m.LEDGER, m.POLICY.parent):
            path.mkdir(mode=0o700)
        for name in ('runner', 'home', 'work'):
            (m.ROOT / name).mkdir(mode=0o700)
        # Legacy cp-a layout, protected by ROOT; fixture models mapped ownership below.
        (m.ROOT / 'runner').chmod(0o777)
        (m.ROOT / '.static-replica.json').write_text(json.dumps({'managed': m.NAME, 'image': m.base.IMAGE}))
        (m.ROOT / 'runner' / '.runner').write_text('\ufeff' + json.dumps(dict(agentId=121,
            agentName='dealpipe-nas-static-2', gitHubUrl='https://github.com/dealpipe/dealpipe',
            workFolder=str(m.ROOT / 'work'))))
        for name in ('run.sh', '.credentials', '.credentials_rsaparams'):
            (m.ROOT / 'runner' / name).write_text('SECRET must never be read')
        (m.ROOT / 'runner' / 'run.sh').chmod(0o755)
        for path in (m.HEAVY, m.LEDGER / 'mutex.lock'):
            path.touch()
        for name, value in {'memory.max': str(32*m.base.GIB), 'memory.swap.max': '0',
                            'cpu.max': '800000 100000', 'memory.current': str(5*m.base.GIB),
                            'memory.stat': f'anon {4*m.base.GIB}\nkernel {m.base.GIB}\nfile 0\nshmem 0\ninactive_file 0\nfile_dirty 0\nfile_writeback 0\n',
                            'memory.events': 'oom 1\noom_kill 1\n'}.items():
            (m.POOL / name).write_text(value)
        self.policy = dict(enabled=True, evidence='offline fixture, full hard ceiling UNVERIFIED',
                           verifiedAt=1000000000, expiresAt=1000000100, reserveMiB=11865,
                           measuredHeadroomMiB=34797, maxConcurrentMiB=22932, cpuPercent=800,
                           profiles={'static-2': dict(memoryMiB=12288, cpuPercent=200)})
        self.save_policy()
        self.meminfo = 'MemTotal: 48596992 kB\nMemAvailable: 35632128 kB\n'
        original_read, original_stat = Path.read_text, Path.stat
        def read(path, *args, **kwargs):
            if str(path) == '/proc/meminfo':
                return self.meminfo
            if path.name in ('.credentials', '.credentials_rsaparams'):
                self.failTest('Credential contents read')
            return original_read(path, *args, **kwargs)
        def filesystem_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            values = list(result)
            values[4] = 232072 if path == m.ROOT / 'runner' else 1102
            return type(result)(values)
        self.patch(Path, 'read_text', read)
        self.patch(Path, 'stat', filesystem_stat)
        self.patch(m.os, 'geteuid', return_value=1102)
        self.patch(m, 'docker', side_effect=self.docker)
        self.patch(m, 'inspect', side_effect=lambda name: copy.deepcopy(self.containers.get(name)))
        self.patch(m, 'lock', return_value=nullcontext())
        self.reservation = self.patch(m, 'capacity_reservation', return_value=nullcontext())
        self.patch(m, 'freeze', side_effect=self.freeze)
        self.patch(m, 'quiesced', side_effect=self.quiesced)
        self.patch(m, 'identity', return_value='starttime')
        self.patch(m.signal, 'pidfd_send_signal', side_effect=lambda fd, sig: self.signals.append(sig))
        self.patch(m.time, 'sleep')
        self.containers = {m.NAME: self.container(False)}
        self.args = SimpleNamespace(expected_container_id=OLD, apply=False)

    def patch(self, obj, name, *args, **kwargs):
        patcher = patch.object(obj, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def save_policy(self):
        m.POLICY.write_text(json.dumps(self.policy))

    def container(self, new):
        return dict(Id=NEW if new else OLD, Name='/' + m.NAME, Image=m.IMAGE if new else m.base.IMAGE,
            State=dict(Running=not new, Status='created' if new else 'running'),
            Config=dict(User='0:0', Entrypoint=['/usr/local/bin/ci-runner'], Cmd=None,
                Env=[f'{k}={v}' for k, v in dict(m.NATIVE if new else {}, **(m.environment() if new else
                    dict(m.base.ENV, RUNNER_WORK_DIRECTORY=str(m.ROOT / 'work')))).items()],
                Labels={m.base.LABEL: m.NAME, m.base.ATTEMPT: OLD, 'com.docker.compose.project': 'dealpipe-ci',
                        'com.docker.compose.service': 'static-2'}),
            HostConfig=dict(NetworkMode='host', Privileged=False, Memory=(12 if new else 8)*m.base.GIB,
                MemorySwap=(12 if new else 8)*m.base.GIB, NanoCpus=2000000000, PidsLimit=2048,
                Init=True, RestartPolicy=dict(Name='unless-stopped')),
            Mounts=[dict(Source=s, Destination=t, RW=rw, Type='bind') for s, t, rw in m.mounts(new)])

    def docker(self, *args):
        self.calls.append(args)
        if args[0] == self.fail:
            raise RuntimeError('SECRET engine error')
        if args[0] == 'info':
            return json.dumps(dict(SecurityOptions=['name=rootless']))
        if args[:2] == ('image', 'inspect'):
            return json.dumps([dict(Id=args[2], Config=dict(Env=[f'{k}={v}' for k,v in
                (m.NATIVE if args[2] == m.IMAGE else {}).items()], Entrypoint=['/usr/local/bin/ci-runner']))])
        if args[0] == 'update':
            self.containers[m.NAME]['HostConfig']['RestartPolicy']['Name'] = 'no'
        elif args[0] == 'stop':
            self.assertFalse(self.worker, 'Never stop a Worker')
            self.assertGreaterEqual(self.checks, 3)
            self.containers[m.NAME]['State'] = dict(Running=False, Status='exited')
        elif args[0] == 'rename':
            self.containers[m.BACKUP] = self.containers.pop(m.NAME)
            self.containers[m.BACKUP]['Name'] = '/' + m.BACKUP
        elif args[0] == 'create':
            self.containers[m.NAME] = self.container(True)
        elif args[0] == 'start':
            self.containers[m.NAME]['State'] = dict(Running=True, Status='running')
        else:
            self.failTest(f'Unexpected Docker operation {args[0]}')
        return ''

    def freeze(self, container_id, stack):
        self.assertEqual(container_id, OLD)
        self.signals.append(signal.SIGSTOP)
        return (4321, 'starttime', 99)

    def quiesced(self, container_id, frozen):
        self.checks += 1
        self.assertEqual(self.signals, [signal.SIGSTOP])
        m.require(not self.worker, 'Worker accepted before freeze')

    def execute(self, apply=False):
        self.args.apply = apply
        with patch('sys.stdout', io.StringIO()):
            m.execute(self.args)

    def mutations(self):
        return [row for row in self.calls if row[0] not in ('info', 'image')]

    def test_dry_run_no_signals_claim_or_engine_mutation(self):
        self.execute()
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.signals, [])
        self.assertFalse((m.ROOT / m.CLAIM).exists())

    def test_apply_retains_registration_old_backup_and_exact_native_candidate(self):
        before = {p: p.read_bytes() for p in (m.ROOT / 'runner').iterdir()}
        self.execute(True)
        self.assertEqual(before, {p: p.read_bytes() for p in (m.ROOT / 'runner').iterdir()})
        self.assertEqual([a[0] for a in self.mutations()], ['update','stop','rename','create','start'])
        self.assertFalse(self.containers[m.BACKUP]['State']['Running'])
        self.assertTrue(self.containers[m.NAME]['State']['Running'])
        create = next(a for a in self.calls if a[0] == 'create')
        self.assertIn('--memory=12g', create)
        self.assertIn('--memory-swap=12g', create)
        self.assertIn('AGENT_CI_PROFILE=static-2', create)
        self.assertIn('AGENT_CI_EXPIRED_POLICY_FALLBACK=1', create)
        self.assertFalse(any('sock' in a or 'token' in a or 'ci-engine' in a for a in create))
        self.reservation.assert_called_once_with(m.LEDGER,m.POLICY,'static-2','/proc/meminfo',m.POOL,exclusive=True)
        self.assertTrue((m.ROOT / m.CLAIM).exists())
        with self.assertRaises(RuntimeError):
            self.execute(True)

    def test_worker_accepted_between_preflight_and_freeze_resumes_without_stop(self):
        original = self.freeze
        def race(*args):
            result = original(*args)
            self.worker = True
            return result
        with patch.object(m, 'freeze', side_effect=race), self.assertRaises(RuntimeError):
            self.execute(True)
        self.assertEqual(self.signals, [signal.SIGSTOP, signal.SIGCONT])
        self.assertEqual(self.mutations(), [])
        self.assertTrue((m.ROOT / m.CLAIM).exists())

    def test_failures_keep_every_artifact_never_cleanup_or_resume_after_stop(self):
        for failure in ('update', 'stop', 'rename', 'create', 'start'):
            with self.subTest(failure=failure):
                self.containers = {m.NAME:self.container(False)}
                (m.ROOT / m.CLAIM).unlink(missing_ok=True)
                self.calls.clear(); self.signals.clear(); self.checks=0
                self.fail=failure
                with self.assertRaises(RuntimeError):
                    self.execute(True)
                self.assertTrue((m.ROOT / m.CLAIM).exists())
                self.assertFalse(any(a[0] in ('rm','kill','run') for a in self.calls))
                self.assertEqual(self.signals, [signal.SIGSTOP, signal.SIGCONT] if failure=='update' else [signal.SIGSTOP])
                if failure in ('create','start'):
                    self.assertIn(m.BACKUP, self.containers)
                if failure=='start':
                    self.assertIn(m.NAME, self.containers)

    def test_identity_drift_mounts_limits_and_private_overrides_fail_before_mutation(self):
        old = self.container(False)
        changes = [lambda c:c.update(Id=NEW), lambda c:c.update(Image=m.IMAGE),
            lambda c:c['HostConfig'].update(Memory=12*m.base.GIB),
            lambda c:c['Mounts'].append(dict(Source='/secret/token',Destination='/secret',RW=False,Type='bind')),
            lambda c:c['Config']['Env'].append('GITHUB_TOKEN=SECRET'),
            lambda c:c['Config']['Env'].append('AGENT_CI_ENGINE_LOCK=/socket'),
            lambda c:c['HostConfig'].update(Privileged=True)]
        for change in changes:
            self.containers[m.NAME]=copy.deepcopy(old)
            change(self.containers[m.NAME])
            with self.assertRaises(RuntimeError):
                self.execute(True)
            self.assertEqual(self.mutations(), [])
            self.assertFalse((m.ROOT / m.CLAIM).exists())

    def test_capacity_policy_parent_reserve_and_full_hard_ceiling(self):
        for field, value in [('enabled', False), ('expiresAt', m.time.time()+3600),
                             ('reserveMiB',11864), ('maxConcurrentMiB',10000)]:
            saved = copy.deepcopy(self.policy)
            self.policy[field]=value; self.save_policy()
            with self.assertRaises((RuntimeError,ValueError)):
                self.execute(True)
            self.policy=saved; self.save_policy()
        self.policy['profiles']['static-2']['memoryMiB']=8192; self.save_policy()
        with self.assertRaises(RuntimeError): self.execute(True)
        self.policy['profiles']['static-2']['memoryMiB']=12288; self.save_policy()
        self.meminfo='MemTotal: 48596992 kB\nMemAvailable: 20000000 kB\n'
        with self.assertRaises(RuntimeError): self.execute(True)
        self.meminfo='MemTotal: 48596992 kB\nMemAvailable: 35632128 kB\n'
        (m.POOL/'memory.swap.max').write_text('1')
        with self.assertRaises(RuntimeError): self.execute(True)
        self.assertEqual(self.mutations(), [])

    def test_bom_registration_and_private_ancestor_accept_legacy_runner_mode(self):
        self.execute()
        (m.ROOT/'home').chmod(0o755)
        with self.assertRaises(RuntimeError): self.execute(True)
        (m.ROOT/'home').chmod(0o700)
        (m.ROOT/'runner'/'.runner').write_text('{"agentId":122}')
        with self.assertRaises(RuntimeError): self.execute(True)
        self.assertEqual(self.mutations(), [])

    def test_changed_frozen_identity_never_signals_replacement(self):
        self.worker=True
        with patch.object(m,'identity',return_value='different-start'), self.assertRaises(RuntimeError):
            self.execute(True)
        self.assertEqual(self.signals,[signal.SIGSTOP])
        self.assertEqual(self.mutations(),[])

    def test_admission_failure_retains_claim_without_signal_or_stop(self):
        with patch.object(m,'capacity_reservation',side_effect=TimeoutError('busy')), self.assertRaises(TimeoutError):
            self.execute(True)
        self.assertTrue((m.ROOT/m.CLAIM).exists())
        self.assertEqual(self.signals,[])
        self.assertEqual(self.mutations(),[])

    def test_candidate_drift_never_starts_or_removes_retained_containers(self):
        original=self.docker
        def drift(*args):
            result=original(*args)
            if args[0]=='create':
                self.containers[m.NAME]['HostConfig']['Memory']=8*m.base.GIB
            return result
        with patch.object(m,'docker',side_effect=drift), self.assertRaises(RuntimeError):
            self.execute(True)
        self.assertIn(m.BACKUP,self.containers)
        self.assertIn(m.NAME,self.containers)
        self.assertFalse(any(a[0] in ('start','rm') for a in self.calls))

    def test_new_oom_after_start_never_stops_running_candidate(self):
        original=self.docker
        def oom(*args):
            result=original(*args)
            if args[0]=='start':
                (m.POOL/'memory.events').write_text('oom 2\noom_kill 2\n')
            return result
        with patch.object(m,'docker',side_effect=oom), self.assertRaises(RuntimeError):
            self.execute(True)
        self.assertTrue(self.containers[m.NAME]['State']['Running'])
        self.assertEqual(sum(a[0]=='stop' for a in self.calls),1)
        self.assertFalse(any(a[0]=='rm' for a in self.calls))

    def test_existing_backup_and_symlink_state_refuse_before_claim(self):
        self.containers[m.BACKUP]=self.container(False)
        with self.assertRaises(RuntimeError): self.execute(True)
        del self.containers[m.BACKUP]
        home=m.ROOT/'home'
        home.rmdir(); home.symlink_to(m.ROOT/'work')
        with self.assertRaises(RuntimeError): self.execute(True)
        self.assertFalse((m.ROOT/m.CLAIM).exists())
        self.assertEqual(self.mutations(),[])

    def test_public_failure_reports_only_stage(self):
        self.fail='start'
        with patch.object(m.sys, 'argv', ['repair','--apply','--expected-container-id',OLD]), \
             patch('sys.stdout',io.StringIO()) as out, patch('sys.stderr',io.StringIO()) as err:
            self.assertEqual(m.main(),1)
        self.assertIn('start-candidate',err.getvalue())
        self.assertNotIn('SECRET',out.getvalue()+err.getvalue())


class ProcessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)
        self.write_process(90, 'tini', 1)
        self.write_process(100, 'bash', 90)
        self.write_process(101, 'Runner.Listener', 100)
        path_type = Path
        def proc_path(value):
            return self.proc if str(value) == '/proc' else path_type(value)
        patcher = patch.object(m, 'Path', side_effect=proc_path)
        patcher.start(); self.addCleanup(patcher.stop)
        self.rows = {90:'tini',100:'bash',101:'Runner.Listener'}

    def write_process(self, pid, name, parent):
        proc = self.proc / str(pid)
        proc.mkdir(exist_ok=True)
        (proc/'status').write_text(f'Name: {name}\nState: S (sleeping)\nUid: 1102 1102 1102 1102\n')
        (proc/'cgroup').write_text(f'0::/user.slice/docker-{OLD}.scope\n')
        fields = ['S',str(parent)] + ['0']*17 + ['123456']
        (proc/'stat').write_text(f'{pid} ({name}) ' + ' '.join(fields))
        task = proc/'task'/str(pid)
        task.mkdir(parents=True,exist_ok=True)
        (task/'status').write_text('State: T (stopped)\n')
        (task/'children').write_text('')

    def test_top_requires_host_pid_column_and_exact_listener_set(self):
        with patch.object(m,'docker',return_value='PID COMMAND\n101 Runner.Listener\n90 tini\n') as engine:
            self.assertEqual(m.processes(OLD),{101:'Runner.Listener',90:'tini'})
            engine.assert_called_once_with('top',OLD,'-eo','pid,comm')
        for response in ('COMMAND PID\nRunner.Listener 101','PID COMMAND\n1 Runner.Listener','PID COMMAND'):
            with patch.object(m,'docker',return_value=response), self.assertRaises(RuntimeError):
                m.processes(OLD)

    def test_identity_requires_host_uid_and_container_cgroup(self):
        self.assertEqual(m.identity(101,OLD),'123456')
        with self.assertRaises(RuntimeError): m.identity(101,NEW)
        status=self.proc/'101/status'
        status.write_text(status.read_text().replace('1102','232072'))
        with self.assertRaises(RuntimeError): m.identity(101,OLD)

    def test_freeze_uses_pidfd_after_identity_check(self):
        with patch.object(m,'processes',return_value=self.rows), \
             patch.object(m.os,'pidfd_open',return_value=99) as opened, \
             patch.object(m.os,'close') as closed, \
             patch.object(m.signal,'pidfd_send_signal') as sent:
            with m.ExitStack() as stack:
                self.assertEqual(m.freeze(OLD,stack),(101,'123456',99))
                opened.assert_called_once_with(101)
                sent.assert_called_once_with(99,signal.SIGSTOP)
            closed.assert_called_once_with(99)
        with patch.object(m,'processes',return_value={**self.rows,102:'Runner.Listener'}), \
             self.assertRaises(RuntimeError):
            with m.ExitStack() as stack: m.freeze(OLD,stack)

    def test_stopped_requires_every_thread_stopped_and_no_child(self):
        m.stopped(101)
        extra=self.proc/'101/task/102'
        extra.mkdir()
        (extra/'status').write_text('State: S (sleeping)\n')
        (extra/'children').write_text('')
        with self.assertRaises(RuntimeError): m.stopped(101)
        (extra/'status').write_text('State: T (stopped)\n')
        (extra/'children').write_text('103')
        with self.assertRaises(RuntimeError): m.stopped(101)

    def test_quiescence_refuses_worker_unknown_and_sibling_shell(self):
        with patch.object(m,'processes',side_effect=lambda _:self.rows):
            m.quiesced(OLD,(101,'123456',99))
            for name in ('Runner.Worker','dotnet','bash'):
                self.write_process(102,name,100)
                self.rows[102]=name
                with self.assertRaises(RuntimeError): m.quiesced(OLD,(101,'123456',99))
                del self.rows[102]
            self.rows[103]='Runner.Listener'
            with self.assertRaises(RuntimeError): m.quiesced(OLD,(101,'123456',99))

    def test_quiescence_accepts_run_helper_only_in_the_parent_chain(self):
        self.write_process(90, 'docker-init', 1)
        self.write_process(100, 'run.sh', 90)
        self.write_process(105, 'run-helper.sh', 100)
        self.write_process(101, 'Runner.Listener', 105)
        self.rows = {90: 'docker-init', 100: 'run.sh', 105: 'run-helper.sh', 101: 'Runner.Listener'}
        with patch.object(m, 'processes', side_effect=lambda _: self.rows):
            m.quiesced(OLD, (101, '123456', 99))
            self.write_process(106, 'run-helper.sh', 90)
            self.rows[106] = 'run-helper.sh'
            with self.assertRaises(RuntimeError):
                m.quiesced(OLD, (101, '123456', 99))


if __name__ == '__main__':
    unittest.main()
