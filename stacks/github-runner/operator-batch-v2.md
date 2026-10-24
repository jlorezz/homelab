# Florian's NAS batch v2 — definitions reviewed, activation NOT performed

2026-10-24. Repository https://github.com/jlorezz/homelab; base
`4bfead617fc26e6d634b1696061729716127822b`; branch `feat/ci-runner-parallelism`.
Worktree: `/home/agent/.t3/worktrees/homelab/feat-ci-runner-parallelism`.
Owner: Florian Bress via Eng Lead. Scope: Homelab runner capacity/admission;
no Dealpipe edits, merge, production promotion, or GitHub settings changes.

**Run these commands yourself ON THE NAS. The agent has not accessed the host.**
`/etc/nixos/homelab` identity, every host path, live unit provenance, headroom,
job peaks, rollback and speedup are UNVERIFIED until these gates pass. Stop on
any failed command; never continue into the next step after a failed gate.
Use an interactive bash terminal, keep shell tracing OFF, and retain the variables
in the same shell. Commands below may require Florian's existing operator rights.
Do not paste token values into this file, chat, Nix, Git, shell arguments or logs.

Acceptance: >=2 concurrent socket-free CI-class jobs when measured capacity
permits, separate preview labels, separate staging admission, unchanged production
unit/image/controller, no worker controller credential or host socket, no new OOM
or port conflict, and lower measured queue times. Two simultaneous Docker/Supabase
jobs are deliberately excluded: fixed ports still collide; they keep exclusive
heavy.lock. One CI-labelled runner plus one browser-labelled runner remain.

## 0. Read-only identity, inventory, measurements and rollback baseline

```bash
set -euo pipefail
set +x
cd /etc/nixos/homelab
test "$(git rev-parse --show-toplevel)" = /etc/nixos/homelab
test -z "$(git status --porcelain)"
git remote get-url origin | python3 -c 'import sys; u=sys.stdin.read().strip(); assert u in ("https://github.com/jlorezz/homelab.git", "git@github.com:jlorezz/homelab.git", "ssh://git@github.com/jlorezz/homelab.git"), "Wrong repository; STOP"'
BASE_COMMIT=$(git rev-parse HEAD)
test "$BASE_COMMIT" = 4bfead617fc26e6d634b1696061729716127822b || { echo "Host checkout differs from reviewed base; STOP for rebase/review"; exit 1; }
OLD_SYSTEM=$(readlink -f /run/current-system)
test -x "$OLD_SYSTEM/bin/switch-to-configuration"
BATCH=$(mktemp -d /var/tmp/runner-rollout-v2.XXXXXX)
printf '%s\n' "$BASE_COMMIT" > "$BATCH/base-commit"
printf '%s\n' "$OLD_SYSTEM" > "$BATCH/old-system"
cp machines/nas/configuration.nix "$BATCH/configuration.nix.before"
PROD_FRAGMENT=$(systemctl show dealpipe-deploy-production.service -p FragmentPath --value)
test -n "$PROD_FRAGMENT"
cat "$PROD_FRAGMENT" > "$BATCH/production-live-before.service"
systemctl show dealpipe-deploy-production.service -p DropInPaths --value > "$BATCH/production-dropins-before"
test -z "$(cat "$BATCH/production-dropins-before")" || { echo 'Production has drop-ins: reconcile them before proceeding'; exit 1; }
systemctl show dealpipe-deploy-production.service -p LoadCredential --value
# Verify the path is github-token:/var/lib/dealpipe-deploy/github-token.
# If different, STOP; use the existing path in the literal config diff below.
systemctl show user-1102.slice dealpipe-deploy.slice -p ControlGroup -p MemoryCurrent -p MemoryMax -p CPUQuotaPerSecUSec
cat /proc/meminfo
cat /proc/pressure/memory
cat /proc/pressure/cpu
nproc
sudo docker --host unix:///var/run/docker.sock ps --format '{{.Names}} {{.Image}}' --filter name=dealpipe-deploy-
# Targeted image IDs only: never dump docker inspect Config.Env (may contain job secrets).
PROD_ID=$(sudo docker --host unix:///var/run/docker.sock ps -q --filter name=dealpipe-deploy-production-)
STAGE_ID=$(sudo docker --host unix:///var/run/docker.sock ps -q --filter name=dealpipe-deploy-staging-)
test "$(printf '%s\n' "$PROD_ID" | wc -l)" -eq 1 && test -n "$PROD_ID"
test "$(printf '%s\n' "$STAGE_ID" | wc -l)" -eq 1 && test -n "$STAGE_ID"
DEPLOY_IMAGE=$(sudo docker --host unix:///var/run/docker.sock inspect --format '{{.Image}}' "$PROD_ID")
test "$DEPLOY_IMAGE" = "$(sudo docker --host unix:///var/run/docker.sock inspect --format '{{.Image}}' "$STAGE_ID")"
printf '%s\n' "$DEPLOY_IMAGE" > "$BATCH/deploy-image"
test "$(sudo stat -c '%a:%U:%G' /var/lib/dealpipe-deploy/github-token)" = 600:root:root
# Must be root:root 600. No cat, no copying the credential into evidence.
gh api --paginate repos/dealpipe/dealpipe/actions/runners --jq '.runners[]|{name,status,busy,labels:[.labels[].name]}' | tee "$BATCH/runners-before.jsonl"
sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock ps --filter publish=8788 --format '{{.Names}} {{.Ports}}'
```

