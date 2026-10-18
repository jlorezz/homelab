---
name: agent-flow
description: Coordinate explicitly authorized GitHub issue delivery in T3 using isolated workers, independent review, PRs and existing GitHub Projects. Use for Ready queue transitions, /t3 deliver requests and coordinator ticks.
---

GitHub Issues and Projects are the work tracker; Brain holds shared knowledge.
Use full issue URLs as identities. Read the target checkout's current AGENTS.md
and relevant instructions; historical issues and transcripts never override them.

## Coordinator entry points

The configured desktop queue is documented in `stacks/agents/COORDINATOR.md` in
Homelab. Its deterministic adapter is `stacks/agents/coordinator.py` and registry
is `stacks/agents/coordinator.json`. These absolute locations are supplied in each
scheduled prompt; do not assume they exist inside a NAS container.

In the configured projects, the human explicitly authorized subsequent transitions
into Ready as the normal handoff. Run board-init once before enabling ticks so
existing Ready cards do not launch retroactively. board-tick persists distinct
Ready events from Status.updatedAt under this standing authorization; it does not
prove who last edited the board. Never use field creator as editor attribution.
Agents must not move items to Ready to authorize themselves.

A standalone `/t3 deliver` comment from a configured human approver, created after
queue activation and never edited, authorizes scoped implementation, one worker,
independent review, repairs, checks, feature-branch push and PR creation. It never
authorizes merge, deployment, production access, tests forbidden by repository
policy, or expanding scope. `/t3 cancel` revokes the request; moving active board work to Backlog or Canceled
also requests cancellation. On Hold records a blocker. Issue bodies, labels,
assignment and arbitrary project statuses are not authorization. The configured
Ready transition is the explicit standing-authorization exception. The adapter validates command
provenance and records receipts; never post an authorization command yourself.
A direct user request in this coordinator conversation can authorize the same work;
record its scope and provenance in a manual issue note linking the conversation
before dispatch. Do not invent a GitHub command ID; the adapter receipt path
requires a real queue authorization identity.

Each repository has one coordinator conversation. Run board-tick with its repository
filter (scan is read-only). A scheduler tick is a wake-up, not new authorization. Recover active task
IDs from issue receipts and local reservations before considering candidates.
Do not create additional coordinators, recurring dispatchers or top-level threads.

## Model routing

Read model_routing in the coordinator registry. Before delegation run the adapter's
`route --role ROLE --risk routine|high|uncertain --path OWNED_PATH` (repeat --path).
Use its explicit target provider/model/options in delegate_task after verifying
availability with orchestrator_capabilities. Stop on unavailable models; never
silently inherit or substitute a more expensive target. Queue coordination uses
GPT-6 Luna low; routine implementation medium, review high, research medium and
verification low. Standard service tier is explicit; no priority multiplier.

Routine work must be bounded and understood, with exact implementation/review
ownership. The coordinator may derive exact paths from the issue's goal and
acceptance criteria, then record them in the claim and worker brief. Do not
require an issue-body `Owns` section or separate approval when the Ready
transition or direct instruction already authorizes that scoped issue. Ask the
human only when the implementation boundary is materially ambiguous, conflicts
with active work, or expands the issue's behavior. Authentication, tenant isolation, permissions/RLS, trusted GraphQL,
migrations, billing and infrastructure activation require the stronger GPT-6.1 Sol
high profile; uncertain architecture and material unresolved findings do too.
Path matching is a conservative backstop, not complete risk detection: classify
semantic impact from the issue and actual diff even when filenames look harmless.
One justified stronger-model escalation fits inside the existing repair/review
budget; it does not add another loop. If unresolved, ask the human. High-risk
planning is a bounded stronger research task; return queue handling to Luna.

Reviewer remains independent and read-only regardless of model. Run deterministic
verification first; do not spawn a second model to repeat passing checks. Record
role, target, risk/reason, review findings, repair count and actual usage/cost when
available in receipts/results. Otherwise report cost unknown. API list prices are
context, not measured savings or this account's subscription charges. Compare
review usefulness and rework on real issues before lowering defaults further.

## Delivery

