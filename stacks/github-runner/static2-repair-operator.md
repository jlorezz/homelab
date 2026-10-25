# Static-2 12 GiB repair (operator execution only)

This is a bounded replacement of existing runner ID **121**, name
`dealpipe-nas-static-2`, container `dealpipe-ci-static-2-1`, on the main rootless
engine at `/run/user/1102/docker.sock`. It does not register a runner, obtain a
token, change GitHub labels, touch static-1/browser/CI2/deploy, or activate Nix.
Parent/operator execution follows independent review. The implementation agent
has performed only offline source checks and mocked tests.

## Incident and limits

The operator reported that real PR CI `tsgo`/lint was OOM-killed at
2026-10-24T14:06:15Z in the 8 GiB static-2 container
`6f19fc9624c81007d64d5161b1162c2a8ac1a77638551ecafef360a01409f44c`,
with approximately 7.6 GiB anonymous RSS. The unchanged parent was
32 GiB / 8 CPUs / zero swap; its ancestor counter included this child OOM,
without evidence of an aggregate OOM. These are supplied operator observations,
not measurements made by this source-only change.

The selected replacement ceiling is **12 GiB memory, 12 GiB memory+swap,
2 CPUs, 2048 PIDs**, host networking, init, user `0:0` inside the rootless
container. Equal memory/swap ceilings allow no additional container swap, and
parent swap remains zero. A ceiling is not a memory allocation. **12 GiB demand
is UNVERIFIED until full real tsgo/lint CI passes**; no working-set peak is
invented here. Two representative overlapping jobs and queue improvements
remain separate acceptance work under [Homelab #16](https://github.com/jlorezz/homelab/issues/16).

`static-replica.py` remains the historical 8 GiB first-phase provisioning helper.
Do not rerun it to repair this registered runner or silently increase its default.
Use `repair-static2.py` for this specific migration.

## Prerequisites owned by the parent/operator

1. Keep **only ID 121** quarantined from normal static scheduling: remove its
   `dealpipe-nas-static` custom label and confirm GitHub reports `busy=false`.
   Registration and Listener stay in place. Labels/status alone are not a drain;
   the helper additionally freezes the actual Listener and checks for Workers.
   Keep the quarantine throughout repair and verification. Do not alter the
   other restored CI/static-1/browser labels or deploy.
2. Verify the reviewed native-Worker image is already present on the main engine:
   `sha256:c766827a8bf4173c036e831688e5a7da4db19cd0b382bb7e026b68be7bf3baaa`.
   No pull/build is performed. The supplied prior CI2 diagnostic canary
   `40101451702` established checkout/node/Bun/Docker/sibling-bind/Postgres and
   native Worker global EX + engine EX + lease custody with CLOEXEC and release;
   it does not establish static-2's full lint memory demand.
3. Atomically add `static-2: {"memoryMiB": 12288, "cpuPercent": 200}` to the
   existing otherwise valid **expired** policy at
   `/var/lib/agent-workloads/ci-policy/policy.json`. Preserve its evidence and
   honest expiry; describe this profile as a full hard-ceiling reservation with
   unverified representative demand. Do not refresh expiry to claim parallel
   acceptance. Retain existing other profiles/budgets. Provision the existing
   admission ledger/mutex and heavy lock without replacing their inodes.
   Keep policy changes serialized with this repair and initial verification.
4. Verify fresh host/cgroup headroom. The helper requires 32 GiB / 8 CPU /
   zero-swap parent, at least 11,865 MiB **and** 25% physical RAM reserved,
   physical capacity for parent + reserve, host MemAvailable for the full
   additional 12 GiB + reserve, and committed parent memory + 12 GiB <= 32 GiB.
   Apply also takes global EX and the existing admission ledger's full 12 GiB
   reservation. This is conservative admission, not an observed working set.
5. Run locally as host UID **1102** with access to its engine and `/proc` host
   PIDs, `pidfd_open` and `pidfd_send_signal`. Do not use container PID namespace
   numbers for host signals. Private base `/var/lib/dealpipe-ci/static-2` must be
   owner 1102/mode 0700; home/work remain private. The runner directory may retain
   mapped UID 232072/mode 0777 from the old `cp -a`, protected by that private
   ancestor. Do not recursively chown it. The helper checks BOM-aware `.runner`
   ID/name/repository/work folder, executable run.sh and credential file presence
   without opening credential contents.

## Execute once

From the reviewed checkout, run dry-run first; add `--apply` only after parent
review and current receipt checks:

```sh
python3 stacks/github-runner/repair-static2.py \
  --expected-container-id 6f19fc9624c81007d64d5161b1162c2a8ac1a77638551ecafef360a01409f44c

python3 stacks/github-runner/repair-static2.py \
  --expected-container-id 6f19fc9624c81007d64d5161b1162c2a8ac1a77638551ecafef360a01409f44c \
  --apply
```

Dry-run makes no claim, signals or engine mutations; it cannot certify frozen
idleness. Apply durably creates `.static2-12g-repair.json` before signals/engine
mutations, rejecting any prior attempt or backup. The claim remains on success
and failure. No blind retry is supported.

Apply obtains global EX and a full admission reservation; this briefly excludes
other cooperating heavy work. It uses Docker `top -eo pid,comm`, validates the
single Listener's host UID, cgroup/container identity and process start time,
and signals through a retained pidfd. After SIGSTOP it waits two bounded 250 ms
stabilization intervals, verifies every Listener thread is stopped without
children, and rejects any Worker, unknown process or process outside the
Listener's supervisor ancestor chain. An already accepted Worker causes refusal
and Listener resume, never Worker termination. The stock Actions runner
supervisor `run-helper.sh` is accepted only as an ancestor of the Listener,
alongside `run.sh`, `docker-init`, `tini`, and `bash`. A sibling with any of
those names is still refused.

Only after these checks does it disable old restart, check quiescence again,
stop the exact old ID, verify exited/inactive, and rename it to
`dealpipe-ci-static-2-1-before-12g-repair`. It creates the replacement at the
original name with the existing registration/home/work, heavy lock, admission
ledger, read-only policy directory and read-only CI pool. There is no engine
lock/socket, controller secret or registration-token mount. Environment uses
profile `static-2`, `AGENT_CI_EXCLUSIVE=0`, `AGENT_CI_SOCKET_REQUIRED=0`,
`AGENT_CI_EXPIRED_POLICY_FALLBACK=1`, and the reviewed image's native hooks.
Exact limits, mounts, image and hooks are checked before start.

The initial expired policy causes each real Worker to take **global EX** and the
12 GiB ledger reservation. It excludes legacy heavy-lock users without ledger
entries. This does **not** establish full parallel acceptance. Historical OOM
counters are accepted as baseline; any increment during repair fails the check.

## Verify and recover

The parent verifies listener/GitHub health and registration remains ID 121,
unchanged parent resources, unchanged unrelated container/unit identities, native
Worker FD custody/release, full real tsgo/lint completion and OOM deltas. Route
verification with an operator-controlled private label while the normal static
label remains absent; label management belongs to the parent. Restore the normal
label only after successful verification. Record actual memory observations and
remaining limits; failed tsgo/lint means the 12 GiB selection remains unverified.

Errors print only a public stage; inspect details privately without dumping
`Config.Env`, credential contents or tokens. If failure occurs before the old
stop is attempted, only the same frozen Listener may receive automatic SIGCONT.
If restart was already disabled, it stays disabled; inspect before restoring it.
Once stop is attempted, even an ambiguous timeout gets no automatic resume,
restart, stop, remove or rename rollback. Old backup, candidate and all persistent
state are retained. A started replacement is never automatically stopped.

Manual rollback requires the parent to quarantine and externally drain the new
runner first, prove it inactive using an equivalent race-free drain, and only
then restore the retained old container/name/restart policy. Do not use a single
idle observation followed by stop. Shared persistent directories are not a
snapshot: inspect any changes made by the new image/runner before old restart.
Do not erase the attempt marker or backup to make retry appear fresh. Reconcile
an interrupted attempt explicitly in a separately reviewed operator action.

Brain context was searched on 2026-10-24: source status healthy; issue #16 chunk
revision `fac8d7ac5d6f36c387672b5c55ba262fbcd2240ca68ae6941ac4a25e9d92476d`
(indexed 14:16:54Z, upstream refresh 13:40:59Z) predates the reported OOM. Its old
8 GiB startup success is historical context, not evidence that lint fits.
