---
name: council
description: Get independent read-only review from three models on three providers. Use when the user says "council".
---

Use three delegates, one per provider family, at high reasoning effort. Use the
council named by the user, otherwise the council in the shared policy. Resolve
current models in T3 with `orchestrator_capabilities` and start all three with
parallel `delegate_task` calls (`role: "review"`); never hardcode IDs. Outside
T3, use the harness's subagents. If a member or provider is unavailable, report
it and continue only if the user accepts a smaller council. Never substitute.

Give each delegate the same self-contained brief: repository path, base SHA,
diff command, intent, out-of-scope items, and check results. Delegates are
read-only: no edits, commits, pushes, comments or subagents. Ask for structured
findings (`id [blocker|should|nit] file:line claim, evidence, suggested fix`) and
the checks each could not run. Do not ask for scores.

Verify every finding yourself. Summarize agreements, disagreements, rejected
findings with reasons, and missing checks. Fix agreed blockers and shoulds with
the smallest change, rerun checks, then start a fresh council round with the
ledger of `fixed`/`disagree`/`declined` items. Stop after three rounds, or
earlier when no blocker or should remains open; report remaining disputes for
the user. Fight scope creep; list nits as follow-ups.

Council output is advisory. It never replaces repository checks, CI, human
review, or merge and deployment authorization. When the work has an open PR,
add a `## Council review` section to its description (not a comment) listing
rounds, members, and per-round outcome (open blocker/should counts); otherwise
put it in the final reply. Record usage when reported, otherwise unknown.