Record NAS load conditions and pause other heavy agent commands for the maintenance
window. All baseline runners must be idle before mutation; do not kill jobs.
The live production/staging controllers exist despite the indexed configuration
not enabling them. Identify their current source/override with `systemctl cat`
and `git diff` locally; do not proceed until the configuration below reproduces
the production unit exactly. If images differ, a common `.image` is insufficient:
STOP and reconcile that drift in a separate reviewed change.

Read-only steps have no rollback. What could go wrong: wrong repo, missing live
units, unaccounted cgroups, wrong credential path or unknown port owner. STOP;
do not delete the lock, remove an unknown container, or guess capacity.

## 1. Get the branch and measure using its read-only sampler

The sandbox has a real `origin` remote. The delivery receipt at the end of this
file records whether push succeeded. If pushed:

```bash
git fetch origin refs/heads/feat/ci-runner-parallelism:refs/remotes/origin/feat/ci-runner-parallelism
git switch -c rollout/ci-runner-parallelism-v2 origin/feat/ci-runner-parallelism
git merge-base --is-ancestor 4bfead617fc26e6d634b1696061729716127822b HEAD
```

If push failed, use this only if the operator can actually read the worktree on
this NAS (its NAS visibility is UNVERIFIED):

```bash
SOURCE=/home/agent/.t3/worktrees/homelab/feat-ci-runner-parallelism
test -d "$SOURCE"
test "$(git -C "$SOURCE" remote get-url origin)" = https://github.com/jlorezz/homelab.git
git -C "$SOURCE" merge-base --is-ancestor 4bfead617fc26e6d634b1696061729716127822b feat/ci-runner-parallelism
git fetch "$SOURCE" refs/heads/feat/ci-runner-parallelism:refs/remotes/rollout/ci-runner-parallelism
git switch -c rollout/ci-runner-parallelism-v2 refs/remotes/rollout/ci-runner-parallelism
```

If neither transport exists, STOP. Transfer a normal `git bundle` from the stated
worktree through your approved file transfer mechanism; no fabricated NAS path.
Verify the receipt's final commit with `git rev-parse HEAD` before proceeding.
Rollback for this source-only step: `git switch --detach "$BASE_COMMIT"` while
clean. No host runtime has changed. If the operator has newer local changes,
STOP and rebase/review separately; do not reset them.

Measurement is step 0 continued; no services are started:

```bash
CI_CGROUP=$(systemctl show user-1102.slice -p ControlGroup --value)
DEPLOY_CGROUP=$(systemctl show dealpipe-deploy.slice -p ControlGroup --value)
test -n "$CI_CGROUP" && test -n "$DEPLOY_CGROUP"
# Inspect all runner workers' CgroupParent/Mounts first; verify these two aggregate
# cgroups cover ALL existing workers, and neither contains the other.
sudo docker --host unix:///var/run/docker.sock inspect --format '{{.Name}} {{.HostConfig.CgroupParent}} {{json .Mounts}}' "$PROD_ID" "$STAGE_ID"
python3 stacks/github-runner/measure-capacity.py \
  --cgroup "/sys/fs/cgroup$CI_CGROUP" \
  --cgroup "/sys/fs/cgroup$DEPLOY_CGROUP" > "$BATCH/measurements.json"
cat "$BATCH/measurements.json"
# Add other EXISTING runner aggregate cgroups with additional --cgroup arguments
# if inventory finds them; never count both a parent and child.
```

