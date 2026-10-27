## Shared NAS knowledge (2026-10-16)

The central vault is `/storage/obsidian`, with the organized entry point
`Brain/Start Here.md`. The knowledge service, proposal collector and timers are
installed separately from NixOS activation. T3/OpenCode and Claude Code
use the same read-only `shared-brain` service; desktop clients connect over SSH.
Agents receive a proposal directory and the search socket, never a vault mount.
Repository documentation remains in Git. See [knowledge/README.md](knowledge/README.md)
for source ownership, deployment, approval, credentials and recovery. The
`agent-knowledge` skill governs retrieval and merge wrap-up. The older policy
rollout notes below describe the pre-knowledge baseline.

# NAS development agents

## T3 coordinator

T3 owns issue delivery, worktree workers, independent review and PR handback.
See [COORDINATOR.md](COORDINATOR.md) for GitHub commands, conversation bindings,
recovery and operator verification. Hermes was retired on 2026-10-17; its private
state and backups are retained, but it is no longer provisioned or deployed.


## Desktop Surplus provider

`opencode.json` is the NAS's OpenCode **1.x** configuration. The separate
`opencode-desktop.json` uses the **2.x** `providers`/`package`/`settings` schema.
Do not replace the NAS configuration with the desktop template.

The Surplus-only desktop configuration is installed at:

- WSL: `/home/jlorezz/.config/opencode/opencode.jsonc`.
- Native Windows: `C:\Users\flori\.config\opencode\opencode.jsonc`.

Both default to `surplus-intelligence/deepseek-v4.1-flash`, preserving the server's
endpoint, model metadata and reasoning variants. No credentials, MCP servers or
NAS-specific mount paths are copied. The Windows T3 app currently uses the
`Ubuntu-26.04` WSL backend, so it uses the WSL provider login. A separate native
Windows OpenCode installation uses the Windows configuration and its own login.

From a WSL terminal, authenticate privately and confirm discovery:

```sh
opencode auth login surplus-intelligence
opencode models
```

Enter the Surplus API key in the login prompt, never in a chat or config commit.
Refresh OpenCode provider status/models in T3 and select
`surplus-intelligence/deepseek-v4.1-flash`. If editing the template later, merge
only its provider and desired default model into each desktop config, preserve
unrelated settings, and run `opencode reload`. OpenCode 2.0.30 successfully lists
the configured model; an authenticated desktop model request remains pending
until the key is supplied.

## Repository-owned policy rollout

These notes describe the earlier policy rollout baseline. The shared knowledge
service and agent policy are now activated through the narrow rollout described
above, using verified Tailscale SSH access. This does not activate unrelated CI
hooks, image builds or NixOS changes from the baseline below. Always preserve
SSH host verification.

The desired state lives here:

| Source | Owns |
| --- | --- |
| `policy/AGENTS.md` | Shared task, credential, verification and delivery policy |
| `policy/skills/` | Locally authored skills; no upstream executable code |
| `policy/provenance.json` | Audited upstream revisions and adopted ideas |
| `access.json` | Complete opt-in list of enabled OpenCode MCP servers |
| `configure.py` | Idempotent home migration and content-addressed policy releases |
| `workload.py` | Shared heavy-workload lock, deadlines and private receipts |
| `review.py`, `policy/review-opencode.json` | Explicitly invoked isolated advisory review |
| `doctor.py` | Source/host drift checks and optional read-only GitHub enforcement audit |
| `../../packages/dealpipe-runtimes.nix` | Bun 1.3.11 and Node 22.23.3 with upstream checksums |
| `../github-runner/job-hook.py` | CI job-level workload lease |
| `../../.github/workflows/agent-platform.yml` | Pinned-action source tests and runtime builds |

T3 is the workbench and coordinator. Explicit delivery requests include an
isolated implementation worker and independent advisory review. No council
score replaces repository checks; merge and deployment remain separate. The
optional credential-free reviewer is available through `review.py run`.

### Provision and activate

Run these from the reviewed administration checkout on the NAS. Use `path:` while
validating a working tree so Nix includes new files that are not committed yet.
Never copy credentials or agent state into that source tree.

1. Validate the source before changing anything:

   ```sh
   nix shell nixpkgs#python3Packages.pyyaml -c python3 stacks/agents/doctor.py --source-only
   nix shell nixpkgs#python3Packages.pyyaml -c python3 -m unittest discover -s stacks/agents/tests -v
   nix build path:.#agent-runtimes
   ```

