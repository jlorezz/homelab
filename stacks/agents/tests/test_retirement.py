import importlib.util
from pathlib import Path
import tempfile
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('retire_hermes', Path(__file__).parents[1] / 'retire-hermes.py')
retirement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)


class API:
    def __init__(self):
        self.calls = []
        self.removed = False
        self.compose = {'services': {'t3': {'image': 'existing:t3'}, 'opencode': {'image': 'existing:opencode'},
                                     'hermes': {'image': 'retired:hermes'}}}

    def request(self, method, route, data=None):
        self.calls.append((method, route, data))
        if route == '/stacks':
            return [{'Id': 1, 'Name': 'homelab-agents', 'EndpointId': 2}]
        if method == 'GET' and route == '/stacks/1':
            return {'Id': 1, 'Env': [{'name': 'AGENT_IMAGE', 'value': 'keep'},
                                   {'name': 'HERMES_IMAGE', 'value': 'remove'}], 'Status': 1}
        if route == '/stacks/1/file':
            return {'StackFileContent': yaml.safe_dump(self.compose)}
        if route.endswith('/containers/json?all=true'):
            return [] if self.removed else [
                {'Id': 'hermes-id', 'State': 'running', 'Labels': {
                    'com.docker.compose.project': 'homelab-agents',
                    'com.docker.compose.service': 'hermes'}},
                {'Id': 'other-hermes', 'State': 'running', 'Labels': {
                    'com.docker.compose.project': 'other', 'com.docker.compose.service': 'hermes'}}]
        if method == 'PUT':
            return {'Status': 1}
        if method == 'DELETE':
            self.removed = True
        return None


class RetirementTests(unittest.TestCase):
    def test_plan_performs_no_mutations(self):
        api = API()
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'backup'
            retirement.retire(api, 2, 'homelab-agents', backup)
            self.assertFalse(backup.exists())
        self.assertTrue(all(method == 'GET' for method, _, _ in api.calls))

    def test_removes_only_owned_container_and_preserves_other_services(self):
        api = API()
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'backup'
            retirement.retire(api, 2, 'homelab-agents', backup, True)
            self.assertEqual((backup / 'env.json').stat().st_mode & 0o777, 0o600)
            self.assertIn('hermes', yaml.safe_load((backup / 'compose.yaml').read_text())['services'])
        update = next(data for method, _, data in api.calls if method == 'PUT')
        self.assertEqual(yaml.safe_load(update['StackFileContent'])['services'], {
            't3': {'image': 'existing:t3'}, 'opencode': {'image': 'existing:opencode'}})
        self.assertEqual(update['Env'], [{'name': 'AGENT_IMAGE', 'value': 'keep'}])
        self.assertFalse(update['Prune'])
        self.assertEqual([route for method, route, _ in api.calls if method == 'DELETE'],
                         ['/endpoints/2/docker/containers/hermes-id'])

    def test_refuses_stack_without_t3(self):
        api = API()
        del api.compose['services']['t3']
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            retirement.retire(api, 2, 'homelab-agents', Path(directory), True)
        self.assertTrue(all(method == 'GET' for method, _, _ in api.calls))