Sampler runs ~5 minutes, read-only. Repeat during representative NAS background
load and use the lowest headroom, largest reserve, and smallest CPU budget from
the samples. Do not treat idle NAS memory as proven peak capacity. No pool usage is added back. Cgroup readings are evidence only. `H = min(MemAvailable)`; never add back cgroup usage because that can
double-count reclaimable cache; `R >= max(8192 MiB, ceil(MemTotal/4))`.
All enabled pool ceilings must fit `H-R`; CPU pool ceilings must fit `nproc-2`.
No host measurements are supplied by the agent.

## 2. Prepare images, tokens and a literal configuration patch; no activation

Both existing deploy workers retain their pinned image. Build ONLY the new
preview image on the NAS; this starts no runner or stack:

```bash
agent-workload run --queue-timeout 300 --timeout 1800 -- sudo python3 stacks/github-preview-runner/build-image.py > "$BATCH/preview-image"
PREVIEW_IMAGE=$(tail -n 1 "$BATCH/preview-image")
sudo docker --host unix:///var/run/docker.sock image inspect --format '{{.Id}}' "$PREVIEW_IMAGE"
```

What could go wrong: build/download failure or runner version rejected by GitHub.
STOP; existing images/workers remain intact. Rollback: leave unused image in place
until normal reviewed image cleanup; no prune. Do not rebuild production.

Existing root-only controller credential is reused, never copied into a worker.
If absent or invalid, STOP and provision a repository-administration scoped
credential privately through the existing operator secret manager to
`/var/lib/dealpipe-deploy/github-token` (root:root 0600). This batch does not ask
for its value. Supply fresh one-hour runner registration material when needed:

```bash
set +x
gh api --method POST repos/dealpipe/dealpipe/actions/runners/registration-token --jq .token \
  | sudo install -o dealpipe-ci -g dealpipe-ci -m 0600 /dev/stdin /var/lib/dealpipe-ci/secrets/registration-token
```

No token reaches the terminal, Nix store, Git, evidence, or command arguments.
A failed pipeline stops the batch; refresh privately before retrying registration.
No secret rollback copy is made; reissue an expired registration token.

Generate the literal diff from measured inputs (no prose edits). The preview and
staging budgets are UNVERIFIED starting estimates; STOP if they or CI job peaks
are insufficient, and obtain measured/reviewed revised limits. This example uses
32/8/8/12 GiB aggregates: total 61440 MiB plus the measured reserve. Individual
CI/browser 8 GiB each, two static 4 GiB each, engine reserve 4 GiB total 28672 MiB
within CI's 32768 MiB. CPU total is 8+4+2+4 = 18 logical CPUs; it requires at least
20 measured logical CPUs including the two-CPU reserve. These are conditional
limits, never invented NAS measurements. If they do not fit, STOP; do not activate
an oversubscribed configuration or silently reduce test memory.

