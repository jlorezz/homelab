"""Offline tests for empty CI network removal; the live engine is never used."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import network_reaper

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
OLD = "2026-10-24T07:26:40.928672117+02:00"


def network(name, created=OLD, containers=None):
    return {"Id": "id-" + name, "Name": name, "Created": created, "Containers": containers or {}}


class FakeDocker:
    def __init__(self, networks, refuse=()):
        self.networks, self.refuse, self.removed = networks, set(refuse), []

    def __call__(self, *args, timeout=None):
        self.timeouts = getattr(self, 'timeouts', []) + [timeout]
        if args[:2] == ("network", "ls"):
            return "\n".join(n["Id"] for n in self.networks)
        if args[:2] == ("network", "inspect"):
            return json.dumps([n for n in self.networks if n["Id"] in args[2:]])
        if args[:2] == ("network", "rm"):
            if args[2] in self.refuse:
                raise subprocess.CalledProcessError(1, args)
            self.removed.append(args[2])
            return args[2]
        raise AssertionError(args)


class NetworkReaperTests(unittest.TestCase):
    def test_removes_only_empty_ci_networks_from_the_incident(self):
        docker = FakeDocker([
            network("ci-40064022103-1-browser-1_default"),
            network("supabase_network_ci-40080808448-1-build-0"),
            network("supabase_network_ci-40091909824-2-coverage-services"),
            network("dealpipe-wt-2d4ca0f0a1afc0744b813449"),
            network("bridge"), network("host"), network("none"),
            network("portainer-management"),
        ])
        removed = network_reaper.reap(NOW, run=docker)
        self.assertEqual(removed, [
            "ci-40064022103-1-browser-1_default",
            "supabase_network_ci-40080808448-1-build-0",
            "supabase_network_ci-40091909824-2-coverage-services",
        ])
        self.assertNotIn("id-dealpipe-wt-2d4ca0f0a1afc0744b813449", docker.removed)

    def test_keeps_attached_recent_and_lookalike_networks(self):
        docker = FakeDocker([
            network("ci-40064022103-1-browser-1_default", containers={"abc": {"Name": "db"}}),
            network("ci-40064022103-1-browser-2_default", created="2026-10-24T14:59:30Z"),
            network("ci-local-1-browser_default"),
            network("ci-123-1-short-run-id_default"),
            network("my-ci-40064022103-1-x_default"),
            network("supabase_network_dealpipe-wt-abc"),
        ])
        self.assertEqual(network_reaper.reap(NOW, run=docker), [])
        self.assertEqual(docker.removed, [])

    def test_in_use_race_is_skipped_not_fatal(self):
        docker = FakeDocker([network("ci-40064022103-1-build-0_default"),
                             network("ci-40064022225-1-build-0_default")],
                            refuse={"id-ci-40064022103-1-build-0_default"})
        self.assertEqual(network_reaper.reap(NOW, run=docker), ["ci-40064022225-1-build-0_default"])

    def test_sweep_stops_at_its_budget_and_no_call_overruns_it(self):
        clock = [0.0]

        class Slow(FakeDocker):
            def __call__(self, *args, timeout=None):
                result = super().__call__(*args, timeout=timeout)
                if args[:2] == ("network", "rm"):
                    clock[0] += 25  # each removal is slow
                return result

        docker = Slow([network(f"ci-4006402210{i}-1-build-0_default") for i in range(5)])
        with patch("builtins.print"):
            removed = network_reaper.reap(NOW, run=docker, clock=lambda: clock[0], budget=60)
        # Removals start at t=0, 25 and 50; at t=75 the sweep stops.
        self.assertEqual(len(removed), 3)
        # ls, inspect and the first two removals get the full 15 s; the third only what is left.
        self.assertEqual(docker.timeouts, [15, 15, 15, 15, 10])

    def test_timestamp_formats(self):
        self.assertEqual(network_reaper.created_at("2026-10-24T13:00:00Z"),
                         datetime(2026, 10, 24, 13, tzinfo=timezone.utc))
        self.assertEqual(network_reaper.created_at(OLD).utcoffset().total_seconds(), 7200)
        with self.assertRaises(ValueError):
            network_reaper.created_at("yesterday")

    def test_best_effort_never_raises_and_skips_without_engine(self):
        with patch.object(network_reaper, "SOCKET", Path("/nonexistent/docker.sock")), \
                patch.dict("os.environ", {}, clear=True), \
                patch.object(network_reaper, "reap", side_effect=AssertionError("must not run")):
            network_reaper.reap_best_effort("started")
        with patch.dict("os.environ", {"DOCKER_HOST": "unix:///x"}, clear=True), \
                patch.object(network_reaper, "reap", side_effect=AssertionError("not opted in")):
            network_reaper.reap_best_effort("started")
        with patch.dict("os.environ", {"DOCKER_HOST": "unix:///x", "AGENT_CI_NETWORK_HYGIENE": "1"}), \
                patch.object(network_reaper, "reap", side_effect=subprocess.TimeoutExpired("docker", 30)), \
                patch("builtins.print") as output:
            network_reaper.reap_best_effort("completed")
        self.assertIn("skipped", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
