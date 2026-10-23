#!/usr/bin/env python3
"""Check source contracts, installed configuration, or live GitHub merge enforcement."""

import argparse
import ast
import json
from pathlib import Path
import stat
import subprocess

import yaml

from configure import HERE, bundle_files, provision


def source_checks(source=HERE):
    bundle_files(source / "policy")
    for path in source.glob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
    for path in (source / "knowledge").glob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
    for path in (source / "previews").glob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
    preview = yaml.safe_load((source / "compose.previews.yaml").read_text())["services"]["t3"]
    assert all(port.startswith("127.0.0.1:") for port in preview["ports"])
    assert preview["volumes"] == ["/var/lib/agent-platform/bin/preview:/opt/nas-preview:ro"]
    knowledge = json.loads((source / "knowledge/sources.json").read_text())["sources"]
    assert len({s["id"] for s in knowledge}) == len(knowledge), "Duplicate knowledge source"
    assert all(s["type"] in {"files", "repo"} and s["include"] for s in knowledge)
    assert any(s["status"] == "proposed" for s in knowledge), "Inbox must remain proposed"
    assert "RuntimeDirectoryPreserve=yes" in (source / "knowledge/units/agent-knowledge.service").read_text(), "Socket mount directory must survive service restarts"
    template = json.loads((source / "opencode.json").read_text())
    assert all(not server["enabled"] for server in template["mcp"].values()), "Remote MCP must be opt-in"
    enabled = json.loads((source / "access.json").read_text())["enabled_mcp"]
    assert isinstance(enabled, list) and all(isinstance(name, str) for name in enabled), "MCP opt-ins must be names"
    assert len(enabled) == len(set(enabled)) and set(enabled) <= set(template["mcp"]), "Unknown/duplicate MCP opt-in"
    review = json.loads((source / "policy/review-opencode.json").read_text())
    assert "mcp" not in review, "Reviewer must not inherit MCP accounts"
    assert review["permission"]["*"] == "deny", "Reviewer must default-deny tools"
    assert review["agent"]["homelab-review"]["permission"]["*"] == "deny"
    for path in (source / "policy/skills").iterdir():
        text = (path / "SKILL.md").read_text()
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        assert frontmatter["name"] == path.name and frontmatter["description"], "Invalid skill metadata"
        link = source.parents[1] / ".agents/skills" / path.name
        assert link.is_symlink() and link.resolve() == path.resolve(), f"Broken local skill link: {path.name}"
    json.loads((source / "policy/handoff.schema.json").read_text())
    for filename in ("compose.yaml", "compose.opencode.yaml"):
        data = yaml.safe_load((source / filename).read_text())
        for service in data["services"].values():
            assert "/var/lib/agent-platform/policy:/opt/agent-policy:ro" in service["volumes"], "Missing read-only policy mount"
            assert "/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock" in service["volumes"], "Missing shared lock"
            assert not any("docker.sock" in value for value in service["volumes"]), "Agent must not receive a Docker socket"
            assert "/run/agent-knowledge:/run/agent-knowledge:ro" in service["volumes"], "Missing read-only knowledge socket mount"
            assert not any("/storage/obsidian" in value for value in service["volumes"]), "Agents must not receive the vault"
    runners = yaml.safe_load((source.parent / "github-runner/compose.yaml").read_text())
    for service in runners["services"].values():
        assert "/var/lib/agent-workloads/heavy.lock:/run/agent-workload.lock" in service["volumes"], "CI lane is missing shared admission"
    return ["source config parses", "remote integrations are opt-in", "review tools default-deny",
            "local skill links resolve", "agent policy/lock mounts configured"]


def github_checks(repository):
    missing = []
    for branch in ("development", "main"):
        proc = subprocess.run(["gh", "api", f"repos/{repository}/branches/{branch}/protection"],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode:
            missing.append(f"{branch}: branch protection could not be read")
            continue
        protection = json.loads(proc.stdout)
        checks = protection.get("required_status_checks") or {}
        contexts = set(checks.get("contexts", [])) | {item["context"] for item in checks.get("checks", [])}
        if "validation" not in contexts:
            missing.append(f"{branch}: validation is not a required branch-protection check (inspect rulesets separately)")
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/agent-platform"))
    parser.add_argument("--enable-mcp", action="append", default=None)
    parser.add_argument("--github", action="store_true", help="Read-only; uses gh authentication; never modifies protection")
    args = parser.parse_args()
    issues = []
    for message in source_checks():
        print("PASS: " + message)
    if not args.source_only:
        issues.extend(provision(args.state, enabled=args.enable_mcp))
        release = args.state / "policy/current"
        if release.is_dir():
            for path in (release, *release.rglob("*")):
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode != (0o555 if path.is_dir() else 0o444):
                    issues.append("installed policy permissions differ from the read-only release contract")
                    break
        lock = Path("/var/lib/agent-workloads/heavy.lock")
        if not lock.is_file() or lock.is_symlink():
            issues.append("shared workload lock is missing or unsafe")
        elif lock.stat().st_uid != 0 or stat.S_IMODE(lock.stat().st_mode) != 0o666:
            issues.append("shared workload lock ownership/mode differs from root:0666")
        for name in ("agent-workload", "workload.py"):
            if not (args.state / "bin" / name).is_file():
                issues.append(f"missing runtime helper: {name}")
    if args.github:
        issues.extend(github_checks("dealpipe/dealpipe"))
    for issue in issues:
        print("NOT READY: " + issue)
    scope = "source" if args.source_only else "source and installed host files; container reload/model/browser connectivity are separate checks"
    print("Scope: " + scope + ("; live GitHub branch protection" if args.github else ""))
    return 1 if issues else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, KeyError, yaml.YAMLError, subprocess.SubprocessError):
        raise SystemExit("Doctor failed; check source/config syntax, paths, permissions, and account access. No config values were logged.")