2. Stop the T3/optional OpenCode stack through Portainer during a maintenance
   window. This prevents agents modifying home configuration while it is migrated.
   Do not interrupt a CI job. Set `access.json` to the reviewed MCP opt-in list;
   the default is empty. Read-only account scopes and separate non-production
   accounts must be provisioned at the services themselves.

3. Apply the narrow home/policy migration:

   ```sh
   sudo nix run path:.#configure-agents -- --apply
   sudo nix run path:.#agent-doctor
   ```

   Existing model/provider selections, authentication files, Discord allowlists
   and the user-owned part of `SOUL.md` are preserved. Owned approval/toolset,
   MCP-enable and instruction settings are reconciled. JSONC comments are
   normalized; values remain intact. Changed configs get a private
   `.before-agent-policy` backup beside the original. No credential values are
   logged. Skills with conflicting names and symlinked config paths are refused.

   Policy releases live in `/var/lib/agent-platform/policy/releases/<sha256>`.
   `current` is updated atomically. Containers mount the parent directory
   read-only and follow `current`; existing sessions still need a reload/restart.
   Root owns policy artifacts. The helper also prepares the shared lock without
   needing a whole NixOS activation. The matching tmpfiles definitions remain in
   the Nix modules for boot/recovery.

4. Build and redeploy the agent image with the existing Portainer helper:

   ```sh
   nix build path:.#agent-image --out-link /var/tmp/homelab-agent-image
   sudo nix run path:.#deploy-agents -- \
     --token-file /var/lib/agent-platform/secrets/portainer-token \
     --connect-address 100.125.180.36 --endpoint 2 \
     --image-archive /var/tmp/homelab-agent-image
   ```

   Preserve the optional OpenCode service by also passing the documented
   `--with-opencode` and password-file arguments if it is enabled. Retired
   Hermes is never included in new stack deployments. Once private preview slots
   are provisioned, also pass `--with-previews` on every replacement so their
   ports, DNS and external backend networks remain configured.

5. In an idle CI maintenance window, rebuild/redeploy the runner from this source:

   ```sh
   sudo nix shell nixpkgs#python3Packages.pyyaml -c python3 stacks/github-runner/deploy.py \
     --token-file /var/lib/agent-platform/secrets/portainer-token
   ```

   The build context now includes the shared workload helper and job hooks.
   Deployment refuses a visible running `Runner.Worker`; pause incoming work
   during maintenance to avoid a new job racing the idle check. No workflow or
   registration token changes are required. The runner remains persistent and
   restricted to the trusted private repository; these hooks do not make it an
   ephemeral security boundary.

6. Check policy in each service and runtime versions from the T3 console:

   ```sh
   test -r /opt/agent-policy/current/AGENTS.md
   bun --version
   node --version
   agent-workload run --timeout 30 -- node -e 'console.log("WORKLOAD_OK")'
   ```

   Run one harmless model/tool turn in T3. For the next CI
   job, check “Set up runner” for `Shared heavy-workload slot acquired`. Confirm
   an agent command queues while that CI lease is held. Source checks do not
   prove container/model/browser connectivity.

### Heavy commands and evidence

All cooperating NAS agent installs, builds, checks and previews use:

```sh
agent-workload run --timeout 900 --receipt /workspace/check-receipt.json -- bun run lint
```

The deadline is explicit per task. The default queue budget is a pilot admission
ceiling of 1800 seconds; `--queue-timeout` overrides it. Timeout returns 124;
queue exhaustion returns 75. Receipts contain timestamps, executable name, status
and unknown spend, never argv/environment/output. Give each receipt a new path.
The wrapper terminates its process group before releasing the slot, including
background children. Keep previews in the foreground for their owned lifetime.
Do not nest this wrapper inside a CI job that already holds the lease.

CI hooks hold shared leases on the same host-file lock for each job, allowing
the three bounded CI lanes to overlap. Heavy agent commands acquire exclusive
leases and wait until every CI lane is finished. Each CI lease releases on completion
or when that exact `Runner.Worker` exits; PID start time prevents PID reuse from
holding a stale lease. The lock coordinates participants, not arbitrary commands,
detached sessions or all Nix builds. It is not a filesystem sandbox, CPU limit or
provider spend cap. Existing cgroups still provide resource ceilings. Configure
provider spending caps separately; no dollar-cap API is assumed.

