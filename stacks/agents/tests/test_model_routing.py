import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import coordinator


class ModelRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((Path(__file__).parents[1] / 'coordinator.json').read_text())

    def route(self, role, risk='routine', paths=(), **kwargs):
        return coordinator.model_route(self.config, role, risk, paths, **kwargs)

    def test_routine_review_uses_small_model_with_explicit_standard_tier(self):
        result = self.route('review', paths=['apps/website/src/button.tsx'])
        self.assertEqual(result['target']['model'], 'gpt-6-luna')
        self.assertEqual(result['target']['options']['serviceTier'], 'default')

    def test_sensitive_work_never_uses_routine_implementation_or_review(self):
        for role in ('implementation', 'review'):
            for path in ('packages/db/roles.ts', 'apps/api/auth/login.ts',
                         'packages/db/migrations/001.sql', 'machines/nas/configuration.nix',
                         '.github/workflows/check.yml', 'apps/api/billing/webhook.ts'):
                with self.subTest(role=role, path=path):
                    self.assertEqual(self.route(role, paths=[path])['target']['model'], 'gpt-6.1-sol')

    def test_risk_and_missing_ownership_are_conservative(self):
        for risk in ('high', 'uncertain'):
            self.assertEqual(self.route('review', risk, ['src/button.tsx'])['profile'], 'escalation')
        self.assertEqual(self.route('implementation')['profile'], 'escalation')

    def test_queue_and_command_execution_stay_on_small_model(self):
        for role in ('coordinator', 'verification'):
            self.assertEqual(self.route(role, 'high', ['machines/nas/configuration.nix'])['target']['model'], 'gpt-6-luna')

    def test_escalation_budget_stops_instead_of_spawning_more_workers(self):
        with self.assertRaisesRegex(coordinator.QueueError, 'budget exhausted'):
            self.route('escalation', escalations_used=1)
        with self.assertRaisesRegex(coordinator.QueueError, 'budget exhausted'):
            self.route('review', paths=['packages/db/migrations/001.sql'], escalations_used=1)

    def test_routing_rejects_missing_configuration_and_path_traversal(self):
        with self.assertRaises(coordinator.QueueError):
            coordinator.model_route({}, 'review')
        for path in ('../auth/login.ts', '/etc/passwd', 'src/../roles.ts', 'C:\\auth\\login.ts'):
            with self.subTest(path=path), self.assertRaises(coordinator.QueueError):
                self.route('review', paths=[path])


if __name__ == '__main__':
    unittest.main()
