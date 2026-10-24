# Measured parallel runner capacity

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
| preview aggregate | 1 / 1 | 8 GiB | 4 CPUs |
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
pause heavy agent commands during rollout. A 32/8/8/12 GiB configuration needs
60 GiB plus R. Shrinking defaults to fit without evidence can just move queue
failures into OOM failures. Stop if the formula fails.

Admission, held for Runner.Worker lifetime before checkout:

* CI/browser: **exclusive heavy.lock**. All Docker/Supabase jobs serialize to
  prevent fixed-port collisions (including the known mail port 8788 collision).
* Static replicas: shared `static.lock`; socket-free light jobs run together and
  concurrently with a stack job. Heavy agent admission no longer excludes these
  bounded light jobs, so their memory must remain in the aggregate budget.
* Preview build/publish: exclusive `preview.lock`; one job at a time across these
  two roles, separately budgeted from CI. Their runners can register concurrently.
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
