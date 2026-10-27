#!/usr/bin/env python3
"""Install/check reviewed policy and merge owned settings without copying credentials."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import yaml

HERE = Path(__file__).resolve().parent
RUNTIME = "/opt/agent-policy/current"


def jsonc(text):
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/',
                  lambda m: m[1] or "", text, flags=re.S)
    text = re.sub(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])',
                  lambda m: m[1] or m[2], text, flags=re.S)
    return json.loads(text)


def bundle_files(source):
    files = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Policy must contain real files: {path.name}")
        if path.is_file():
            files[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files or "AGENTS.md" not in files:
        raise ValueError("Policy bundle is missing AGENTS.md")
    return files


def bundle_id(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def atomic_write(path, text, uid=None):
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".agent-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.chmod(name, 0o600)
        if os.geteuid() == 0 and uid is not None:
            os.chown(name, uid, uid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def refuse_symlink_path(state, path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Symlink in managed path: {part.name}")
        if part == state:
            break


def managed_opencode(current, template, enabled):
    result = dict(current)
    for key in ("$schema", "model", "provider"):
        result.setdefault(key, template[key])
    servers = {**template.get("mcp", {}), **result.get("mcp", {})}
    unknown = set(enabled) - set(servers)
    if unknown:
        raise ValueError("Unknown MCP server names: " + ", ".join(sorted(unknown)))
    result["mcp"] = {name: {**value, "enabled": name in enabled} for name, value in servers.items()}
    instructions = result.get("instructions", [])
    if not isinstance(instructions, list):
        raise ValueError("OpenCode instructions must be a list")
    result["instructions"] = list(dict.fromkeys([*instructions, f"{RUNTIME}/AGENTS.md"]))
    permission = dict(result.get("permission", {}))
    permission["external_directory"] = "ask"
    permission["doom_loop"] = "ask"
    result["permission"] = permission
    return result


def provision(state, source=HERE, apply=False, enabled=None):
    if enabled is None:
        enabled = json.loads((source / "access.json").read_text())["enabled_mcp"]
    policy = source / "policy"
    files = bundle_files(policy)
    revision = bundle_id(files)
    desired = []
    # Validate all current configs before making changes. Never print their contents.
    for worker in ("t3", "opencode"):
        home = state / worker
        directory = home / ".config/opencode"
        candidates = [p for p in (directory / "opencode.jsonc", directory / "opencode.json") if p.exists()]
        if len(candidates) > 1:
            raise ValueError(f"Two OpenCode configs in {worker}; resolve ambiguity first")
        target = candidates[0] if candidates else directory / "opencode.jsonc"
        refuse_symlink_path(state, target)
        old = jsonc(target.read_text()) if target.exists() else {}
        desired.append((target, managed_opencode(old, json.loads((source / "opencode.json").read_text()), enabled), 1100, "json"))
        # Claude Code shares the worker home, but retains its own MCP adapter.
        target = home / ".claude.json"
        refuse_symlink_path(state, target)
        claude = json.loads(target.read_text()) if target.exists() else {}
        servers = dict(claude.get("mcpServers", {}))
        servers.pop("shared-brain", None)
        if "shared-brain" in enabled:
            servers["shared-brain"] = {"type": "stdio", "command": "python3", "args": ["/usr/local/lib/agent-brain.py", "bridge"]}
        claude["mcpServers"] = servers
        desired.append((target, claude, 1100, "json"))
        target = home / ".claude/CLAUDE.md"
        refuse_symlink_path(state, target)
        previous = target.read_text() if target.exists() else ""
        start, end = "<!-- homelab-shared-policy:start -->", "<!-- homelab-shared-policy:end -->"
        if previous.count(start) != previous.count(end) or previous.count(start) > 1:
            raise ValueError("Malformed Claude policy block")
        previous = re.sub(re.escape(start) + r".*?" + re.escape(end), "", previous, flags=re.S).rstrip()
        desired.append((target, previous + "\n\n" + start + "\n@" + RUNTIME + "/AGENTS.md\n" + end + "\n", 1100, "text"))
    desired.extend([
        (state / "bin/workload.py", (source / "workload.py").read_text(), None, "text"),
        (state / "bin/agent-workload", '#!/bin/sh\nexec python3 /usr/local/lib/agent-workload.py "$@"\n', None, "text"),
        (state / "bin/brain.py", (source / "knowledge/brain.py").read_text(), None, "text"),
    ])
    links = []
    for worker in ("t3", "opencode"):
        # Codex/OpenCode read .agents/skills; Claude Code reads .claude/skills.
        for skill in sorted((policy / "skills").iterdir()):
            for directory in (".agents/skills", ".claude/skills"):
                path = state / worker / directory / skill.name
                refuse_symlink_path(state, path.parent)
                target = f"{RUNTIME}/skills/{skill.name}"
                if (path.exists() or path.is_symlink()) and (not path.is_symlink() or os.readlink(path) != target):
                    raise ValueError(f"Skill collision at {path}; preserve/rename the existing skill first")
                links.append((path, target))
    current = state / "policy/current"
    if current.exists() and not current.is_symlink():
        raise ValueError("Policy current must be a managed symlink, not a directory/file")
    release = state / "policy/releases" / revision
    refuse_symlink_path(state, release)
    refuse_symlink_path(state, state / "bin")
    drift = []
    if not current.is_symlink() or os.readlink(current) != f"releases/{revision}":
        drift.append("policy revision")
    elif not release.is_dir() or bundle_files_excluding_manifest(release) != files:
        drift.append("policy content")
    for path, data, uid, kind in desired:
        existing = read_config(path, kind) if path.exists() else None
        if existing != data:
            drift.append(str(path.relative_to(state)))
        if path.parent == state / "bin" and path.exists() and path.stat().st_mode & 0o222:
            drift.append(f"writable runtime helper: {path.name}")
    for path, _ in links:
        if not path.is_symlink():
            drift.append(str(path.relative_to(state)))
    if not apply:
        return drift
    if state == Path("/var/lib/agent-platform") and os.geteuid() != 0:
        raise ValueError("Apply the NAS configuration as root")
    if state == Path("/var/lib/agent-platform"):
        # Allow a narrow policy rollout without activating unrelated NixOS edits.
        directory = Path("/var/lib/agent-workloads")
        refuse_symlink_path(directory, directory / "heavy.lock")
        directory.mkdir(mode=0o755, exist_ok=True)
        if directory.stat().st_uid != 0:
            raise ValueError("Workload directory must be root-owned")
        directory.chmod(0o755)
        fd = os.open(directory / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o666)
        try:
            if os.fstat(fd).st_uid != 0:
                raise ValueError("Workload lock must be root-owned")
            os.fchmod(fd, 0o666)
        finally:
            os.close(fd)
    for worker, uid in (("t3", 1100), ("opencode", 1100)):
        home = state / worker
        home.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chown(home, uid, uid)
        home.chmod(0o700)
    for path in (state / "policy", state / "policy/releases", state / "bin"):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)
    if release.exists() and bundle_files_excluding_manifest(release) != files:
        raise ValueError("Existing immutable policy release is corrupt; investigate before applying")
    if not release.exists():
        release.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".policy-", dir=release.parent) as tmp:
            staged = Path(tmp) / "bundle"
            shutil.copytree(policy, staged)
            (staged / "manifest.json").write_text(json.dumps({"revision": revision, "files": files}, indent=2) + "\n")
            for path in staged.rglob("*"):
                path.chmod(0o555 if path.is_dir() else 0o444)
            staged.rename(release)
            release.chmod(0o555)
    current.parent.mkdir(parents=True, exist_ok=True)
    pending = current.parent / ".current-next"
    if pending.exists() or pending.is_symlink():
        pending.unlink()
    pending.symlink_to(f"releases/{revision}")
    pending.replace(current)
    for path, data, uid, kind in desired:
        existing = read_config(path, kind) if path.exists() else None
        if existing == data:
            if path.parent == state / "bin":
                path.chmod(0o555 if path.name == "agent-workload" else 0o444)
            continue
        if path.exists():
            # Private backup may contain user configuration/inline secrets. Never log it.
            atomic_write(path.with_name(path.name + ".before-agent-policy"), path.read_text(), uid)
        text = (json.dumps(data, indent=2) + "\n" if kind == "json" else
                yaml.safe_dump(data, sort_keys=False) if kind == "yaml" else data)
        atomic_write(path, text, uid)
        if path.parent == state / "bin":
            path.chmod(0o555 if path.name == "agent-workload" else 0o444)
        if os.geteuid() == 0 and uid is not None:
            for directory in (path.parent, *path.parent.parents):
                if directory == state / path.relative_to(state).parts[0]:
                    break
                os.chown(directory, uid, uid)
    for path, target in links:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_symlink():
            path.symlink_to(target)
        if os.geteuid() == 0:
            for directory in (path.parent, path.parent.parent):
                os.chown(directory, 1100, 1100)
    return drift


def read_config(path, kind):
    text = path.read_text()
    return jsonc(text) if kind == "json" else yaml.safe_load(text) if kind == "yaml" else text


def bundle_files_excluding_manifest(path):
    return {name: digest for name, digest in bundle_files(path).items() if name != "manifest.json"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/agent-platform"))
    parser.add_argument("--source", type=Path, default=HERE)
    parser.add_argument("--apply", action="store_true", help="Default is a read-only drift check")
    parser.add_argument("--enable-mcp", action="append", default=None, metavar="NAME", help="Override access.json's complete opt-in list, repeated per server")
    args = parser.parse_args()
    drift = provision(args.state, args.source, args.apply, args.enable_mcp)
    for item in drift:
        print(("Applied: " if args.apply else "DRIFT: ") + item)
    if not drift:
        print("Agent policy is in sync")
    return 0 if args.apply or not drift else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        # Parser exceptions can contain secret-bearing source lines.
        raise SystemExit("Configuration failed; check paths, permissions, config syntax, and skill collisions. No config values were logged.")