```bash
export BATCH DEPLOY_IMAGE PREVIEW_IMAGE
python3 - <<'PY'
import difflib, json, os, re
from pathlib import Path
b = Path(os.environ['BATCH'])
m = json.loads((b/'measurements.json').read_text())
assert m['measuredHeadroomMiB'] >= 61440 + m['reserveMiB'], 'Insufficient measured RAM; STOP'
assert m['measuredCpuPercent'] >= 1800, 'Insufficient measured CPU; STOP'
for key in ('DEPLOY_IMAGE', 'PREVIEW_IMAGE'):
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', os.environ[key]), key
p = Path('machines/nas/configuration.nix')
before = p.read_text()
anchor = '  qnoxslab.githubRunner.enable = true;\n'
assert before.count(anchor) == 1
assert 'qnoxslab.githubDeploymentRunners.enable' not in before, 'Live drift already reconciled differently; STOP for review'
addition = '''  qnoxslab.githubRunnerCapacity = {
    enable = true;
    measuredHeadroomMiB = HEADROOM;
    measuredTotalMiB = TOTAL;
    reserveMiB = RESERVE;
    measuredCpuPercent = CPU;
    ciPool = { count = 1; memoryMiB = 32768; cpuPercent = 800; };
    ci = { count = 1; memoryMiB = 8192; cpuPercent = 400; };
    browser = { count = 1; memoryMiB = 8192; cpuPercent = 400; };
    static = { count = 2; memoryMiB = 4096; cpuPercent = 200; };
    engineReserveMiB = 4096;
    previewPool = { count = 1; memoryMiB = 8192; cpuPercent = 400; };
    previewBuild = { count = 1; memoryMiB = 8192; cpuPercent = 400; };
    previewPublish = { count = 1; memoryMiB = 2048; cpuPercent = 100; };
    staging = { count = 1; memoryMiB = 8192; cpuPercent = 200; };
    production = { count = 1; memoryMiB = 12288; cpuPercent = 400; };
  };
  qnoxslab.githubDeploymentRunners = {
    enable = true;
    image = "DEPLOY_IMAGE";
    previewImage = "PREVIEW_IMAGE";
    credentialFile = "/var/lib/dealpipe-deploy/github-token";
    environments = [ "staging" "production" "preview-build" "preview-publish" ];
  };
'''
for marker, key in [('HEADROOM','measuredHeadroomMiB'), ('TOTAL','measuredTotalMiB'), ('RESERVE','reserveMiB'), ('CPU','measuredCpuPercent')]:
    addition = addition.replace('= '+marker+';', '= '+str(m[key])+';')
addition = addition.replace('"DEPLOY_IMAGE"', '"'+os.environ['DEPLOY_IMAGE']+'"').replace('"PREVIEW_IMAGE"', '"'+os.environ['PREVIEW_IMAGE']+'"')
after = before.replace(anchor, anchor + addition)
patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='a/'+str(p), tofile='b/'+str(p)))
(b/'capacity.patch').write_text(patch)
print(patch)
PY
git apply --check "$BATCH/capacity.patch"
git apply "$BATCH/capacity.patch"
git diff --check
```

Rollback source edits before activation: `git apply -R "$BATCH/capacity.patch"`.
Failed patch/application or measured budget: STOP; runtime unchanged. If your
current config already has deployment options elsewhere, generate and review a
literal diff against that configuration instead; this exact batch cannot infer
or override undocumented live production changes.

## 3. Offline gates and production equality; build only

```bash
agent-workload run --queue-timeout 300 --timeout 300 -- bash stacks/github-runner/production-equivalence.sh 4bfead617fc26e6d634b1696061729716127822b "$BATCH/synthetic-units"
agent-workload run --queue-timeout 300 --timeout 600 -- nix eval --offline --raw path:.#nixosConfigurations.nas.config.system.build.toplevel.drvPath
agent-workload run --queue-timeout 300 --timeout 600 -- nix flake check --offline --no-build
nix eval --offline --raw 'path:.#nixosConfigurations.nas.config.systemd.units."dealpipe-deploy-production.service".text' > "$BATCH/production-proposed.service"
cmp "$BATCH/production-live-before.service" "$BATCH/production-proposed.service"
agent-workload run --queue-timeout 300 --timeout 1800 -- nixos-rebuild build --flake path:.#nas
NEW_SYSTEM=$(readlink -f result)
test -x "$NEW_SYSTEM/bin/switch-to-configuration"
```

Require byte-identical production comparison: **any diff blocks activation**.
The synthetic comparison uses equal fixtures; the live comparison reconciles
drift. Missing Nix/store inputs: STOP and arrange the normal approved build
prerequisites. No offline result alone proves capacity or live isolation.
Rollback a failed gate: reverse capacity.patch and switch source branch back
when clean. No running system changed. Do not ignore failed assertions.

## 4. Idle window, activation, bounded CI registration

First verify *all* runner registrations are idle and CI workers have finished.
GitHub runner inventory must have no busy entries for this NAS; `require_idle`
in the helper is an additional check, not a complete scheduler drain. Use your
existing maintenance/drain procedure. If work arrives, wait; do not cancel it.
Capture non-secret baseline rootless container IDs before stopping only idle
listeners (not databases). Verify that these IDs are runner services only:

