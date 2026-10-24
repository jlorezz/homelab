# Measured parallel runner capacity

For the actual 12 CPU / 47458 MiB NAS, use the disabled-by-default
[CI2 next-phase runbook](ci2-operator.md). The older illustrative topology below
does not fit this NAS and is not its activation procedure.

Definitions only: nothing here activates the NAS. The owning configuration is
`machines/nas/configuration.nix`. The existing rootless engine and Portainer
helper remain the CI deployment path. Never duplicate a socket-bearing Compose
service: its jobs use fixed host ports inside the one rootless network namespace.

`qnoxslab.githubRunnerCapacity` owns counts and memory/CPU limits. All defaults
are UNVERIFIED capacity estimates; `enable = false` and null measurement inputs
prevent expansion. Enable only after step-0 measurements in the operator batch.
The Nix-generated `/etc/dealpipe-runner-capacity.json` is consumed by `deploy.py`;
it renders replicas and limits before any Portainer API call. `--prepare-only`
also requires this measured configuration. Scaling down requires explicitly
stopping idle removed replicas: Portainer deliberately uses `Prune: false`.

| Pool/role | Count default / permitted | Memory default | CPU ceiling default |
|---|---|---|---|
| CI aggregate, including engine/test services | 1 / 1 | 32 GiB | 8 CPUs |
| CI / browser | 1 each / 1 each | 8 GiB each | 4 CPUs each |
| static | 1 / 1–4 | 4 GiB each | 2 CPUs each |
| preview aggregate | 1 / 1 | 10 GiB | 4 CPUs |
| preview build / publish | 0 each / 0–1 each | 8 / 2 GiB | 4 / 1 CPUs |
| staging | 1 when environment enabled / 1 | 8 GiB | 2 CPUs |
| production | 1 when environment enabled / 1 | 12 GiB | 4 CPUs (frozen) |

CI aggregate includes static replicas, engine and all sibling Supabase containers;
container limits alone do not bound Docker-created sibling services. The rootless
`user-1102.slice` caps them together. Require CI aggregate >= sum of CI/browser/
static container limits + `engineReserveMiB` (default 4 GiB, UNVERIFIED for actual
stacks). Container memory+swap equals memory; all aggregate slices disable swap.
CPU ceilings per container may sum above their aggregate CPU ceiling; the aggregate
ceiling wins. These are limits, not throughput reservations or measured job peaks.

Let H be the minimum `MemAvailable` at representative NAS load. Do not add
existing pool usage back: MemAvailable already includes reclaimable cache, and
adding memory.current can double-count it. Budgeting all pools again is
conservative; drain jobs first to reduce this double reservation. Let T be MemTotal and
R >= max(8 GiB, ceil(T/4)). Require:

    CI_pool + enabled_preview_pool + enabled_staging_pool
      + enabled_production_pool <= H - R

Also require the sum of enabled aggregate CPU ceilings <= measured logical CPUs
minus at least two CPUs for the NAS. `measure-capacity.py` emits inputs, not an
approval: record cgroup paths, timestamp and load conditions, repeat during busy
NAS periods. Memory accounting/reclaim can vary; observe PSI/OOM after activation.
Unmeasured background agents must fit the reserve or be separately budgeted;
pause heavy agent commands during rollout. A 32/10/8/12 GiB configuration needs
62 GiB plus R. Shrinking defaults to fit without evidence can just move queue
failures into OOM failures. Stop if the formula fails.

Admission, held for Runner.Worker lifetime before checkout:

* CI/browser: **exclusive heavy.lock**. All Docker/Supabase jobs serialize to
  prevent fixed-port collisions (including the known mail port 8788 collision).
* Static replicas: shared `static.lock`; socket-free light jobs run together and
  concurrently with a stack job. Heavy agent admission no longer excludes these
  bounded light jobs, so their memory must remain in the aggregate budget.
* Preview build/publish: exclusive `preview.lock`; one job at a time across these
  two roles, separately budgeted from CI. Their runners can register concurrently.
  The aggregate covers both container ceilings, including the idle listener.
* Staging: exclusive `deploy.lock`, its own slice; no socket or shared work volume.
* Production: unchanged original controller/image/unit, exclusive `heavy.lock`,
  original deploy slice. It still excludes CI stacks and heavy agent commands.

