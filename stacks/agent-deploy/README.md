# Restricted agent deploy path

`qnoxslab.agentDeploy` (module `machines/nas/modules/agent-deploy.nix`) lets one SSH key run **named, allowlisted host actions** on the NAS instead of the operator pasting commands into a root shell. It is **disabled by default** and enabled on the NAS in `machines/nas/configuration.nix` (key comment `agent-deploy@t3`, source `172.18.0.0/16`); a NixOS switch activates it. Enabling it is a reviewed config change plus a NixOS switch by the operator. The one-time setup is in the Dealpipe pipeline handover `NAS-DEPLOY-USER-SETUP.md`.

## Shape

- **User:** system user `agent-deploy`, no password, home `/var/empty`.
- **SSH:** its one key is `restrict,command="<agent-deploy-ssh>",from="<T3 source CIDR>"`. sshd adds a `Match User agent-deploy` block (ForceCommand, no TTY, no forwarding, publickey only).
- **Dispatcher:** `agent-deploy-ssh` parses `SSH_ORIGINAL_COMMAND` against a fixed character set and the action table in `agent_deploy.py`. It never runs a shell or accepts free-form arguments.
- **Root:** the only sudo rule allows exactly the Nix store path of `agent-deploy-root`. That re-validates the action, enforces the arm, audits and runs it. Scripts come from this generation's own reviewed source in the Nix store.
- **Never:** an arbitrary shell, Docker socket exposure, secret values in arguments/logs/output, GitHub label changes, or production changes. A new generation whose production deploy unit differs from the live one is refused.

## Actions

| Action | Args | Arm | What it does |
|---|---|---|---|
| `help` | – | no | List actions (no root). |
| `status` | – | no | Arm, pending switch, current system. |
| `inventory` | – | no | Read-only inventory (`inventory.sh`, from the Dealpipe NAS-HOST-BATCH §0). |
| `nix-build` | 40-hex SHA on `main` | no | Mirror the public repo, require the SHA to be an ancestor of `main`, build the NAS toplevel to `/var/lib/agent-deploy/builds/<sha>`, refuse if the production unit would change. Takes the heavy lock. |
| `nixos-switch` | SHA | **yes** | Needs that build, idle runners (no `Runner.Worker`) and healthy `sshd`/`tailscaled`. Starts a dead-man revert timer (`confirmMinutes`, default 15), then `switch-to-configuration test`; reverts at once if the health check fails. Not the boot default yet. |
| `nixos-confirm` | same SHA | no (only for the armed pending switch) | Re-checks health, sets the system profile and `switch-to-configuration boot`, stops the revert timer. |
| `nixos-rollback` | – | no (only a pending switch) | Reverts to the pre-switch generation now. |
| `image-build` | `deploy` or `preview` | **yes** | Runs the reviewed `build-image.py` (host engine). Heavy lock. |
| `image-check` | `sha256:<64 hex>` | no | `docker image inspect` ID/created only. |
| `static2-check` | – | no | static-2 container image and limits. |
| `static2-policy` | – | **yes** | NAS-HOST-BATCH B2: adds the 12 GiB/2 CPU static-2 profile to the **expired** CI policy, with a backup. |
| `static2-dryrun` | container ID (64 hex) | no | `repair-static2.py` dry run as `dealpipe-ci`. |
| `static2-apply` | container ID | **yes** | `repair-static2.py --apply` (B3). Runner label steps B4/B5 stay on GitHub, outside this path. |
| `ci2-canary-dryrun` | image ID | no | `ci2-canary.py` gates. |
| `ci2-canary-apply` | image ID | **yes** | `ci2-canary.py --apply`. The registration token is read once from the root-only file `/var/lib/agent-deploy/registration-token` the operator wrote, piped on stdin, and deleted even on failure. |

## Operator commands (root, on the NAS)

```sh
agent-deploy-admin arm <action> [minutes]   # single use, bound to one action, max 30 min
agent-deploy-admin disarm
agent-deploy-admin status
agent-deploy-admin audit [lines]            # JSON lines: who/what/args/arm nonce/status/output sha256
journalctl -t agent-deploy                  # same records via logger
```

The audit log is `/var/log/agent-deploy/audit.log` (opened `O_APPEND`). tmpfiles sets `+a` (append-only) where the filesystem supports it; whether the NAS root filesystem does is UNVERIFIED. Records hold action names, validated arguments, timestamps and hashes, never secret values.

## Revoke

Set `qnoxslab.agentDeploy.enable = false` (or remove the key), then rebuild and switch. As an immediate stopgap: `usermod -L -s /run/current-system/sw/bin/nologin agent-deploy` and `pkill -u agent-deploy` (only that user's sessions). Then remove the private key from the T3 volume.

## Tests

`python -m unittest discover -s stacks/agent-deploy/tests -v` (root-free: allowlist, arm, audit, token handling, argv smuggling). `python3 stacks/agent-deploy/tests/check-agent-deploy-nix.py` (needs nix): disabled by default; key options; one exact sudo rule; sshd Match block; no production unit change; bad key/CIDR assertions.