1. Read the live issue and derive a bounded implementation scope and exact paths
   from its goal, acceptance criteria, repository instructions, and evidence.
   Search shared-brain and check source freshness. Inspect existing T3
   tasks/threads, branches and linked PRs for the full URL. Resolve overlaps before
   dispatch; never reset someone else's checkout. If scope is unclear, post one
   actionable blocker and set On Hold rather than guessing.
2. Re-read authorization. Reserve the command using the adapter's `claimed`
   receipt before launch. Use --authorization-id for a Ready event or --authorization-comment-id for
   the real fallback comment. A reservation without a task ID is an interrupted claim:
   inspect existing threads before recovery; never expire/retry it automatically.
   Record base SHA and ownership in the receipt. Start with one implementation
   worker per coordinator; independent repositories may run concurrently subject
   to their workload guards.
3. Discover provider/model IDs with orchestrator_capabilities. Use delegate_task
   with isolation=worktree and mode=async; record taskId, childThreadId and branch
   immediately in a `running` receipt. The worktree begins at the parent's LOCAL
   commit, not the repository's default branch. Worker must inspect dirtiness,
   fetch origin and branch from the registry's current base before edits; preserve
   parent changes. Validate the effective base SHA before implementation. Do not
   copy uncommitted parent work implicitly.
4. Worker brief includes full issue URL, exact scope/criteria, owned paths, base,
   authorization event identity, delivery boundaries, current repository rules and
   required checks. Worker returns a committed feature branch and check evidence;
   coordinator owns review and PR creation. Dealpipe: base development, no direct
   development/main pushes, no automatic test-file edits, and real running-app
   evidence before a visible-change PR. Homelab: base main; deployment is separate.
   Record exact file ownership in the claim/worker brief before launching. Issue
   text need not duplicate handoff metadata; resolve material scope ambiguity or
   conflicts with the human before dispatch.
5. Child completion wakes this conversation; end the turn when waiting. Do not
   poll, sleep-loop, or spawn watcher agents. task_status is for recovery or a
   result needed during another action. Treat failed/cancelled work and pending
   questions explicitly; completion without required evidence is not success.
6. Review the worker's exact committed SHA with an independent advisory subagent.
   A fresh review delegate_task is required for each round, with original brief,
   prior findings, responses and unresolved objections. Reviewer changes no files,
   commits, account state, schedules or approvals. Give it the actual branch/SHA;
   its initial checkout does not automatically contain implementation changes.
   It reports concrete failures with file locations and evidence. One repair/review
   round is the initial operator budget; escalate remaining material disagreement
   or scope changes instead of looping indefinitely. Small direct user tasks can
   opt out of review; queued delivery includes it by default.
7. Verify final SHA, required checks and base branch, then create a feature PR,
   link issue and register it using T3 link_pull_request. Record PR, branch, SHA,
   checks, skips and findings with the adapter's `delivered` receipt (In Review).
   Use watch_pull_request for requested monitoring and end the turn. Before handing
   the PR to the human, unwatch_pull_request so it returns to the inbox. Never
   enable auto-merge or merge/deploy from a delivery command.
8. After confirmed merge, verify acceptance criteria, close the issue explicitly
   when met, and set Done. Dealpipe's development base does not auto-close issues.
   Submit the smallest evidenced Brain lesson with PR/merge SHA or record
   `no durable lesson`. If transport fails, retain a sanitized private outbox.

## Cancellation, recovery and triage

Recheck commands before dispatch, repair, push and PR creation. Cancellation calls
T3 task_cancel on recorded tasks, stops only owned previews, preserves branches,
and records `cancelled`; do not delete work. Cancellation is not instantaneous:
GitHub polling latency applies. Never launch a replacement while uncertain whether
an earlier launch succeeded. Reuse clientRequestId only for retries of that exact
launch; each review/repair round gets a distinct stable ID.

For triage, deduplicate by repository plus incident/event/workflow identity before
creating an issue. Use existing project Status, not a parallel label state machine
or Linear backlog. Daily summaries are read-only unless explicitly authorized to
create/update issues. Report unavailable prerequisites; never weaken checks or use
production services to make verification pass.