Two static registrations (operator sets `static.count = 2`) provide at least two
concurrent CI-class light jobs, plus one stack job and one preview job where the
budget permits. This change does **not** promise two simultaneous stack jobs or
two `dealpipe-nas-ci` jobs. That needs Dealpipe port/engine isolation in separate
work; workflow concurrency policies and production are outside this change.

Labels match Dealpipe PR #827 head `12ef96b92691a6f25addcf1ca1277a57f3dda71d`:
`dealpipe-nas-static`, `dealpipe-nas-ci`, `dealpipe-nas-browser`,
`dealpipe-nas-preview`, `dealpipe-preview-publish`, `dealpipe-deploy-staging`,
`dealpipe-deploy-production`. Static replica names append `-2` etc; disposable
names are `dealpipe-deploy-<role>-<random>`. Labels route jobs; they are not access
controls. GitHub environment policies stay with the application owner.

The preview image is built separately from the pinned deployment Dockerfile with
a preview-only entrypoint. Staging reuses its existing deployment image. Workers
receive only a one-hour registration token over stdin; no controller credential,
host Docker socket, host networking, shared home/work or service volumes. The
existing public-egress-only deployment bridge/firewall remains in force.
Production source files, image ID, controller command and slice stay unchanged.
Live production drift must be reconciled before applying anything.

Offline checks:

    python3 -m unittest discover -s stacks/github-runner/tests -v
    bash stacks/github-runner/production-equivalence.sh BASE_SHA /tmp/runner-unit-check
    nix eval --offline --raw path:.#nixosConfigurations.nas.config.system.build.toplevel.drvPath
    nix flake check --offline --no-build

The equivalence script enables production with identical synthetic inputs in
both revisions; fixture numbers are not host measurements. Compare the actual
reconciled host unit too before activation. Missing Nix/store inputs blocks this
check; text inspection is not evaluated equivalence. No image/container/service
is started by these offline checks. Operator commands live in the handoff batch.

## First phase: one static replica inside the existing ceiling

`static-replica.py` is a separate, incremental operator helper. It adds **only**
`dealpipe-ci-static-2-1` (`dealpipe-nas-static-2`, label `dealpipe-nas-static`),
using the existing UID 1102 rootless engine, original entrypoint and reviewed
source image ID (default
`sha256:121de326c9541820ea00f82a924286b5d6f9134d48c66cef1f50064e722ee079`).
It never pulls an image or changes static-1, other workers, services or NixOS.
The new container has 8 GiB memory, equal memory+swap, 2 CPUs, 2048 PIDs,
init and restart `unless-stopped`. Host networking stays inside the existing
rootless namespace. Browser already has a separate engine; this helper always
uses `/run/user/1102/docker.sock` and never mounts any engine socket.

Run as CI UID 1102 on the NAS. Resolve the existing Python executable explicitly
(`python3` is absent from the NAS PATH); no install is needed. Default is read-only:

```bash
runner_python=/nix/store/lb41b0anx1f98y9y5s9mdv97gjgsq740-python3-3.14.7/bin/python3
"$runner_python" static-replica.py --expected-image sha256:121de326c9541820ea00f82a924286b5d6f9134d48c66cef1f50064e722ee079
```

After separately checking production/staging unit hashes, the operator may run
with `--apply` and supply **one fresh short-lived registration token via stdin**
from a private input/pipe. Never put tokens in command arguments, shell history,
logs or evidence. The helper captures configuration output without displaying it;
the temporary configuration container disables Docker logging. It sends the token
only over stdin, exports it to `config.sh` and exits after configuration. Persistent
GitHub runner credentials (not the input token) live in the private runner state.
The permanent original entrypoint then finds `/runner/.runner`; no registration
file, controller credential, host home or old runner state is mounted.

Only `/var/lib/dealpipe-ci/static-2/{runner,home,work}` (UID 1102, mode 0700)
and the **preexisting** `heavy.lock` inode are bound. Both static workers retain
shared global admission (`AGENT_CI_EXCLUSIVE=0`); this phase does not introduce
`static.lock`. Production and heavy agent commands retain exclusive `heavy.lock`.
Only source static-1 must be idle immediately before configuration; other lanes
can continue. A source job starting after that check is safe because static-1
is never changed.

The incremental gate requires the existing CI slice to remain exactly 32 GiB /
8 CPUs with aggregate memory.swap.max = 0, physical RAM >= 32 GiB + max(8 GiB, ceil(MemTotal/4)), and MemAvailable >=
8 GiB + that reserve. Within the existing aggregate it also requires 8 GiB free
against the larger of anon+kernel usage and memory.current-file. File cache is
not added to MemAvailable. CI OOM/kill counters must start at zero and remain
unchanged through configuration/start. These checks permit two light CI jobs
within the same aggregate ceiling; they cannot guarantee workload peaks or
throughput. They **do not bypass the full 62 GiB pool gate** above. Preview and
staging expansion remain blocked; existing production/staging are untouched.