```bash
busy=$(gh api --paginate repos/dealpipe/dealpipe/actions/runners --jq '.runners[]|select(.busy)|.name')
test -z "$busy" || { echo 'Busy runner: STOP and wait'; exit 1; }
# Continue only inside the existing maintenance/drain procedure.
sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock ps -q --filter label=com.docker.compose.project=dealpipe-ci > "$BATCH/ci-container-ids"
sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock ps --filter label=com.docker.compose.project=dealpipe-ci --format '{{.ID}} {{.Names}}'
while read -r id; do
  test -n "$id"
  sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock top "$id" | python3 -c 'import sys; assert "Runner.Worker" not in sys.stdin.read(), "Active worker: STOP"'
done < "$BATCH/ci-container-ids"
# STOP if any Runner.Worker is present. Do not stop an active job.
while read -r id; do
  sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock stop "$id"
done < "$BATCH/ci-container-ids"
```

Keep the rollback function available. Invoke it on ANY failed activation or
verification; once jobs are active, wait for completion before stopping workers.
Do not delete or recreate any lock inode, state directory, volume or network.

```bash
rollback_runtime() {
  sudo "$OLD_SYSTEM/bin/switch-to-configuration" switch
  # Only invoke after the new workers are idle; stop extra replica explicitly.
  extra=$(sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock ps -q --filter label=com.docker.compose.project=dealpipe-ci --filter label=com.docker.compose.service=static-2)
  if [ -n "$extra" ]; then
    sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock stop "$extra"
  fi
  git archive "$BASE_COMMIT" stacks/github-runner stacks/agents > "$BATCH/rollback-source.tar"
  mkdir -p "$BATCH/rollback-source"
  tar -xf "$BATCH/rollback-source.tar" -C "$BATCH/rollback-source"
  agent-workload run --queue-timeout 300 --timeout 1800 -- sudo nix shell nixpkgs#python3Packages.pyyaml -c python3 "$BATCH/rollback-source/stacks/github-runner/deploy.py" --token-file /var/lib/agent-platform/secrets/portainer-token
  cmp "$BATCH/production-live-before.service" "$(systemctl show dealpipe-deploy-production.service -p FragmentPath --value)"
}
if ! sudo "$NEW_SYSTEM/bin/switch-to-configuration" switch; then
  rollback_runtime
  exit 1
fi
if ! cmp "$BATCH/production-live-before.service" "$(systemctl show dealpipe-deploy-production.service -p FragmentPath --value)"; then
  rollback_runtime
  exit 1
fi
# The existing helper builds through Portainer's rootless engine if necessary;
# controller/preview credentials never enter that build context.
if ! agent-workload run --queue-timeout 300 --timeout 1800 -- sudo nix shell nixpkgs#python3Packages.pyyaml -c python3 stacks/github-runner/deploy.py --token-file /var/lib/agent-platform/secrets/portainer-token; then
  rollback_runtime
  exit 1
fi
```

What could go wrong: stale short-lived registration token, image mismatch,
Portainer outage, incomplete activation, wrong cgroup enforcement, OOM, or a
job arriving during the maintenance window. Stop at the failed command. Roll
back immediately if idle; if a job has started, wait for it before rollback.
Portainer restore uses the actual base source and `Prune: false`; explicitly stop
extra replicas first. Keep old rootless state and images. If rollback itself
fails, STOP and restore the recorded old closure with your normal NAS recovery
procedure; never repeatedly restart a production worker.

## 5. Verify topology, locks, limits, and queue improvement; rollback on failure

```bash
systemctl is-active dealpipe-deploy-production dealpipe-deploy-staging dealpipe-deploy-preview-build dealpipe-deploy-preview-publish
systemctl show user-1102.slice dealpipe-preview.slice dealpipe-staging.slice dealpipe-deploy.slice -p MemoryMax -p MemorySwapMax -p CPUQuotaPerSecUSec
stat -c '%i %a %n' /var/lib/agent-workloads/{heavy,static,preview,deploy}.lock
sudo -u dealpipe-ci env XDG_RUNTIME_DIR=/run/user/1102 docker --host unix:///run/user/1102/docker.sock ps --filter label=com.docker.compose.project=dealpipe-ci --format '{{.Names}} {{.Status}}'
gh api --paginate repos/dealpipe/dealpipe/actions/runners --jq '.runners[]|{name,status,busy,labels:[.labels[].name]}' | tee "$BATCH/runners-after.jsonl"
sudo docker --host unix:///var/run/docker.sock ps --filter name=dealpipe-deploy- --format '{{.ID}} {{.Names}}'
# For EACH worker ID from the last command, inspect ONLY these fields:
# sudo docker --host unix:///var/run/docker.sock inspect --format '{{.Name}} {{.HostConfig.CgroupParent}} {{json .Mounts}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.NanoCpus}}' ID
# Repeat targeted inspect on each rootless runner via its rootless socket.
cat /proc/pressure/memory
cat /sys/fs/cgroup/user.slice/user-1102.slice/memory.events
```

