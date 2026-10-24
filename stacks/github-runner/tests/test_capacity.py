"""Offline policy regressions: no engines, API calls, or service starts."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capacity", ROOT / "capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.limits = {"enable": True, "engineReserveMiB": 4096}
        for name, count, memory, cpu in (("ciPool", 1, 32768, 800), ("ci", 1, 8192, 400),
                                         ("browser", 1, 8192, 400), ("static", 2, 4096, 200)):
            self.limits[name] = dict(count=count, memoryMiB=memory, cpuPercent=cpu)
        self.compose = {"services": {}}
        for service, state in (("runner", ""), ("browser", "/browser"), ("static", "/static")):
            self.compose["services"][service] = {
                "volumes": [f"/var/lib/dealpipe-ci{state}/runner:/runner",
                            f"/var/lib/dealpipe-ci{state}/work:/var/lib/dealpipe-ci{state}/work",
                            "/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock"],
                "environment": {"RUNNER_LABEL": "dealpipe-nas-" + service,
                                "RUNNER_WORK_DIRECTORY": f"/var/lib/dealpipe-ci{state}/work"}}
        for lane in ("runner", "browser"):
            self.compose["services"][lane]["volumes"].append("/run/user/1102/docker.sock:/var/run/docker.sock")

    def test_parallel_light_jobs_never_get_stack_socket_or_lock(self):
        result = capacity.render(self.compose, self.limits)["services"]
        for lane in ("static", "static-2"):
            self.assertFalse(any("docker.sock" in v for v in result[lane]["volumes"]))
            self.assertTrue(any("/static.lock:" in v for v in result[lane]["volumes"]))
            self.assertEqual(result[lane]["environment"]["AGENT_CI_EXCLUSIVE"], "0")
        self.assertIn("/var/lib/dealpipe-ci/static-2/runner:/runner", result["static-2"]["volumes"])
        self.assertEqual(result["static-2"]["environment"]["RUNNER_NAME"], "dealpipe-nas-static-2")
        self.assertEqual(result["static-2"]["environment"]["RUNNER_LABEL"], result["static"]["environment"]["RUNNER_LABEL"])
        for lane in ("runner", "browser"):
            self.assertEqual(result[lane]["environment"]["AGENT_CI_EXCLUSIVE"], "1")
            self.assertTrue(any("/heavy.lock:" in v for v in result[lane]["volumes"]))
            self.assertEqual(result[lane]["mem_limit"], result[lane]["memswap_limit"])

    def test_rejects_port_colliding_replicas_and_overcommit(self):
        for pool in ("ci", "browser"):
            limits = copy.deepcopy(self.limits)
            limits[pool]["count"] = 2
            with self.assertRaises(ValueError):
                capacity.render(copy.deepcopy(self.compose), limits)
        self.limits["ciPool"]["memoryMiB"] = 16384
        with self.assertRaises(ValueError):
            capacity.render(self.compose, self.limits)

    def test_requires_explicit_capacity_activation(self):
        self.limits["enable"] = False
        with self.assertRaises(ValueError):
            capacity.render(self.compose, self.limits)


if __name__ == "__main__":
    unittest.main()
