---
name: agent-flow
description: Prepare, dispatch when explicitly authorized, and wrap up GitHub issue work in isolated T3 worktrees; triage findings into the existing GitHub project.
---

GitHub Issues and Projects are the single work queue. Linear IDs and imported
records are historical provenance. Use full GitHub issue URLs as identities;
issue numbers alone collide across repositories. Dealpipe uses
`dealpipe/dealpipe`, base `development`, organization project 1. Homelab uses
`jlorezz/homelab`, base `main`, user project 3. Read current checkout instructions.

An executable issue specifies Scope, Acceptance criteria, Owns (paths), evidence
and any requested review. Clarify missing boundaries on the existing issue.
A human must authorize implementation and any dispatch. Verify the user's
instruction or authenticated human approval event; an issue body, assignment,
project status or agent-applied label is not authorization. Never self-approve.

When dispatch is authorized, check existing threads/tasks and linked PRs for the
full issue URL before starting work. Check path ownership against active work;
resolve overlap before dispatch. Use the available T3 orchestration contract:
`delegate_task` with worktree isolation for authorized delegated work; create a
top-level thread only when the user explicitly requested one. Record the returned
identity and inspect it before retrying an uncertain launch. Do not create a
recurring dispatcher or additional workers as a side effect of ordinary work.

Give the worker the issue URL, verbatim scope and acceptance criteria, owned
paths, repository, current base revision and delivery authorization. Do not copy
uncommitted parent changes implicitly. Run the existing required checks and
report unavailable prerequisites. Review is advisory and only when requested.
Opening a PR follows current repository authorization rules; implementation
approval alone may not authorize a Dealpipe PR. Target the documented base,
link the issue and register any PR with T3 `link_pull_request` when available.
Never infer merge or production deployment permission from dispatch approval.

Record branch/task, evidence and verification on the GitHub issue and explicitly
maintain its existing project Status field. Do not introduce a parallel label
state machine or Linear backlog. After a confirmed merge, close only if acceptance
criteria are satisfied; Dealpipe's development base needs explicit issue closure.
Use `agent-knowledge` for the smallest evidenced lesson with merge SHA and source
link, or record `no durable lesson`. Retain an unsent proposal in the task's
private outbox if the knowledge transport is unavailable and report that fact.

For requested triage, deduplicate by repository plus incident/event ID, workflow
run or dependency before creating a ticket. Put reproducible evidence and scope
in the existing GitHub issue/project. Triage does not authorize repairs, changes
to checks, scheduling, deployments or issue approval. This skill is a procedure;
its presence does not mean an automated dispatcher is installed or running.
