"""Expose missing NAS storage and stale Restic snapshots as a failed systemd unit."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess


def command(*args):
    return subprocess.check_output(args, text=True, timeout=60).strip()


def main():
    for mount in ("/storage", "/storage/obsidian", "/storage/media", "/storage/docker-dataset"):
        source = command("findmnt", "--mountpoint", mount, "--noheadings", "--output", "SOURCE")
        if source != "storage" and not source.startswith("storage/"):
            raise RuntimeError(f"{mount} is not mounted from the storage ZFS pool")
    health = command("zpool", "list", "-H", "-o", "health", "storage")
    if health != "ONLINE":
        raise RuntimeError(f"ZFS pool storage is {health}")
    snapshots = json.loads(command("restic", "--no-cache", "--no-lock", "snapshots", "--json", "--latest", "1"))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)
    for path in ("/storage/obsidian", "/var/lib/agent-backup-staging"):
        recent = [s for s in snapshots if s.get("hostname") == "nas" and path in s["paths"]
                  and datetime.fromisoformat(s["time"].replace("Z", "+00:00")) > cutoff]
        if not recent:
            raise RuntimeError(f"No successful backup covering {path} in the last 36 hours")
    offsite = os.environ.get("OFFSITE_BACKUP_STATUS")
    if offsite:
        state = json.loads(Path(offsite).read_text())
        for action, hours in (("copy", 36), ("check", 8 * 24), ("restore_test", 35 * 24)):
            age = datetime.now(timezone.utc) - datetime.fromisoformat(state[action]["at"])
            if age < timedelta(minutes=-5) or age > timedelta(hours=hours):
                raise RuntimeError(f"Offsite {action} receipt is missing or stale")
        failure = state.get("failure", {})
        action = failure.get("action", "").replace("-", "_")
        if action in state and failure["at"] > state[action]["at"]:
            raise RuntimeError("Latest offsite operation failed")
    production = os.environ.get("DEALPIPE_RECOVERY_STATUS")
    if production:
        state = json.loads(Path(production).read_text())
        for action, hours in (("backup", 36), ("check", 8 * 24), ("restore_verify", 35 * 24), ("sanitize_refresh", 8 * 24)):
            age = datetime.now(timezone.utc) - datetime.fromisoformat(state[action]["at"])
            if age < timedelta(minutes=-5) or age > timedelta(hours=hours):
                raise RuntimeError(f"Production recovery {action} is missing or stale")
        if state["sanitize_refresh"].get("source") != "production-backup":
            raise RuntimeError("Only a synthetic recovery rehearsal has completed")
        if state["testing_smoke"]["sanitized_at"] != state["sanitize_refresh"]["at"]:
            raise RuntimeError("Latest dataset has no matching API smoke receipt")
        if state["testing_realtime"]["sanitized_at"] != state["sanitize_refresh"]["at"]:
            raise RuntimeError("Latest dataset has no matching Realtime receipt")
        failure = state.get("failure", {})
        action = failure.get("action", "").replace("-", "_")
        if action in state and failure["at"] > state[action]["at"]:
            raise RuntimeError("Latest production recovery action failed")
    print("NAS health OK: required mounts present, ZFS ONLINE, NAS and agent backups younger than 36h")


if __name__ == "__main__":
    main()
