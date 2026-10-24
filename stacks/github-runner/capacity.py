"""Render measured Nix capacity into rootless Compose; never starts services."""
import copy


def render(compose, capacity):
    if not capacity.get("enable"):
        raise ValueError("Enable and measure qnoxslab.githubRunnerCapacity first")
    if capacity["ci"]["count"] != 1 or capacity["browser"]["count"] != 1 or not 1 <= capacity["static"]["count"] <= 4:
        raise ValueError("Only socket-free static replicas are safe")
    required = (capacity["ci"]["memoryMiB"] + capacity["browser"]["memoryMiB"]
                + capacity["static"]["count"] * capacity["static"]["memoryMiB"]
                + capacity["engineReserveMiB"])
    if required > capacity["ciPool"]["memoryMiB"]:
        raise ValueError("CI pool is too small for lane ceilings and test services")
    services = compose["services"]
    for service, pool in (("runner", "ci"), ("browser", "browser"), ("static", "static")):
        limits = capacity[pool]
        if limits["count"] != 1 and pool != "static":
            raise ValueError("Fixed-port lanes cannot be replicated")
        lane = services[service]
        lane["cpus"] = limits["cpuPercent"] / 100
        lane["mem_limit"] = lane["memswap_limit"] = f'{limits["memoryMiB"]}m'
        # Stack jobs must be exclusive even if their image had a shared hook default.
        lane["environment"]["AGENT_CI_EXCLUSIVE"] = "0" if pool == "static" else "1"
        if pool == "static":
            lane["volumes"] = [v.replace("/heavy.lock:", "/static.lock:") for v in lane["volumes"]]
    for number in range(2, capacity["static"]["count"] + 1):
        lane = copy.deepcopy(services["static"])
        lane["volumes"] = [v.replace("/static/", f"/static-{number}/") for v in lane["volumes"]]
        lane["environment"]["RUNNER_NAME"] = f"dealpipe-nas-static-{number}"
        lane["environment"]["RUNNER_WORK_DIRECTORY"] = f"/var/lib/dealpipe-ci/static-{number}/work"
        services[f"static-{number}"] = lane
    return compose
