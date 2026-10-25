#!/usr/bin/env python3
"""Restricted NAS deploy dispatcher for one agent SSH key.

Modes:
  ssh    Forced SSH command for the `agent-deploy` user. Parses
         SSH_ORIGINAL_COMMAND against a fixed allowlist and re-executes the
         root helper through `sudo -n`. Never runs a shell.
  root   Runs as root via the single sudo rule. Re-validates everything,
         enforces the operator arm for mutating actions, audits and runs.
  admin  Local root-only operator commands: arm, disarm, status, audit.

There are no free-form arguments: every action declares exact argument
patterns. Nothing reads, prints or forwards secret values; the one action that
needs a registration token reads it from a root-only file the operator wrote.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

SHA40 = r"[0-9a-f]{40}"
IMAGE = r"sha256:[0-9a-f]{64}"
CONTAINER = r"[0-9a-f]{64}"
MAX_ARM_MINUTES = 30
# Characters SSH_ORIGINAL_COMMAND may contain at all; anything else is refused
# before tokenizing so quoting tricks never reach the allowlist.
COMMAND_CHARS = re.compile(r"[a-z0-9:. -]{1,200}")


class Refused(Exception):
    """A request outside the allowlist, arm or precondition contract."""


# name: (argument patterns, needs operator arm, timeout seconds, heavy lock)
ACTIONS = {
    "help": ((), False, 10, False),
    "status": ((), False, 30, False),
    "inventory": ((), False, 300, False),
    "nix-build": ((SHA40,), False, 3600, True),
    "nixos-switch": ((SHA40,), True, 1800, True),
    "nixos-confirm": ((SHA40,), False, 900, False),
    "nixos-rollback": ((), False, 900, False),
    "image-build": ((r"deploy|preview",), True, 1800, True),
    "image-check": ((IMAGE,), False, 120, False),
    "static2-check": ((), False, 120, False),
    "static2-policy": ((), True, 120, False),
    "static2-dryrun": ((CONTAINER,), False, 600, False),
    "static2-apply": ((CONTAINER,), True, 1800, False),
    "ci2-canary-dryrun": ((IMAGE,), False, 600, False),
    "ci2-canary-apply": ((IMAGE,), True, 900, False),
}
# Root is needed for everything except listing actions.
LOCAL_ONLY = {"help"}


def parse_request(raw):
    """Turn an SSH_ORIGINAL_COMMAND into (action, args) or refuse."""
    if raw is None or not COMMAND_CHARS.fullmatch(raw):
        raise Refused("Command missing or contains characters outside the allowlist.")
    words = shlex.split(raw, posix=True)
    if not words:
        raise Refused("Empty command.")
    return validate(words[0], words[1:])


def validate(action, args):
    if action not in ACTIONS:
        raise Refused(f"Unknown action {action!r}; run 'help'.")
    patterns = ACTIONS[action][0]
    if len(args) != len(patterns):
        raise Refused(f"{action} takes exactly {len(patterns)} argument(s).")
    for value, pattern in zip(args, patterns):
        if not re.fullmatch(pattern, value):
            raise Refused(f"{action}: argument {value!r} does not match {pattern}.")
    return action, list(args)


class Dispatcher:
    def __init__(self, config):
        self.config = config
        self.state = Path(config["stateDir"])
        self.log = Path(config["auditLog"])
        self.source = Path(config["source"])

    # ---------- arm ----------
    @property
    def arm_file(self):
        return self.state / "arm.json"

    def arm(self, action, minutes, now=None):
        now = time.time() if now is None else now
        if action not in ACTIONS:
            raise Refused(f"Unknown action {action!r}.")
        if not ACTIONS[action][1]:
            raise Refused(f"{action} does not need an arm.")
        if not 1 <= minutes <= MAX_ARM_MINUTES:
            raise Refused(f"Arm for 1-{MAX_ARM_MINUTES} minutes.")
        record = {"action": action, "until": int(now + minutes * 60), "nonce": secrets.token_hex(8)}
        self._write_private(self.arm_file, json.dumps(record))
        return record

    def disarm(self):
        self.arm_file.unlink(missing_ok=True)

    def consume_arm(self, action, now=None):
        """Return the nonce of a valid single-use arm for exactly this action."""
        now = time.time() if now is None else now
        try:
            record = json.loads(self.arm_file.read_text())
        except FileNotFoundError:
            raise Refused(f"{action} needs an operator arm: agent-deploy-admin arm {action}") from None
        except ValueError:
            self.disarm()
            raise Refused("Arm file unreadable; disarmed.") from None
        self.disarm()  # single use, also when it turns out invalid
        if record.get("action") != action:
            raise Refused(f"Armed for {record.get('action')!r}, not {action!r}; disarmed.")
        if not isinstance(record.get("until"), int) or record["until"] < now:
            raise Refused("Arm expired; disarmed.")
        return record["nonce"]

    # ---------- audit ----------
    def audit(self, **record):
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
        line = json.dumps(record, sort_keys=True) + "\n"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
        logger = self.config.get("logger")
        if logger:
            subprocess.run([logger, "-t", "agent-deploy", line.strip()], check=False)

    # ---------- helpers ----------
    def _write_private(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)

    def _run(self, argv, timeout, stdin=None, env=None, digest=None):
        """Stream output to the caller while hashing it for the audit log."""
        full_env = {"PATH": self.config["path"], "LANG": "C.UTF-8", **(env or {})}
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=full_env)
        if stdin:
            process.stdin.write(stdin)
            process.stdin.close()
        deadline = time.monotonic() + timeout
        for chunk in iter(lambda: process.stdout.readline(), b""):
            if digest is not None:
                digest.update(chunk)
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
            if time.monotonic() > deadline:
                process.terminate()
                raise Refused(f"Timed out after {timeout}s.")
        code = process.wait(timeout=max(1, deadline - time.monotonic()))
        if code != 0:
            raise Refused(f"{Path(argv[0]).name} exited {code}.")

    def _script(self, *parts):
        path = self.source.joinpath(*parts)
        if not path.is_file():
            raise Refused(f"Reviewed script missing from source: {'/'.join(parts)}")
        return str(path)

    def _ci_user(self, argv):
        return ["runuser", "-u", "dealpipe-ci", "--", "env", "XDG_RUNTIME_DIR=/run/user/1102", *argv]

    # ---------- entry points ----------
    def run_root(self, action, args, client="unknown"):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            action, args = validate(action, args)
            nonce = self.consume_arm(action) if ACTIONS[action][1] else None
        except Refused as error:
            self.audit(event="refused", action=str(action)[:40], client=client, detail=str(error))
            raise
        timeout, heavy = ACTIONS[action][2], ACTIONS[action][3]
        digest = hashlib.sha256()
        started = time.monotonic()
        self.audit(event="start", action=action, args=args, client=client, arm=nonce)
        status, detail = "ok", ""
        try:
            with self._lock(self.state / "dispatch.lock", 10):
                if heavy:
                    with self._lock(Path(self.config["heavyLock"]), self.config.get("queueSeconds", 900)):
                        getattr(self, "do_" + action.replace("-", "_"))(args, timeout, digest)
                else:
                    getattr(self, "do_" + action.replace("-", "_"))(args, timeout, digest)
        except Refused as error:
            status, detail = "refused", str(error)
            raise
        except Exception as error:  # noqa: BLE001 - audit every failure
            status, detail = "error", type(error).__name__
            raise
        finally:
            self.audit(event="end", action=action, args=args, client=client, arm=nonce, status=status,
                       detail=detail, seconds=round(time.monotonic() - started, 1),
                       outputSha256=digest.hexdigest())

    @staticmethod
    def _lock(path, seconds):
        class Held:
            def __enter__(self_inner):
                self_inner.fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
                deadline = time.monotonic() + seconds
                while True:
                    try:
                        fcntl.flock(self_inner.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return self_inner
                    except BlockingIOError:
                        if time.monotonic() > deadline:
                            os.close(self_inner.fd)
                            raise Refused(f"Lock {path} busy for {seconds}s.") from None
                        time.sleep(2)

            def __exit__(self_inner, *exc):
                os.close(self_inner.fd)

        return Held()

    # ---------- actions ----------
    def do_status(self, args, timeout, digest):
        arm = self.arm_file.read_text() if self.arm_file.exists() else "{}"
        pending = self.pending_file.read_text() if self.pending_file.exists() else "{}"
        report = {"arm": json.loads(arm), "pending": json.loads(pending),
                  "currentSystem": os.path.realpath("/run/current-system")}
        text = json.dumps(report, indent=2) + "\n"
        digest.update(text.encode())
        sys.stdout.write(text)

    def do_inventory(self, args, timeout, digest):
        self._run(["bash", self._script("stacks", "agent-deploy", "inventory.sh")], timeout, digest=digest)

    # nix-build / switch / confirm / rollback
    @property
    def pending_file(self):
        return self.state / "pending-switch.json"

    def _mirror(self, sha):
        mirror = self.state / "homelab.git"
        if not mirror.exists():
            self._run(["git", "clone", "--bare", "--quiet", self.config["repository"], str(mirror)], 600)
        self._run(["git", "-C", str(mirror), "fetch", "--quiet", "--prune", "origin",
                   "+refs/heads/main:refs/heads/main"], 600)
        check = subprocess.run(["git", "-C", str(mirror), "merge-base", "--is-ancestor", sha, "main"],
                               env={"PATH": self.config["path"]}, check=False)
        if check.returncode != 0:
            raise Refused(f"{sha} is not on the reviewed main branch.")
        return mirror

    def _build_link(self, sha):
        return self.state / "builds" / sha

    def do_nix_build(self, args, timeout, digest):
        (sha,) = args
        mirror = self._mirror(sha)
        link = self._build_link(sha)
        link.parent.mkdir(parents=True, exist_ok=True)
        self._run(["nix", "build", "--no-write-lock-file", "--out-link", str(link),
                   f"git+file://{mirror}?ref=main&rev={sha}#{self.config['toplevelAttr']}"],
                  timeout, digest=digest)
        self._production_unit_unchanged(link)
        sys.stdout.write(f"Built {sha} -> {os.path.realpath(link)}; production unit unchanged.\n")

    def _production_unit_unchanged(self, link):
        unit = self.config["productionUnit"]
        live = Path(unit)
        new = Path(os.path.realpath(link)) / "etc" / "systemd" / "system" / live.name
        if not live.exists():
            return  # production controller not deployed through this host
        if not new.exists() or new.read_bytes() != live.read_bytes():
            raise Refused("Evaluated production deploy unit differs from live; production is Florian-only.")

    def _health(self):
        failures = []
        for unit in self.config["healthUnits"]:
            if subprocess.run(["systemctl", "is-active", "--quiet", unit],
                              env={"PATH": self.config["path"]}, check=False).returncode != 0:
                failures.append(unit)
        return failures

    def _workers_running(self):
        for comm in Path("/proc").glob("[0-9]*/comm"):
            try:
                if comm.read_text().strip() == "Runner.Worker":
                    return True
            except OSError:
                continue
        return False

    def do_nixos_switch(self, args, timeout, digest):
        (sha,) = args
        if self.pending_file.exists():
            raise Refused("A switch is already pending; confirm or roll it back first.")
        link = self._build_link(sha)
        if not link.exists():
            raise Refused(f"Run nix-build {sha} first.")
        self._production_unit_unchanged(link)
        if self._workers_running():
            raise Refused("A Runner.Worker is active; switch only when idle.")
        old = os.path.realpath("/run/current-system")
        new = os.path.realpath(link)
        before = self._health()
        if before:
            raise Refused(f"Unhealthy before switch: {before}.")
        # Dead-man revert first, so losing this session can't strand the host.
        minutes = self.config["confirmMinutes"]
        self._run(["systemd-run", "--unit", "agent-deploy-revert", "--on-active", f"{minutes}min",
                   "--timer-property=AccuracySec=1s", f"{old}/bin/switch-to-configuration", "test"], 60)
        self._write_private(self.pending_file, json.dumps(
            {"sha": sha, "old": old, "new": new, "until": int(time.time() + minutes * 60)}))
        try:
            self._run([f"{new}/bin/switch-to-configuration", "test"], timeout, digest=digest)
        except Refused:
            self.do_nixos_rollback([], timeout, digest)
            raise
        after = self._health()
        if after:
            self.do_nixos_rollback([], timeout, digest)
            raise Refused(f"Health check failed after test activation: {after}; reverted.")
        sys.stdout.write(f"Activated {sha} with `test` (not boot default). Run nixos-confirm {sha} "
                         f"within {minutes} min or it reverts to {old}.\n")

    def do_nixos_confirm(self, args, timeout, digest):
        (sha,) = args
        try:
            pending = json.loads(self.pending_file.read_text())
        except FileNotFoundError:
            raise Refused("Nothing pending to confirm.") from None
        if pending["sha"] != sha or pending["until"] < time.time():
            raise Refused("Pending switch is for another commit or expired.")
        if os.path.realpath("/run/current-system") != pending["new"]:
            raise Refused("Running system is not the pending generation.")
        failures = self._health()
        if failures:
            raise Refused(f"Unhealthy, not confirming: {failures}.")
        self._run(["nix-env", "-p", "/nix/var/nix/profiles/system", "--set", pending["new"]], 300, digest=digest)
        self._run([f"{pending['new']}/bin/switch-to-configuration", "boot"], timeout, digest=digest)
        subprocess.run(["systemctl", "stop", "agent-deploy-revert.timer"],
                       env={"PATH": self.config["path"]}, check=False)
        self.pending_file.unlink()
        sys.stdout.write(f"Confirmed {sha} as the boot default; revert timer stopped.\n")

    def do_nixos_rollback(self, args, timeout, digest):
        try:
            pending = json.loads(self.pending_file.read_text())
        except FileNotFoundError:
            raise Refused("Nothing pending; older rollbacks are operator-only.") from None
        self._run([f"{pending['old']}/bin/switch-to-configuration", "test"], timeout, digest=digest)
        subprocess.run(["systemctl", "stop", "agent-deploy-revert.timer"],
                       env={"PATH": self.config["path"]}, check=False)
        self.pending_file.unlink()
        sys.stdout.write(f"Reverted to {pending['old']}.\n")

    # images
    def do_image_build(self, args, timeout, digest):
        (kind,) = args
        stack = "github-deploy-runner" if kind == "deploy" else "github-preview-runner"
        self._run(["python3", self._script("stacks", stack, "build-image.py")], timeout, digest=digest)

    def do_image_check(self, args, timeout, digest):
        (image,) = args
        self._run(["docker", "--host", "unix:///var/run/docker.sock", "image", "inspect",
                   "--format", "{{.Id}} {{.Created}}", image], timeout, digest=digest)

    # static-2 (NAS-HOST-BATCH group B; GitHub label steps B4/B5 stay off-host)
    def do_static2_check(self, args, timeout, digest):
        self._run(self._ci_user(["docker", "--host", "unix:///run/user/1102/docker.sock", "inspect",
                                 "--format", "{{.Name}} {{.Image}} memory={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}}",
                                 "dealpipe-ci-static-2-1"]), timeout, digest=digest)

    def do_static2_policy(self, args, timeout, digest):
        path = Path(self.config["ciPolicy"])
        data = json.loads(path.read_text())
        if data.get("expiresAt") is None or data["expiresAt"] > time.time():
            raise Refused("CI policy must stay expired for this edit (NAS-HOST-BATCH B2).")
        backup = path.with_name(f"policy.json.before-static2-{int(time.time())}")
        backup.write_bytes(path.read_bytes())
        os.chmod(backup, 0o600)
        data["profiles"]["static-2"] = {"memoryMiB": 12288, "cpuPercent": 200}
        if (data.get("maxConcurrentMiB") or 0) < 12288:
            raise Refused("maxConcurrentMiB below 12288; not editing.")
        stat = path.stat()
        tmp = path.with_suffix(".agent-deploy.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.chown(tmp, stat.st_uid, stat.st_gid)
        os.chmod(tmp, stat.st_mode & 0o777)
        os.replace(tmp, path)
        text = f"static-2 profile set; backup {backup.name}\n"
        digest.update(text.encode())
        sys.stdout.write(text)

    def _static2(self, container, apply, timeout, digest):
        argv = ["python3", self._script("stacks", "github-runner", "repair-static2.py"),
                "--expected-container-id", container]
        self._run(self._ci_user(argv + (["--apply"] if apply else [])), timeout, digest=digest)

    def do_static2_dryrun(self, args, timeout, digest):
        self._static2(args[0], False, timeout, digest)

    def do_static2_apply(self, args, timeout, digest):
        self._static2(args[0], True, timeout, digest)

    # CI2 canary registration (token comes only from a root-only operator file)
    def _canary(self, image, apply, timeout, digest):
        argv = ["python3", self._script("stacks", "github-runner", "ci2-canary.py"), "--image", image]
        if not apply:
            self._run(["runuser", "-u", "dealpipe-ci", "--", *argv], timeout, digest=digest)
            return
        token_file = self.state / "registration-token"
        try:
            token = token_file.read_bytes()
        except FileNotFoundError:
            raise Refused(f"Operator must write a short-lived token to {token_file} (0600) first.") from None
        finally:
            token_file.unlink(missing_ok=True)  # single use even on failure
        try:
            self._run(["runuser", "-u", "dealpipe-ci", "--", *argv, "--apply"], timeout, stdin=token, digest=digest)
        finally:
            del token

    def do_ci2_canary_dryrun(self, args, timeout, digest):
        self._canary(args[0], False, timeout, digest)

    def do_ci2_canary_apply(self, args, timeout, digest):
        self._canary(args[0], True, timeout, digest)


def help_text():
    lines = ["Allowed actions (arm = needs `agent-deploy-admin arm <action>` by the operator):"]
    for name, (patterns, armed, _timeout, _heavy) in ACTIONS.items():
        lines.append(f"  {name} {' '.join(patterns)}{'  [arm]' if armed else ''}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--root-helper")
    parser.add_argument("mode", choices=["ssh", "root", "admin"])
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    config = json.loads(Path(options.config).read_text())
    try:
        if options.mode == "ssh":
            action, args = parse_request(os.environ.get("SSH_ORIGINAL_COMMAND"))
            if action in LOCAL_ONLY:
                sys.stdout.write(help_text())
                return 0
            client = (os.environ.get("SSH_CONNECTION") or "unknown").split(" ")[0]
            if not re.fullmatch(r"[0-9a-f.:]{1,45}", client):
                client = "unknown"
            if not options.root_helper:
                raise Refused("ssh mode needs --root-helper from its wrapper.")
            os.execv(config["sudo"], [config["sudo"], "-n", options.root_helper, client, action, *args])
        if os.geteuid() != 0:
            raise Refused("root and admin modes require root.")
        dispatcher = Dispatcher(config)
        if options.mode == "root":
            if os.environ.get("SUDO_USER") != config["user"]:
                raise Refused("Root mode is only reachable through the agent-deploy sudo rule.")
            if len(options.rest) < 2 or not re.fullmatch(r"[0-9a-f.:]{1,45}|unknown", options.rest[0]):
                raise Refused("Malformed root invocation.")
            dispatcher.run_root(options.rest[1], options.rest[2:], client=options.rest[0])
            return 0
        return admin(dispatcher, options.rest)
    except Refused as error:
        sys.stderr.write(f"agent-deploy: {error}\n")
        return 2


def admin(dispatcher, rest):
    if rest[:1] == ["arm"] and len(rest) in (2, 3):
        minutes = int(rest[2]) if len(rest) == 3 and rest[2].isdigit() else MAX_ARM_MINUTES
        record = dispatcher.arm(rest[1], minutes)
        dispatcher.audit(event="arm", action=rest[1], arm=record["nonce"], until=record["until"])
        print(f"Armed {rest[1]} until {time.strftime('%H:%M:%S %Z', time.localtime(record['until']))} (single use).")
        return 0
    if rest == ["disarm"]:
        dispatcher.disarm()
        dispatcher.audit(event="disarm")
        print("Disarmed.")
        return 0
    if rest == ["status"]:
        dispatcher.do_status([], 30, hashlib.sha256())
        return 0
    if rest[:1] == ["audit"] and len(rest) <= 2:
        count = int(rest[1]) if len(rest) == 2 and rest[1].isdigit() else 30
        lines = dispatcher.log.read_text().splitlines() if dispatcher.log.exists() else []
        print("\n".join(lines[-count:]))
        return 0
    raise Refused("Usage: agent-deploy-admin arm <action> [minutes<=30] | disarm | status | audit [lines]")


if __name__ == "__main__":
    sys.exit(main())
