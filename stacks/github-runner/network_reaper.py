#!/usr/bin/env python3
"""Remove empty Dealpipe CI networks from this lane's own Docker engine.

Every Supabase/Compose job creates bridge networks named after its run
(`ci-<run>-<attempt>-<job>_default`, `supabase_network_ci-<run>-<attempt>-<job>`).
Cancelled jobs, runner loss and incomplete in-job cleanup leave them behind;
Docker's built-in address pools hold about thirty, after which every new job
fails with "all predefined address pools have been fully subnetted".

The job hooks call this while the lane holds its admission and runs nothing
else. Each lane has exactly one Worker per engine, so a matching network with
no attached container cannot belong to a live job. Worktree (`dealpipe-wt-*`)
and all other networks never match. Containers and volumes are never touched.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time

CI_NETWORK = re.compile(r"(?:supabase_network_)?ci-[0-9]{8,}-[0-9]+-[a-z0-9][a-z0-9_.-]*\Z")
SOCKET = Path("/var/run/docker.sock")
MIN_AGE_SECONDS = 60
DOCKER_TIMEOUT = 15
# Whole sweep, so a slow engine cannot hold the lane's admission for long.
SWEEP_SECONDS = 60


def created_at(value):
    # Docker reports nanoseconds; fromisoformat accepts at most microseconds.
    match = re.fullmatch(r"(.+T\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", value)
    if not match:
        raise ValueError(f"Unrecognized network timestamp {value!r}")
    base, fraction, zone = match.groups()
    fraction = ((fraction or "") + "000000")[:6]
    return datetime.fromisoformat(f"{base}.{fraction}{'+00:00' if zone == 'Z' else zone}")


def docker(*args, timeout=DOCKER_TIMEOUT):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def candidates(networks, now, min_age=MIN_AGE_SECONDS):
    for network in networks:
        name = network.get("Name", "")
        if not CI_NETWORK.match(name) or network.get("Containers"):
            continue
        if (now - created_at(network["Created"])).total_seconds() < min_age:
            continue
        yield network["Id"], name


def reap(now=None, run=docker, clock=time.monotonic, budget=SWEEP_SECONDS):
    deadline = clock() + budget

    def remaining():
        # Every call gets only what is left, so the sweep never exceeds the budget.
        return max(0.1, min(DOCKER_TIMEOUT, deadline - clock()))

    now = now or datetime.now(timezone.utc)
    ids = run("network", "ls", "--quiet", "--no-trunc", timeout=remaining()).split()
    if not ids:
        return []
    removed = []
    for network_id, name in candidates(json.loads(run("network", "inspect", *ids, timeout=remaining())), now):
        if clock() >= deadline:
            print(f"CI network hygiene stopped at its {budget}s budget; the next job continues")
            break
        try:
            # Removal by immutable ID; Docker itself refuses a network in use.
            run("network", "rm", network_id, timeout=remaining())
        except subprocess.CalledProcessError:
            continue
        removed.append(name)
    return removed


def reap_best_effort(stage):
    """Never fail a job because of hygiene; report what happened instead."""
    # Explicit per-lane opt-in: only lanes owning a dedicated CI engine.
    if os.environ.get("AGENT_CI_NETWORK_HYGIENE") != "1":
        return
    if not (SOCKET.is_socket() or os.environ.get("DOCKER_HOST")):
        return
    try:
        removed = reap()
    except Exception as error:  # noqa: BLE001 - hygiene must not change job outcome
        print(f"CI network hygiene ({stage}) skipped: {type(error).__name__}: {error}")
        return
    if removed:
        print(f"CI network hygiene ({stage}) removed {len(removed)} empty network(s): {', '.join(removed)}")


if __name__ == "__main__":
    for name in reap():
        print(f"Removed {name}")