Existing state is accepted only with this helper's private ownership marker;
existing containers require its managed label, exact name, mounts, limits and
configuration. Unknown or partially registered state is never cleared. On
configuration/start failure cleanup requires the unique creation-attempt label.
A running permanent replica is always retained on failure: an idle inspection
cannot prevent a job arriving before a stop. No stop or force-removal of the
permanent replica is performed. `--rollback` refuses every running replica and
removes only an already-stopped, exactly owned container, retaining private state.
If a job may be active, let it finish and use a separately verified operator drain
before stopping it; this helper does not implement a GitHub admission drain.
The operator must remove only the exact new GitHub registration if needed,
compare unit hashes before/after, and verify listener/GitHub health and OOM/PSI.
A stopped replica requires private operator inspection rather than an
automatic restart. Do not delete retained state until registration cleanup is
confirmed. No live-host behavior has been validated by the mocked offline tests.

## Missing Bun runtime (additive repair)

The CI image pins X64 Bun **1.3.11**, matching Dealpipe development
[`packageManager` at `9b31e97`](https://github.com/dealpipe/dealpipe/blob/9b31e97d47859fad51877c24000b343e9a0d088f/package.json). The [official release](https://github.com/oven-sh/bun/releases/tag/bun-v1.3.11)
[asset metadata](https://api.github.com/repos/oven-sh/bun/releases/tags/bun-v1.3.11)
identifies `bun-linux-x64.zip` with archive SHA-256
`8611ba935af886f05a6f38740a15160326c15e5d5d07adef966130b4493607ed`.
The Dockerfile verifies that archive before reading only `bun-linux-x64/bun`,
stores the public binary at `/opt/ci-runtime/bun`, generates and checks its
**binary** digest in `/opt/ci-runtime/bun.sha256`, then checks the version.
Track the application's `packageManager` when rebuilding; this fixed pin does
not solve future requested-version changes. The runner base pin is unchanged.

Before Listener starts, `runtime_bootstrap.py` verifies the image binary and seeds
only a missing `$HOME/.bun/bin/bun`. It preserves every existing executable,
including other legitimate versions; the workflow's setup action owns version
changes. No cache binary is executed by the helper. The reproduced static-2
failure was a restored setup-bun cache followed by missing `/home/ci/.bun/bin/bun`
([run attempt 2](https://github.com/dealpipe/dealpipe/actions/runs/40091909824/attempts/2)).
The archive-path cause remains **unverified**.

An operator may repair an existing home additively without restarting its
Listener. First obtain the public binary, binary digest and reviewed helper from
the newly built reviewed image (or the official checksum-verified archive).
Never use an arbitrary cache binary as the source. Run as the home owner:
**UID1102 on the host**, corresponding to UID0 inside the rootless worker.
Example, with SOURCE and BINARY_DIGEST set to that verified public artifact:

```sh
python3 stacks/github-runner/runtime_bootstrap.py \
  --home /var/lib/dealpipe-ci/static-2/home \
  --source "$SOURCE" --sha256 "$BINARY_DIGEST"
# Review dry-run output; repeat the same command with --apply to publish.
```

Home must already exist, belong to the current EUID and be private (0700).
Ancestors cannot be symlinks or unsafely writable; root-owned sticky temporary
directories may be traversed. Existing `.bun` and `bin` must be real directories,
owned by the current EUID without group/world write (0755 is accepted).
The helper publishes with an atomic no-replace hardlink and removes only its own
temporary file; a concurrent installer wins. Symlinks and nonexecutable destination
files are refused for operator inspection. There is no recursive chown, cache
purge, home deletion or registration-state change. Repair/build/activation and
live CI validation require the parent/operator's separate runtime work; source
tests do not prove that the setup action or restored cache will succeed.

The existing registered static-2 runner has a bounded, operator-only 12 GiB
repair path in [static2-repair-operator.md](static2-repair-operator.md). The old
8 GiB first-phase provisioning default remains unchanged; 8 GiB failed real
tsgo/lint according to the 2026-10-24 operator incident receipt. The selected
12 GiB ceiling remains unverified until representative real CI passes.