The policy's `handoff.schema.json` specifies repository, base SHA, branch, owned
scope, acceptance criteria, status, evidence and next action. Never transfer
credentials/session databases as a handoff. Validate generated JSON with a
Draft 2020-12 JSON Schema validator. Git bundles require a restore check.

### Optional advisory review

An explicitly requested review can run from a committed snapshot, without the
coding agent's home, GitHub token, MCP accounts, Docker socket or production data:

```sh
nix run path:.#review-agents -- prepare \
  --repo /var/lib/agent-platform/projects/t3/dealpipe \
  --base origin/development --head HEAD \
  --output /var/lib/agent-platform/reviews/example-snapshot

sudo nix run path:.#review-agents -- run \
  --snapshot /var/lib/agent-platform/reviews/example-snapshot \
  --output /var/lib/agent-platform/reviews/example-result \
  --image localhost/homelab-agents:EXACT_BUILT_TAG \
  --key-file /var/lib/agent-platform/secrets/review-inference-key \
  --token-file /var/lib/agent-platform/secrets/portainer-token \
  --timeout 900
```

Provision `review-inference-key` privately with mode 0600 and a separate,
spend-limited Surplus inference key. No key is copied from a coding profile.
The snapshot exports committed head context plus the exact base/head diff;
uncommitted files and `.git` are excluded. Tracked environment files in either
revision and unsafe symlinks refuse export. Harness configuration is excluded
from the runnable source to prevent project config/plugin loading; its diff is
still visible as untrusted data. Gitleaks scans the entire snapshot/diff before
any paid request; scanning does not prove the absence of every kind of sensitive
business data, so review the export before sending it to a provider.

The ephemeral container has a read-only root/source/policy, temporary home,
default-denied tools with only source reading/search, no published ports, and
pilot limits of one CPU / 4 GiB / 12 model steps. The command deadline triggers
container termination; API cleanup may take additional time. These are admission
budgets, not measured Dealpipe requirements or a monetary spending guarantee.
Private Docker log output and an advisory receipt are retained; completion only
means the process exited successfully, not that its claims were validated.
Inspect findings individually. The helper always attempts container removal.

### Dealpipe CI and browser boundary

Dealpipe owns `validate.yml` and branch protection. Its solo-development workflow
has merged; older notes saying it is pending are stale. Run:

```sh
nix run path:.#agent-doctor -- --source-only --github
```

This is read-only. At implementation time neither `development` nor `main` had a
required `validation` branch-protection check. Prove a full green run on the
current branch, review rulesets and required check identity, then explicitly
enable the desired merge gate in Dealpipe. Homelab provisioning does not mutate
application branch protection, merge PRs or promote production.

The agent image now supplies the exact Bun/Node runtimes but does **not** install
Dealpipe dependencies, browser binaries or a new database. Use T3's collaborative
preview for interactive inspection. Production-build browser/integration checks
remain on the dedicated CI runner with disposable services and project-pinned
Playwright. Do not connect agent containers to the CI Docker socket or the
laptop's shared database to make a check appear runnable.

### Rollback

Stop agent services in Portainer, restore changed home configs from their private
`.before-agent-policy` backups, point `policy/current` at the previous recorded
release, and redeploy the prior images/Compose definition. Keep the new policy
mounts until restoring configs that reference `/opt/agent-policy/current` is
complete. Do not remove/recreate the lock inode while jobs hold it. To remove CI
hooks, redeploy the previous runner image in an idle maintenance window. Do not
delete runner registration, application services or database volumes.

## Agent deployment and onboarding

Portainer environment 2 owns the `homelab-agents` stack (ID 1). Hermes has been
removed from the stack. The NAS hosts new, manually started T3 work; the old
desktop delivery queues remain disabled. See COORDINATOR.md for historical
bindings and the prerequisites for any future reactivation.

In the T3 Portainer console as user **1100:1100**, authenticate with
`opencode auth login`, discover models, then run `t3 pair` and pair the NAS at
<https://nas.tailb80645.ts.net:3773>. Authenticate GitHub independently with
`gh auth login` when that environment needs repository access. Existing private
provider and Portainer credentials stay in their owning homes.

## Always-on NAS workspace

