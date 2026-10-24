# CI2 next phase — disabled, source implementation only

Issue: https://github.com/jlorezz/homelab/issues/16. Base:
`d9fa65983808970cd0ca3bf9815e5c802feef45a`. This supersedes the earlier
62 GiB / 18 CPU operator batch **for this NAS**. Do not run that batch or the
old `deploy.py` capacity renderer to roll out CI2: it shrinks worker ceilings,
uses exclusive stack hooks, and does not describe the live browser engine.
No production source, image, controller or Chromium override is changed here.
No NixOS switch is safe against the known source-archive drift.

Containment: UID1102 remains 32768 MiB / 800% CPU / swap0. CI2's child
`dealpipe-ci2.slice` defaults to a 16384 MiB / 400% ceiling (UNVERIFIED demand).
The daemon and Docker systemd scopes must BOTH reside in that user slice.
This shares the existing parent; it adds no host pool. Existing CI/browser/static
container ceilings are preserved, including static-2's live 8 GiB / 2 CPU cap.
CI2 copies CI1's 12 GiB / 4 CPU worker ceiling. These limits are not simultaneous
memory reservations and are not proof that two representative CI jobs fit.
Same UID means same public-IPv4 nft egress policy, not a new security boundary.
The executable and `/run/wrappers` path follow the
[pinned nixpkgs rootless module](https://github.com/NixOS/nixpkgs/blob/ca1659429c950a11a565b5764e37a1b704e17ec8/nixos/modules/virtualisation/docker-rootless.nix).

## CI2 canonical paths (source contract; live isolation UNVERIFIED)

Dealpipe's unchanged coverage/services guard requires DockerRootDir to equal
`/var/lib/dealpipe-ci/docker`. CI2 therefore uses that canonical daemon data-root,
and the worker uses `RUNNER_WORK_DIRECTORY=/var/lib/dealpipe-ci/work` with the
same canonical source and destination in its work bind. The CI2 engine must resolve
those paths inside its own mount namespace. Do not substitute physical CI2 paths
in rendered worker YAML, fake `docker info`, or relax Dealpipe's guard.

Physical host storage remains `/var/lib/dealpipe-ci/ci2/docker` and
`/var/lib/dealpipe-ci/ci2/work`. The service adds
`DOCKERD_ROOTLESS_ROOTLESSKIT_FLAGS=--copy-up=/var/lib/dealpipe-ci` and sets
`DOCKERD` to a Nix-generated child wrapper; it does not patch or rebuild Docker.
The parent supplied a read-only capture of the pinned Docker 29.8.1 script:
`DOCKERD` selects the child executable, and the official RootlessKit invocation
uses `--propagation=rslave`. RootlessKit's
[tmpfs/symlink implementation](https://github.com/rootless-containers/rootlesskit/blob/master/pkg/copyup/tmpfssymlink/tmpfssymlink.go)
creates a private tmpfs parent whose entries point into a `.ro*` backing mount.
That upstream reference describes the algorithm, not proof of the NAS runtime.

Before any filesystem mutation the wrapper requires the child marker, real UID0,
user AND mount namespaces different from the launcher snapshots, an exact tmpfs parent mount with
private/slave propagation, and both canonical paths as copy-up symlinks into the
same backing directory. Physical sources must be directories. It unlinks ONLY
those two private symlinks, creates private destinations, binds the CI2 sources,
makes each bind private, and checks source/destination device+inode identity and
mount presence before execing the real packaged dockerd. Real host canonical
directories are never removal targets. Failed setup exits; there is no fallback
to another engine or host path. Same-UID trusted repositories remain the trust
model; filesystem namespacing is not a new security boundary.

Runner and home sources remain `/var/lib/dealpipe-ci/ci2/runner` and
`/var/lib/dealpipe-ci/ci2/home`; only CI2's socket is mounted into its worker.
Exec-root, PID, socket and RootlessKit runtime remain under
`/run/user/1102/dealpipe-ci2`. Main/browser/static path contracts are unchanged.

UNVERIFIED live gates, reserved for the parent/operator after review:

- Evaluate the opt-in module and wrapper text without IFD, then build/review the
  exact generated CI2 unit and wrapper in the pinned NAS package environment.
  Nix is unavailable in the implementation worker; the earlier parent evaluation
  predates this path fix and does not validate this new wrapper.
- Verify the actual pinned RootlessKit copy-up layout and Docker DOCKERD handoff.
  The launcher snapshots its own namespaces as UID1102 before entering RootlessKit.
  UID1102 cannot read host PID1 namespace links on this NAS; the wrapper therefore
  compares with those snapshots and does not request additional proc permissions.
  Missing/invalid snapshots or an unchanged namespace must fail closed.
  Never test the wrapper directly on the host, even with a forged child marker.
- In the real RootlessKit child, verify mount/user/net namespace separation,
  canonical mounts' device+inode identity with the CI2 physical sources, and
  unchanged main host docker/work identities and contents before/after startup.
  Verify no outward propagation; do not infer this from offline command mocks.
- Query the actual CI2 socket's DockerRootDir and require the genuine canonical
  value. Prove a worker and sibling service see the same canonical work files,
  backed by CI2/work, while main-engine work/data remain untouched. Verify only
  CI2's socket, its explicit cgroup parent, DNS, egress and network isolation.
- Run the unchanged Dealpipe coverage/services guard and representative overlap
  checks only after all existing drain, capacity, native custody and production
  fingerprint gates below pass. None of those live gates is satisfied here.

## Read-only gates (operator, not implementation worker)

Run from a verified Git checkout containing the returned implementation commit,
NOT `/etc/nixos/homelab` (a source archive). Set BATCH to a new private evidence
directory and require the current production fingerprints before and after
EVERY later runtime step. Do not dump container environments or credentials.

```bash
set -euo pipefail
umask 077
BATCH=$(mktemp -d /var/tmp/dealpipe-ci2.XXXXXXXX)
git status --porcelain > "$BATCH/source-status"
test ! -s "$BATCH/source-status"
git rev-parse HEAD > "$BATCH/source-sha"
cat > "$BATCH/production.sha256" <<'HASHES'
fc4e319c5d1a929c5d1f63dfd7313c12b6689dd9f73799f31f48759d4fe2c933  /etc/systemd/system/dealpipe-deploy-production.service
edb249f00e1c626483e6103f700943742c31a3afebef689a92f913846212a85f  /etc/systemd/system.control/dealpipe-deploy-production.service.d/40-chromium.conf
HASHES
sha256sum --check "$BATCH/production.sha256"
runuser -u dealpipe-ci -- env XDG_RUNTIME_DIR=/run/user/1102 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1102/bus systemctl --user cat docker.service docker-browser.service > "$BATCH/existing-engine-units"
systemctl show user-1102.slice -p MemoryMax -p MemorySwapMax -p CPUQuotaPerSecUSec > "$BATCH/parent-limits"
cat /sys/fs/cgroup/user.slice/user-1102.slice/{memory.current,memory.events,cpu.max} > "$BATCH/parent-before"
agent-workload run --queue-timeout 300 --timeout 600 -- python3 stacks/github-runner/measure-capacity.py \
  --cgroup /sys/fs/cgroup/user.slice/user-1102.slice > "$BATCH/headroom.json"
agent-workload run --queue-timeout 300 --timeout 600 -- python3 stacks/github-runner/tests/check-ci2-nix.py
```

Inventory actual runner names, engine sockets, mounts, image IDs, restart policies
and cgroups using focused inspect, never `.Config.Env`. Export/reconcile the live
Portainer Compose (including static-2) as `$BATCH/live-reviewed.yaml`. Main and
browser are distinct engines; the renderer refuses the old source's shared browser
socket. `github-runner-browser.nix` now reflects the unit and daemon configuration
read on 2026-10-24, including the separate runtime/data roots and DNS postStart
fix. The main daemon's live DNS recovery drop-in is also represented in source.
Keep the captured unit and compare again before scoped activation; these source
changes do not restart either engine. Preserve exact production files and images.

Required measurement evidence is not supplied by this patch. Obtain representative
full CI1, CI2, browser and both static working sets INCLUDING Docker/Supabase
siblings, daemon overhead, idle listeners and margin. Capture memory.current,
memory.peak, memory.events, memory PSI and wall times, and fresh host headroom at
representative background load. A Docker worker's memory peak alone is inadequate.
CI1 currently lacks a dedicated child cap: measure its complete engine workloads
and keep the unchanged parent as the ultimate bound. Main/browser netns isolation
must also be verified before any overlap. Admission is conservative: it adds FULL
live reservations to committed cgroup usage and subtracts them from MemAvailable,
so an otherwise safe combination can remain queued. Do not bypass this check or
lower estimates to get jobs through.

Committed usage discounts only clean inactive file cache. Active cache, tmpfs/shmem,
dirty/writeback pages, anonymous and kernel memory remain committed. Nothing is
added to host MemAvailable. Offline regression fixtures cover a 28 GiB cgroup with
22 GiB inactive clean cache, then prove that tmpfs or dirty pages are not discounted.
This is accounting behavior, not evidence of real workload peaks.

The sample policy is DISABLED with null working sets. Fresh measured policy JSON
must have positive integer memoryMiB/cpuPercent for all five profiles, an evidence
reference identifying the reviewed image and measurement window, verifiedAt and
expiresAt Unix seconds no more than 24h apart, enabled=true, reserve >=11865 MiB
and >=25% physical RAM, maxConcurrentMiB <= H-R, and CPU budget <=800. Baseline
H=34797 gives H-R=22932 MiB; this is historical headroom, not a working-set proof.
If two CI reservations do not fit, expansion remains disabled. The source must
not claim issue acceptance until actual overlap evidence exists. Renewal is an
operator evidence update. With the rendered fallback enabled, expiry returns new
jobs to exclusive global admission using the last validated profile and fresh live
headroom gates. This preserves serial CI availability while preventing unrenewed
parallel operation; missing, disabled or malformed policy still fails closed. A
fallback can wait behind active shared jobs before taking its exclusive lock.
Renew the evidence to restore parallel admission. Active Workers keep their pinned
reservation through expiry or policy replacement; completion does not revalidate
the new policy. Sustained parallel throughput requires evidence renewal and remains
an operator acceptance gate, rather than a promise made by these source tests. The root-owned
policy directory `/var/lib/agent-workloads/ci-policy` is mounted read-only as a directory,
so atomic replacement of its `policy.json` is visible to existing workers.
Keep this runtime policy under the existing workload directory. RootlessKit
copy-up of `/etc` snapshots top-level entries at daemon startup, so a newly
created top-level `/etc` directory would be missing from an existing engine.
The workload directory is shared live and avoids restarting established engines. Never
replace the ledger mutex inode or mount a single policy file that pins an old inode. Active leases
retain their reservation until completion and are not killed on expiry.

After fresh evidence exists, these source-only commands render a reviewable batch:

```bash
# POLICY is a private, fresh operator-produced JSON file in the schema above.
# NEW_IMAGE is the immutable NEW hook image digest built in the normal CI image
# environment under agent-workload. The old live image is explicitly rejected.
test -n "${POLICY:?Fresh measured policy required}"
test -n "${NEW_IMAGE:?Reviewed new immutable CI hook image required}"
python3 stacks/github-runner/parallel.py --compose "$BATCH/live-reviewed.yaml" \
  --policy "$POLICY" --image "$NEW_IMAGE" --output "$BATCH/rendered"
git diff d9fa65983808970cd0ca3bf9815e5c802feef45a -- stacks/github-deploy-runner \
  machines/nas/modules/github-deploy-runner.nix > "$BATCH/production-source.diff"
test ! -s "$BATCH/production-source.diff"
```

Review both YAMLs. All existing lanes must get the new hook and policy mounts;
leaving static/browser on an old independent lock bypasses the capacity ledger.
CI1/CI2/browser acquire shared heavy.lock THEN exclusive engine lock THEN a
measured reservation. Static lanes acquire shared heavy.lock and a reservation.
Production's existing exclusive heavy.lock and ordinary `agent-workload run`
remain exclusive. Never delete/recreate a lock inode to unblock a job. Directory
and mutex must be provisioned once; lease files use flock, not cross-container PID
identity, and kernel-release on holder death. Cooperating trusted workers can
write the ledger; this is resource coordination, not isolation from malicious jobs.

The new CI image loads a Worker-only native receiver. Its constructor starts a
receiver thread and returns immediately; managed startup and JobRequest IPC run
before admission. The started hook obtains the locks/reservation and transfers
their descriptors with SCM_RIGHTS to the actual Worker. The receiver retains
CLOEXEC descriptors and acknowledges receipt. The hook verifies Worker inode,
flock mode and pinned reservation evidence before allowing checkout. Completion
only verifies that evidence. Hook exit/death after transfer cannot release the
Worker's locks, and exec children cannot inherit them. Missing preload or failed
proof never falls back to a detached helper. Queueing remains bounded at 1800
seconds in the hook; runner IPC timeouts are unchanged.

Native offline tests use a small ELF fixture. Before accepting the image, verify
the actual .NET Runner.Worker retains the descriptors through started hook,
checkout, a job, completed hook and final cleanup. Record the real runner version,
image digest and Worker process start identity. While it lives, prove same-engine
and global exclusive probes remain blocked; prove they release on Worker exit.
Check an exec'd job child and an orphan child for absence of lease descriptors.
Repeat after runner auto-update. Use the same verification as the hook:

```bash
# Inside the candidate runner, for its actual inventoried Worker PID only.
PYTHONPATH=/usr/local/lib/agent-platform python3 -c 'import sys; from custody import verify_worker; verify_worker(int(sys.argv[1]))' "$WORKER_PID"
```

Until those runtime gates pass, a passing fake-Worker test is not a deployed-safety
claim. Do not register the candidate for unrestricted parallel GitHub work first.

## Actual quiesce and drain before replacing listeners

An idle snapshot is NOT a drain. Operator must first inhibit new dispatch locally
on EVERY CI/browser/static listener. Do not freeze Runner.Worker or stop containers
running a Worker. For each inventoried permanent runner, in its actual rootless
engine, SIGSTOP ONLY Runner.Listener (all its threads). Record the exact names and
original restart policy. Example command for ONE inventoried listener:

```bash
# Repeat for all four current listeners using MAIN or BROWSER socket as appropriate.
# These variables must come from the focused live inventory, never broad ps filters.
test -n "${SOCKET:?Rootless engine socket required}"
test -n "${RUNNER:?Exact inventoried permanent runner container required}"
ci_docker() { sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host "unix://$SOCKET" "$@"; }
ci_docker exec "$RUNNER" pkill -STOP -x Runner.Listener
ci_docker top "$RUNNER" -eo pid,stat,comm
```

Verify Listener is in stopped state and no supervisor has replaced it; freeze any
replacement before continuing. The container init/restart supervisor is still
running. Existing Runner.Workers, including jobs blocked in OLD pre-checkout
exclusive hooks, must be allowed to finish. Observe until no Worker remains in
ANY frozen-listener container. GitHub busy=false alone is insufficient. If a Worker
needs the listener to finish, CONT that listener, let it finish, then repeat the
freeze/drain; never kill the Worker to meet a deadline. Jobs accepted just before
the freeze may need GitHub recovery/requeue if the listener had not spawned a
Worker; inventory their run IDs and reconcile them after replacement.

Only AFTER all listeners are frozen and all Workers absent may the operator
manually stop the exact drained containers. This removes the idle-check/new-job
race. Repeat focused top immediately before each stop; do not put this behind an
automatic helper that stops a running permanent worker.

```bash
ci_docker top "$RUNNER" -eo pid,stat,comm > "$BATCH/drained-top"
! rg -q 'Runner.Worker' "$BATCH/drained-top"
rg -q 'T.*Runner.Listener' "$BATCH/drained-top"
ci_docker update --restart=no "$RUNNER"
ci_docker stop --timeout 60 "$RUNNER"
```

If no replacement is made, abort quiesce with `ci_docker exec "$RUNNER" pkill -CONT
-x Runner.Listener` for each still-running frozen container. If already stopped,
start only the original stopped container and restore its recorded restart policy.
Never run that abort against an unknown name. Old hook image cannot provide the
new protocol: ALL current lanes must be replaced during this quiesced window.
Pin/install the new image separately in main, browser and CI2 engines; images are
not shared between engines. The permanent registration token is only the existing
short-lived registration secret; controller credentials never enter a worker.

## Activation gates and rollback

This patch intentionally provides NO full switch command. A scoped operator batch
must first reconcile the captured browser unit, build/evaluate CI2's user service
and slice, provision its tmpfiles and install only those new units. Build the new
hook image, compare its workload.py/job-hook.py digests with this commit, and
preserve all existing unit/drop-in files. Do not copy an entire evaluated generation
into the live system. The operator should review the exact unit artifacts and
literal install commands before execution; unknown live drift blocks that batch.
`qnoxslab.githubRunnerCI2.enable = true` is the opt-in; it is never set in this
checkout. The engine definition does not automatically manage a permanent worker.
Use the reviewed CI2 Compose only on its own socket after all gates pass.

Read-only post-start checks (before accepting queue improvement):

```bash
systemctl --user --machine=dealpipe-ci@.host show docker-ci2.service dealpipe-ci2.slice -p ControlGroup -p MemoryMax -p MemorySwapMax -p CPUQuotaPerSecUSec
sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/dealpipe-ci2/docker.sock info --format '{{json .SecurityOptions}} {{.CgroupDriver}}'
sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/dealpipe-ci2/docker.sock inspect dealpipe-ci2-runner --format '{{.Image}} {{.HostConfig.CgroupParent}} {{json .Mounts}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.NanoCpus}}'
gh api --paginate repos/dealpipe/dealpipe/actions/runners --jq '.runners[]|{name,status,busy,labels:[.labels[].name]}'
sha256sum --check "$BATCH/production.sha256"
cat /sys/fs/cgroup/user.slice/user-1102.slice/memory.events
cat /proc/pressure/memory
```

Require exactly one `dealpipe-nas-ci-2` with `dealpipe-nas-ci`, independent physical
main/browser/CI2 daemon backing roots and RootlessKit netns inodes, correct default
Docker cgroup parent and actual runner AND Supabase process cgroup membership
under the CI2 child and UID1102 parent. Any process escaping that child blocks
acceptance. Inspect only selected mount/cgroup fields on sibling services too.
CI2 gets only its own rootless socket; no host Docker socket, controller secret,
other runner home or physical work backing, or production volume. The canonical
CI2 work path must resolve to CI2/work inside its engine namespace. Verify UID1102 public egress filtering
covers all three engines; do not test against production/private destinations.

Observe two representative dealpipe-nas-ci jobs actually overlap with separate
engine leases and distinct localhost/Supabase ports; verify a second same-engine
job queues, agents and production exclusive lock wait, no OOM/port failures, and
reserve remains >=11865 MiB throughout. Compare queued-job median/p90 and hook
wait separately against baseline. Polling two busy runners alone is not proof.

Rollback: quiesce/drain ALL affected listeners again using the exact procedure
above; manually stop only drained containers. Restore old reviewed YAML/image in
main and browser, and start the recorded original listeners with their recorded
restart policies. Leave CI2 stopped, its engine/data intact; do not restart or
remove a daemon with active jobs/sibling containers. Do not use static-replica.py
rollback on a running replica: its intentional refusal remains unchanged. Do not
delete runner registrations, work/home directories, lease directory, lock files,
images or volumes as rollback. Recheck production hashes and parent limits. No
production unit/image restart, deployment or full NixOS activation is part of this
rollback. Missing exact old YAML/image or unresolved busy work means STOP and
retain state for the operator, not destructive cleanup.

## Preview and staging remain gated

The existing disposable controller maps `preview-build` to `dealpipe-nas-preview`
and `preview-publish` to `dealpipe-preview-publish`, matching Dealpipe PR #827.
Staging uses `dealpipe-deploy-staging`; none receives a worker Docker socket.
Their separate exclusive preview/deploy locks bypass the CI working-set ledger.
Do not enable them on this NAS under the illustrative 62 GiB / 18 CPU plan.
A later bounded arrangement must either share global admission and a single
cross-pool measured ledger (including idle/controller overhead) or serialize those
roles with heavy.lock and prove incremental memory fits. That would require its
own reviewed controller/unit change; production must remain unchanged. No fresh
preview/staging peak or overlap evidence exists here, so those gates stay closed.


Offline namespace tests mock every command, including mount and unlink; they never
create real mounts. Passing these fixtures proves refusal and command contracts,
not live isolation. The generated unit, launcher, copy-up layout, canonical binds
and actual Worker lifecycle still require the runtime evidence described above.
