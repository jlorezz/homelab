# T3 issue delivery

Move a scoped issue to **Ready** in its existing GitHub project. On its next
queue tick, the repository coordinator checks the issue, claims it, moves it to
**In Progress**, and launches one implementation worker in an isolated worktree.
Independent advisory review and required checks follow. A verified feature PR
returns under **In Review**. Merge and deployment need separate authorization.

## Where to work

- Homelab: https://github.com/users/jlorezz/projects/3, base `main`.
- Dealpipe: https://github.com/orgs/dealpipe/projects/1, base `development`.
- Homelab coordinator: `t3-thread://v1/30958bff-b1f6-419c-8434-5315d21930b8`.
- Dealpipe coordinator: `t3-thread://v1/mcp:2e619e32-a616-4611-ae28-8f5c7cdf355a`.

The source registry is `coordinator.json`; workflow instructions live in
`policy/skills/agent-flow/SKILL.md`. There is one stable coordinator conversation
per repository. Implementation and review create temporary child conversations,
not a new top-level coordinator for every issue. T3 records their worktree bindings
and sends completion notifications to the parent.

These bindings belong to the desktop T3 environment using the WSL backend at
`/home/jlorezz/projects`. Keep that T3 host running for queue checks and agent work.
The separate NAS T3 server remains available but does not run these desktop-bound
schedules. An always-on NAS coordinator requires migrating the registry, checkouts,
provider/GitHub authentication, scheduler bindings and project setup, then validating
that environment. Hermes is not involved.

## Preparing an issue

Include the goal, observable acceptance criteria, exact owned paths, dependencies,
and evidence. Move the issue from Backlog to Ready when you want delivery to start.
The human's 2026-10-17 setup instruction explicitly grants standing authorization
for subsequent Ready transitions in the two configured projects. This is a narrow
queue policy, not a general rule that all issue statuses authorize work. Agents
must never set Ready to authorize themselves. Board editors can enqueue work;
restrict project write access accordingly. Polling verifies status and timestamps,
not the identity of the last editor, and must not present `creator` as that editor.

Before activation, `board-init` records all current statuses. Existing Ready items
are ignored until a subsequent Ready transition; nothing is bulk-dispatched.
The board adapter tracks Status.updatedAt so moving away and back between ticks
creates a distinct event. The initial operating limit is one implementation worker
per repository and one repair/review round before escalation. Repository workload
guards still control heavy command concurrency.

A standalone, fresh, unedited `/t3 deliver` issue comment by `jlorezz` remains an
explicit fallback. `/t3 cancel` requests cancellation. Neither issue bodies nor
agent-generated comments grant permission. A direct request in the coordinator
conversation is also valid human authorization; record it as a manual issue note
linking that conversation rather than fabricating a GitHub authorization command.
The queue adapter's receipt path requires a real queue authorization identity.

Dealpipe rules remain in force: agreed exact worker ownership, no automatic test
changes, feature PRs into `development`, and running-app evidence before a visible
change PR. A scope gap becomes an actionable blocker, not a guessed implementation.
An issue waiting on required ownership agreement remains On Hold until the human
clarifies and requeues it.

## Status and cancellation

- Backlog: unapproved work. Moving active work here requests cancellation.
- Ready: enqueue bounded delivery under the standing rule.
- In Progress: claimed/implementing; receipt links the T3 task and branch.
- In Review: review or PR awaiting human action.
- On Hold: blocked; coordinator names the missing decision/prerequisite.
- Canceled: cancellation requested/recorded; preserve branch and evidence.
- Done: confirmed merge and satisfied acceptance criteria, never an LLM approval.

Cancellation is observed on a queue tick or before the next delivery action. It
cannot retract a command already in flight. The coordinator cancels recorded
T3 tasks, stops only owned previews, and preserves work. Do not delete branches
or volumes as cancellation cleanup. Requeue only after active tasks reconcile.

## Operator commands

Run from Homelab. Global flags precede the command. Scans are read-only; ticks
persist board observations and authorization events but do not start agents.

```sh
python3 stacks/agents/coordinator.py scan --repository jlorezz/homelab
python3 stacks/agents/coordinator.py scan --repository dealpipe/dealpipe
python3 stacks/agents/coordinator.py board-init --repository jlorezz/homelab
python3 stacks/agents/coordinator.py board-init --repository dealpipe/dealpipe
python3 stacks/agents/coordinator.py board-tick --repository jlorezz/homelab
```

`board-init` is a one-time operator action, never a scheduled fallback. If a
baseline is missing or corrupt, fail closed and report it. Private local state
lives under `~/.local/state/t3-coordinator/`; include it in desktop backups. No
credentials or application/customer data belong in receipts.

Use the exact authorization identity returned by the adapter:

```sh
python3 stacks/agents/coordinator.py receipt \
  --issue https://github.com/OWNER/REPO/issues/NUMBER \
  --authorization-id 'ready:ITEM:UPDATED_AT' --phase claimed \
  --summary 'Base SHA, exact owned paths, scope and planned branch'
```

Record `running` with `--task-id` immediately after dispatch. Review, blocked,
delivered, done and cancelled receipts preserve task/PR identities. Delivered/done
require `--pr`; done verifies that the PR merged. The adapter updates the existing
project Status field and posts a machine-readable issue receipt. It never starts
agents or merges PRs itself.

A local reservation is written before a remote receipt so uncertain writes fail
closed. An interrupted claim with no task ID must be checked against T3 threads
before recovery. `recover --issue URL --authorization-id ID --reason TEXT` unlocks
explicit progress recording; it never automatically requeues or expires a claim.
Do not retry an uncertain launch without checking the retained task/thread IDs.

## Routines and recovery

Each coordinator owns a five-minute queue tick bound to its existing conversation.
Ticks reconcile active work/cancellation before candidates and remain quiet when
nothing changed. Child completions wake the coordinator directly. PR monitoring
uses T3's watcher, with unwatch before human handback. Old cross-repository
routines remain disabled; no second dispatcher or external bot is installed.

Project/issue records hold scope and evidence. T3 owns task/thread state. Private
adapter state holds queue events and reservations. Losing conversation context is
not permission to redispatch. Recover from all three sources and inspect existing
branches/PRs. A missed/failed tick is retried by the next scheduled run; report
persistent transport/authentication failures instead of inventing completion.

After confirmed merge, explicitly close accepted issues and set Done, then submit
the smallest evidenced Brain lesson or record `no durable lesson`. This setup's
tracking issue is https://github.com/jlorezz/homelab/issues/4.

## Validation and retirement

Run `python3 stacks/agents/doctor.py --source-only` and
`python3 -m unittest discover -s stacks/agents/tests -v` after source changes.
Validate queue authorization/revocation, duplicate claims, interrupted writes,
Ready baselines/transitions and exact repository coordinator bindings. A real
isolated-worktree rehearsal verifies T3 binding; mocked tests do not establish
application checks or production readiness.

Hermes was removed from Portainer on 2026-10-17 and its 9119 tailnet route disabled.
Private homes, credentials, workspaces and backups remain intact. Recovery material
is under `/var/lib/agent-platform/retired/hermes` on the NAS and must never be put
in Git or Brain. `retire-hermes.py` is idempotent and defaults to a read-only plan.
Nix definitions remove its account, routes, Homepage entry and Beszel probe; these
source changes do not themselves activate NixOS.