Use the NAS environment at <https://nas.tailb80645.ts.net:3773> for new threads
and connect to it from the Mac. The Windows/WSL environment remains available
for optional heavy work while the PC is on. Existing desktop thread history,
worktrees and disabled schedules remain on that environment; connecting the Mac
does not migrate them. Do not copy live T3 SQLite files between machines.

The image pins T3 and bootstrap provider binaries in `packages/t3-nightly.nix`,
`packages/codex.nix`, `packages/opencode.nix` and the locked Nixpkgs Claude package.
Codex needs the complete platform bundle, including `codex-code-mode-host` and
its resources: a CLI-only install can pass login/version checks but fail every
tool call. The image also supplies the ELF loader, shared libraries and fonts
for T3's downloaded Chrome. Verify an actual `preview_open` after upgrades.
The bootstrap Claude package patches its Linux binary and disables CLI
self-updates. A generic `curl | bash` installer on the NixOS host can fail at its
ELF loader and does not install a provider inside T3.
Authenticate in the worker with
`docker exec -it -u 1100:1100 homelab-agents-t3-1 claude auth login`, then enable
Claude Code in the NAS provider settings and verify a real turn. Browser login
may require opening the printed URL on the Mac and pasting the returned code
directly into that terminal; never put the code in chat or source control.

For T3's provider Update buttons, use writable installations in the persistent
home. Nix-store binaries cannot update themselves. In the worker as UID 1100:

```sh
agent-workload run --queue-timeout 1800 --timeout 600 -- \
  npm install --global --prefix /home/agent/.local \
  opencode-ai@latest @openai/codex@latest

# Preserve a native Claude installation that its updater already created.
if [ ! -e /home/agent/.local/bin/claude ]; then
  agent-workload run --queue-timeout 1800 --timeout 600 -- \
    npm install --global --prefix /home/agent/.local @anthropic-ai/claude-code@latest
fi
```

Set each NAS provider's **Binary path** to `/home/agent/.local/bin/claude`,
`/home/agent/.local/bin/opencode` or `/home/agent/.local/bin/codex`. T3 detects npm
ownership and updates the same prefix; native Claude uses its own updater in
the writable home. Compose also puts this directory on PATH
for terminals. These user-managed versions survive image replacement; image pins
are bootstrap/rollback versions, not the live provider update channel. Check
versions and run a real tool-using turn after an update. Do not update a provider
while it has active turns.
### Updating T3 itself

T3 updates the same way, without an image rebuild. In a T3 terminal or
`docker exec -it -u 1100:1100 homelab-agents-t3-1 sh`:

```sh
update-t3            # latest nightly; or: update-t3 latest | preview | <exact version>
update-t3 --restart  # same, then restart T3 (drops open sessions briefly)
```

`update-t3` installs the official `t3` npm launcher into `/home/agent/.local`.
The launcher downloads the release archive into `~/.t3/runtime/versions/` and
verifies it against that release's `SHA256SUMS`. The container starts through
`t3-start`, which runs the user-managed T3 when its `--version` check passes and
otherwise falls back to the image pin in `packages/t3-nightly.nix`. `--restart`
stops PID 1; Docker's `unless-stopped` policy starts the container again. Do not
restart while turns are active. Roll back with `update-t3 <older version>`, or
remove `/home/agent/.local/bin/t3` to return to the pin. Check `docker logs` for
the `t3-start: using ...` line and verify a real turn and `preview_open` after
an update. Move the image pin forward occasionally so the fallback stays recent.

The existing backup excludes `node_modules`, including these npm installations;
reinstall the CLI packages after a restore before using their saved binary paths.
Provider authentication and thread data remain in the backed-up home.

