# CI engine network hygiene and orphaned-queue detection

Incident 2026-10-24 (Dealpipe PR #829, staging run 40083703557):

1. **Network pool exhaustion.** The browser engine held 30 empty
   `ci-*`/`supabase_network_ci-*` networks from five completed runs. Docker's
   built-in pools fit about thirty bridge networks, so every new job failed with
   `all predefined address pools have been fully subnetted`. An operator removed
   them by hand under the exclusive workload lock.
2. **Orphaned queued job.** An agent diagnostic removed `dealpipe-nas-ci` from the
   production runner from 14:30 to 14:40 UTC. `core / coverage-merge`, queued at
   14:35, never started after the label returned, although the runner was idle.
   Cancelling the run and rerunning failed jobs recovered it.

## Guards

| Guard | Where | Effect |
|---|---|---|
| In-job cleanup of the job's own project | Dealpipe #829 | First line; cannot run after runner loss |
| Lane job hooks remove empty CI networks | `network_reaper.py` via `job-hook.py` | At job start (leftovers of a lost previous job) and completion (still inside admission); 60 s budget; never fails a job |
| Explicit `default-address-pools` (/24, 256 networks) | main `10.201/16`, browser `10.202/16`, CI2 `10.203/16` | Headroom so a missed cleanup degrades slowly instead of after ~30 networks |
| No relabeling of production runners | `stacks/agents/policy/AGENTS.md` | Removes the trigger of the orphaned job |
| `queue-watchdog.py` | operator / scheduled agent | Reports jobs queued > 15 min while a matching runner is idle; optional `--redispatch` |

The reaper only matches `ci-<8+ digit run>-<attempt>-<job>` and
`supabase_network_ci-…` names with **zero attached containers**, older than 60 s,
removed by immutable ID. `dealpipe-wt-*`, `bridge`/`host`/`none` and all other
networks never match; containers and volumes are not touched. It is enabled only
where `AGENT_CI_NETWORK_HYGIENE=1` (the main CI, browser and CI2 lanes, each one
Worker per engine). Static, deploy and preview images never run it.

## Rollout (operator, drained window)

Neither part is active until rolled out; this repository change alone does not
alter the host.

1. **Runner image:** rebuild and redeploy through the existing reviewed path
   (`deploy.py`, or `parallel.py` render for the parallel layout). The image now
   contains `network_reaper.py`; `compose.yaml`/`parallel.py` set the opt-in.
2. **Address pools:** apply the NixOS configuration, then restart each rootless
   engine while its lanes are idle (`systemctl --user` as `dealpipe-ci`:
   `docker`, `docker-browser`, and `docker-ci2` when enabled). The browser and
   CI2 units have `restartIfChanged = false`. Restarting the **main** engine also
   stops the `dealpipe-wt-*` worktree containers on it (they return only if they
   have a restart policy); coordinate with active agent work. Existing networks keep their subnets; only new ones use the pool.
3. Verify: `docker info --format '{{json .DefaultAddressPools}}'` per socket, then
   one browser job whose "Complete job" log shows the hygiene line or no leftovers,
   and the inventory command from the incident showing no `ci-*` networks idle.

## Orphaned-queue check

```bash
python3 stacks/github-runner/queue-watchdog.py              # report; exit 1 if found
python3 stacks/github-runner/queue-watchdog.py --redispatch # cancel + rerun failed
```

Redispatch re-reads the run and skips it if any job is in progress. GitHub has
no conditional cancel, so a job starting in the instant before the cancel is
cancelled and then rerun by `--failed`: repeated work, not lost work. Run it after every
label change or runner replacement, and before blaming capacity for a long queue.
