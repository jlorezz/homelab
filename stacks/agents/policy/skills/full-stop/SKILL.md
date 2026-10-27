---
name: full-stop
description: Take a change unattended to a proven, reviewed PR that is ready to merge, then stop without merging. Use when the user says "full stop" or "full send".
---

The user is not watching. Own the work from its current state to a PR that is
ready for them to merge. Use this only after an explicit "full stop" or "full
send" request. "Full send" receives this same bounded workflow: on the NAS,
agents never merge under an unattended request.

**Granted:** commit and push to this task's branch, rebase it, and update it with
`--force-with-lease`. Run the tests proof needs, including the app, browser
automation and previews, within repository instructions, `agent-workload` and
the shared policy. **Not granted:** merging, auto-merge, merge queues, pushing to
the default branch, bypassing protection, closing PRs, production or customer
data, deployments, deleting data, or changes outside the repository. If
auto-merge is enabled, disable it before work and report that.

1. **Finish** the smallest change that achieves the goal. Branch first when on
   the default branch; respect the coordinator's worktree ownership.
2. **Prove** every change with evidence you observed: reproduce the failure and
   watch the same reproduction pass, run the repository's CI checks, add one
   focused failing-first test when the behavior has none, and look at UI or
   runtime behavior directly. Mocked green tests do not prove user experience.
3. **Review** with `review-loop`, or `council` when the user asked for it.
4. **File** with `file-pr` after rebasing onto the fresh base; add one line saying
   how the change was proven.
5. **Babysit** with `babysit-pr`. Re-prove each fix before pushing it. Stay active
   until the gate resolves; leaving pending checks ends the work.
6. **Stop at the gate.** Fetch and re-read the live PR: open, not draft, head
   unchanged, proof covering the final diff, required checks green, bots finished
   with findings fixed or answered, no human change request, branch current and
   mergeable. Call `unwatch_pull_request`, then hand off.

Verdicts: **ready to merge**, **waiting for required human approval** (confirmed
as the only blocker), or **blocked** (what blocks, what was tried, what is
needed). Ask only when you cannot prove the change, cannot pass the gate, need an
action outside the grant, face ambiguity that changes the result, or find the PR
obsolete. Ask with the harness question tool (`AskUserQuestion`,
`request_user_input`) including the PR link and options with a recommendation;
without one, start the final message with the question. Decide everything else
with sensible defaults.

Report the PR link, final head SHA, verdict, proof, reviewer outcome, decisions
made, remaining risks, and that the PR is open and unmerged.