`browser-seccomp.json` retains the default-deny Moby profile from commit
[`4b8214fc4971e06172938a1f1554ccff6647ee80`](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json)
(Apache-2.0), with `clone`, `setns` and `unshare` allowed as described in
[Playwright's Docker guidance](https://playwright.dev/docs/docker#crawling-and-scraping).
It also permits `chroot`: with `cap_drop: ALL`, Moby's capability-conditional
rule otherwise blocks Chrome inside its new user namespace. The syscall allowance
does not grant host capabilities. This enables Chrome's sandbox with the non-root user,
dropped capabilities and `no-new-privileges`. `deploy-agents` stages a
content-addressed profile in `/var/lib/portainer/agent-profiles` on the NAS and
sets `AGENT_BROWSER_SECCOMP` to its `/data/agent-profiles` path inside Portainer.
Compose reads a file, not inline JSON. For another Portainer data bind, set
`--portainer-data-directory` to its host path (mounted at `/data` in Portainer).
The profile is included in the existing Portainer data backup and can be
regenerated by `deploy-agents`. Keep browser sandboxing enabled.
The persistent `/home/agent` holds provider authentication and T3 state. GitHub
uses HTTPS with `gh auth setup-git`; no host SSH key is mounted into the worker.
The workspaces are `/workspace/homelab` and `/workspace/dealpipe`.
`configure-agents` seeds a missing Codex configuration with the opted-in Brain
bridge and links its instructions to the reviewed policy. It preserves existing
Codex settings and authentication, and rejects instruction-file collisions.
Existing Codex configurations need their Brain integration checked separately.

New NAS threads default to separate worktrees. Private Dealpipe previews use the
operator-provisioned development backends described in
[previews/README.md](previews/README.md). The NAS launcher claims one of two
persistent backend slots per checkout, installs locked dependencies and initializes
only that slot from canonical migrations/seeds. It never retargets the sanitized
testing stack or mounts a Docker socket. The normal desktop worktree hook still
requires Docker and must not be imported as the NAS setup command.

Generate a fresh one-time pairing link **when the Mac is ready**, in the T3
container as UID 1100, with `t3 pair` (see `t3 pair --help` for the public address
option). Paste it into **Settings → Connections → Add environment** on the Mac.
Use the NAS HTTPS address, not container localhost. Pairing credentials stay out
of Git, Brain, issue comments and logs. Each client retains its own connection
and load-balancing preferences; choose NAS explicitly for normal work and Windows
explicitly for heavy work. See [T3 remote access](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md).

All agent subprocesses remain under the container and aggregate cgroup ceilings.
The parent `agent.slice` has CPU/I/O weight 20, below the default-weight parent
slices containing CI. `agent-workload` still gates heavy commands against CI;
weights do not preempt a running build or reserve memory. Test actual CI latency
before increasing concurrency. A narrow rollout may use persistent
`systemctl set-property agent.slice CPUWeight=20 IOWeight=20` with the matching
Nix definition, avoiding activation of unrelated host changes.

Before a container upgrade, save SQLite online backups and the Portainer stack
in a root-only maintenance directory. Preserve existing network attachments,
including `dealpipe-testing_testing` if present: an attachment made outside
Compose is lost during replacement. Deploy through Portainer, then verify the
image, provider login, a real model turn, Brain access and restart recovery.
A healthy HTTP page alone proves neither model access nor scheduler execution.

The old queue registry still identifies desktop-bound coordinators. A NAS
workspace is not permission to re-enable disabled delivery/monitoring tasks.
Create and validate any new NAS coordinator/schedules separately against the
current application guide; never reuse old absolute paths or start historical
Ready cards by replacing the queue baseline.

## OpenCode account configuration

`opencode.json` contains the reviewed, secret-free OpenCode 1.x translation of the
user's Surplus Intelligence / DeepSeek v4.1 Flash configuration and nine MCP servers.
The live T3 copy is `/var/lib/agent-platform/t3/.config/opencode/opencode.jsonc`
on the NAS (`/home/agent/.config/opencode/opencode.jsonc` inside the container).
The workspace helper seeds missing configs but never overwrites existing ones.

In the T3 Portainer console as user `1100:1100`, run `opencode auth login`, choose
the custom provider / Other, and use the exact provider ID `surplus-intelligence`.
Alternatively, set `SURPLUS_API_KEY` in T3 Settings → Providers → OpenCode →
Environment. Refresh provider status and choose
`surplus-intelligence/deepseek-v4.1-flash`. Config parsing/model discovery do not
verify the provider's advertised context/output limits or an actual model request.

The pasted GitHub credential was not installed; it must be revoked and replaced.
Set the replacement as `GITHUB_MCP_TOKEN` in T3's OpenCode provider environment.
The config uses `Bearer {env:GITHUB_MCP_TOKEN}`, with no literal token in Git or
the Nix store. An `export` in an unrelated console does not update an already
running T3/OpenCode process; use the provider environment settings.

For OAuth-capable MCP servers, authenticate independently with
`opencode mcp auth SERVER` and inspect `opencode mcp list`. Enabling a server in
the config is not account authorization. Headless OAuth may require callback
forwarding from the laptop; do not publish a callback port publicly as a workaround.
All MCP URLs/project scope match the user's supplied config. These MCP tools do
not create or migrate a self-hosted Supabase stack.

The homelab flake owns the **host**: SSD-backed state directories, a dedicated
UID, aggregate CPU/RAM limits, tailnet HTTPS routes, and encrypted local backups.
**Portainer owns images and container/stack lifecycle.** No Nix OCI service starts
these agents, and none of the existing application containers are migrated by
this change.

The initial workspace is this homelab repository. Supabase is deliberately not
deployed: homelab has no Supabase application/configuration to migrate. A future
application gets its own stack and database volumes; don't share the NAS's
existing application PostgreSQL instance with a Supabase stack.

## Components and budgets

- T3 with OpenCode installed: `compose.yaml`, 3 CPU threads / 10 GiB maximum.
- Optional independent OpenCode web workspace: add `compose.opencode.yaml`,
  2 CPU threads / 6 GiB maximum, authenticated with a unique server password.
- The **aggregate** `agent-workloads.slice` ceiling is 4 CPU threads and 16 GiB
  RAM, with a 12 GiB pressure threshold and no swap. Individual maxima cannot
  all be reached at once. Start with T3 and one heavy build/test at a time.

Each service has its own checkout and home. T3's bundled OpenCode provider uses
T3's checkout; the optional OpenCode web service has another checkout. Neither
gets `/etc/nixos`, NAS data, host SSH keys, the Docker socket, or sudo access.
Container limits cover subprocesses; they do not limit hosted-model token spend.
Model-provider credentials are configured at runtime and are never Nix values.

The development image includes Git, GitHub CLI, Node, Python, and common shell
tools. It does **not** expose the host Nix daemon or provide a writable Nix store.
Agents can edit Nix files; full Nix builds/activation run from the trusted NAS
administration checkout using the commands below. Browser/CAD toolchains are
not preinstalled. Add project-specific tooling to the image when needed.

## Prepare the host and image

Review and activate the host configuration using the normal homelab process:

```sh
sudo nixos-rebuild switch --flake /etc/nixos/homelab#nas
sudo nix run /etc/nixos/homelab#prepare-agent-workspaces
nix build /etc/nixos/homelab#agent-image --out-link /var/tmp/homelab-agent-image
```

The workspace helper only clones missing checkouts, creates separate agent
branches, creates a private OpenCode server password file. It never resets
existing work. It accepts a Git bundle path as an optional argument when cloning
the latest committed local source without GitHub credentials. Uncommitted laptop
edits are not transferred. Configure a Git identity and repository authentication
within each workspace before expecting pushes or PRs to work.

The image uses the official T3 nightly CLI archive pinned by version and SHA-256
in `packages/t3-nightly.nix`, the official OpenCode release pinned by version and
SHA-256 in `packages/opencode.nix`, and a derivation-based image tag. These pins are
the bootstrap and fallback versions. Day-to-day T3 and provider updates use the
user-managed npm installs described above; change a pin's version and checksum
only to move the fallback forward, then deploy the image through Portainer.
After changing a package pin or the image definition, build and import a new image
and update the stack in Portainer. A NixOS rebuild alone does not update containers.

## Deploy through Portainer

Create an API token in **My account → Access tokens**, save it in a private file
on the machine running deployment (`chmod 600`), and keep it out of this repo.
Run the helper on the NAS so the image archive stays on that machine:

```sh
nix run /etc/nixos/homelab#deploy-agents -- \
  --token-file /path/to/private-portainer-token \
  --connect-address "$(tailscale ip -4)" \
  --image-archive "$(readlink -f /var/tmp/homelab-agent-image)"
```

This streams the image through Portainer's environment API and creates or updates
the `homelab-agents` stack. It does not invoke Docker directly. It selects the
single environment automatically; use `--endpoint ID` when there are several.
The direct connection address accommodates this NAS's disabled MagicDNS; TLS
still verifies the hostname in `--url`. Resolving the image symlink before sudo
also avoids Linux's protection against following user-owned symlinks in `/var/tmp`.
The stack is managed in Portainer's editor, with this repository as its reviewed
source. Changes are not automatically fetched from GitHub; rerun the helper or
apply the reviewed Compose changes in Portainer. Preserve the same stack name
on updates, and include optional-service flags again when retaining those services.

To add independent OpenCode, include:

```sh
sudo nix run /etc/nixos/homelab#deploy-agents -- \
  --token-file /path/to/private-portainer-token \
  --connect-address "$(tailscale ip -4)" \
  --image-archive "$(readlink -f /var/tmp/homelab-agent-image)" \
  --with-opencode \
  --opencode-password-file /var/lib/agent-platform/secrets/opencode-password
```

Alternatively, import the archive with Portainer's image import UI and deploy the
Compose files as a stack. Set `AGENT_IMAGE` to the exact imported tag; optional
OpenCode requires `OPENCODE_SERVER_PASSWORD`. Set the password as a Portainer
environment value, not a committed file. Portainer administrators can read stack
environment values.

## Connect T3 and the hosted provider

T3 is served at <https://nas.tailb80645.ts.net:3773>. Generate a pairing link in
the T3 container console with `t3 pair`, then pair the NAS environment in the
laptop T3 application. Inspect `t3 pair --help` for address options; a link using
container-local loopback must be changed to the reachable HTTPS address while
preserving its pairing token. Do not publish pairing tokens in docs or logs.

In the T3 container console, run `opencode auth login` to authenticate the hosted
provider, then `opencode models` to discover the exact available model ID. Choose
the OpenCode provider and that model in T3. The model name mentioned in chat is
not assumed to be an API identifier. Provider login/session compatibility with
the laptop's newer OpenCode installation must be checked during onboarding.

For optional OpenCode web, use <https://nas.tailb80645.ts.net:4096>, username
`opencode`, and the generated server password. Run provider login separately in
that container. Home directories persist login and session data across upgrades.

## Retired Hermes

`retire-hermes.py` removes only the Hermes service and its labelled container
through Portainer. It defaults to a read-only plan; `--apply` saves private Compose
and environment snapshots, updates the stack without global pruning, and removes
only Hermes containers without deleting volumes. Homes, secrets, workspaces and
backups are retained under `/var/lib/agent-platform` for recovery. The retired
9119 tailnet route, Homepage entry and Beszel probe are removed from desired Nix
configuration; activate those changes separately from unrelated NAS work.

## Verify before assigning work

In Portainer, confirm container health/logs and each container's cgroup parent.
On the NAS:

```sh
systemctl show agent-workloads.slice -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax
systemd-cgls /agent.slice/agent-workloads.slice
sudo tailscale serve status
systemctl status restic-backups-agents.timer
```

Check that unauthenticated clients cannot use agent APIs and that tailnet URLs
work from the laptop. Run one harmless hosted-model request and a small test
command in each enabled agent; verify the resulting file is in its own checkout.
Observe resource usage during a representative test/build before adding parallel
workers. Infrastructure deployment still runs from the administrator's checkout.

## Persistence, backups, and recovery

All state is under `/var/lib/agent-platform` on the NAS SSD. A separate nightly
Restic job stages homes, credentials, sessions, Git workspaces, and retained retired-agent state
into `/var/lib/agent-backup-staging`; dependency/build caches are excluded. SQLite
databases are replaced with online-consistent backups, including committed WAL
data. This provides per-database consistency, not an atomic snapshot of every
agent's files. Existing NAS backups separately cover Portainer's own database.

The Restic repository is encrypted but lives on this NAS; it is not an offsite
backup. Test recovery before relying on it for irreplaceable work. Push reviewed
commits regularly.

For recovery, stop the stack in Portainer, restore the agent staging snapshot to
a temporary directory, copy its contents back to `/var/lib/agent-platform`,
preserve UID 1100 for T3/OpenCode and ownership of archived state, then redeploy
the pinned images and verify provider login and workspaces. Do not run two copies
of an agent against the same SQLite state directory.

## Sources

- [T3 remote access](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
- [OpenCode server/authentication](https://opencode.ai/docs/server/)


### CI concurrency

The Dealpipe CI Portainer stack contains three bounded runner instances: `runner`
(database regression), `browser` (production builds and browser tests), and `static`
(routing, lint, typecheck and unit checks). They share the rootless engine but have
separate registration state, homes and host-matched workspace paths. Only regression
and browser receive the CI Docker socket. Their test services use separate project
names and port ranges. The shared lease permits these CI lanes to overlap while
ordinary `agent-workload run` commands retain exclusive admission. Do not add more
runner replicas without reviewing aggregate capacity and fixed-port ownership.