Require online names: `dealpipe-nas-ci` x1, `dealpipe-nas-browser` x1,
`dealpipe-nas-static` and `dealpipe-nas-static-2` with static label x2;
disposable `dealpipe-deploy-preview-build-*` with `dealpipe-nas-preview` x1,
`dealpipe-deploy-preview-publish-*` with `dealpipe-preview-publish` x1,
staging and production x1 each with their existing labels. Ephemeral names change
per job; wait for replacement registration, not a particular suffix. Do not merge
or alter Dealpipe routing here; give its owner evidence that both preview labels
are online before #827 is applied.

Require rootless CI/browser mounts: rootless socket only + heavy.lock, hook env
`AGENT_CI_EXCLUSIVE=1`; static: no socket + static.lock, shared hook. Check only
that environment key, never dump all job environment variables. Deploy workers:
no host socket, controller credential, home/work/service volume, host networking,
or privileged mode; only the expected lock bind. Preview uses preview.lock and
preview slice; staging deploy.lock and staging slice; production unchanged
heavy.lock and deploy slice. Cgroup limits must match the measured configuration,
with MemorySwapMax=0. Any failed check: wait until idle, call `rollback_runtime`,
verify original runner inventory and production unit, and STOP. No lock removal.

Observe naturally arriving jobs, without dispatching a production release. Confirm
two static jobs actually overlap while a stack job and a preview job run, with
no new memory.events oom/oom_kill increments, sustained memory PSI, or fixed-port
errors. Record baseline memory.events for **all** aggregate cgroups before work,
then compare after representative load. If pressure or errors increase, stop new
capacity at idle and roll back; do not raise limits without new measurements.
The 8788 collision may predate this rollout; identify its owner, never remove an
unknown stack. CI stack serialization prevents new overlap but cannot repair an
already occupied port.

In GitHub: Settings → Actions → Runners shows online/idle/busy labels; Actions
shows queued jobs. Read-only queue snapshot:

```bash
gh api --paginate 'repos/dealpipe/dealpipe/actions/runs?status=queued&per_page=100' --jq '.workflow_runs[]|{id,name,created_at}' > "$BATCH/queued-runs.jsonl"
# This counts queued RUNS, not jobs. For each queued/in-progress run ID:
# gh api --paginate repos/dealpipe/dealpipe/actions/runs/ID/jobs?per_page=100 --jq '.jobs[]|select(.status=="queued")|{id,name,labels,created_at,started_at}'
```

Count queued jobs across those responses; compare at similar arrival rates.
For the next 10 representative completed validations, compare median/p90
created_at→started_at, run wall time, and Set up runner hook duration separately
from execution. Historical snapshot: static queue median/p90 2.32/27.58 min,
CI 7.89/42.90, browser 5.79/31.17 (sample/method in runner-capacity-plan.md).
These are comparison inputs, not promised speedup; dependency delay can be part
of the queue proxy. Acceptance is lower queues/hook waits without OOM/port errors,
checks weakened, or production-unit changes. If queues stay high, retain evidence
and reassess workload arrival, port isolation and peak memory; do not silently
add more socket-bearing runners. Production still takes heavy.lock and can block
CI stacks by design under the production-equivalence constraint.

After successful host validation, Florian can commit the measured local config
with a normal commit for review. Do not commit secrets or this machine's raw logs.
No merge or promotion is authorized by this batch. Actual provider spend unknown;
no capacity purchase requested or made.

## Sandbox delivery receipt

Pending final commit/push and advisory review; see appended receipt below.
