---
name: review-loop
description: Review a change with one cross-provider delegate in a bounded loop until both agree. Use when the user says "review loop" or "sol loop".
---

Use this before filing a PR, or on an open PR, to make a change more correct with
the smallest fixes. The reviewer is advisory: it finds problems; you decide,
fix and verify. Its verdict never replaces repository checks or merge approval.

**Reviewer.** Default to the `review` profile's provider family from
`coordinator.json` model routing, at high effort. "sol loop" means the latest
GPT Sol through Codex. Honor a model the user names. In T3, resolve the model with
`orchestrator_capabilities`; never hardcode an ID or substitute another model.
If no route can run it, report the blocker.

**Budget.** Three rounds by default; up to five only for security,
authorization, tenant isolation, data loss, billing or concurrency findings with
open evidence, or when the user asks. Stop early when a round returns only nits.
One reviewer; it starts no subagents. Keep a ledger of id, severity, outcome and
usage (unknown when not reported).

**Scope.** Run `git status --short`; do not stash, discard or include unrelated
work. Fetch and set the base with `git merge-base HEAD origin/<base>`. Use one
diff command every round. Run the covering checks through `agent-workload` and
note missing or pre-existing failures. Do not edit scoped files while a round runs.

**Each round** is a fresh `delegate_task` (`role: "review"`, new
`clientRequestId`), never `t3_thread_send`. Outside T3, use a native subagent on
the chosen model. Wait for the completion notice without polling. Timeout, error
or silence is not approval. The round 1 brief:

```
Review only. Do not edit, commit, push, comment, or start subagents.
Repo: <path> Base: <sha> Intent: <1-2 sentences> Out of scope: <...> Checks: <results>
Diff: git diff <base> [-- <paths>]; read untracked files from git status --short.
Find bugs, broken callers and mismatches with intent. Flag code the intent does
not need. Style, rare paths, minor perf and extra compatibility are nits.
blocker = intended case wrong; should = real bug callers will hit; nit = rest.
Reply: VERDICT: APPROVE|CHANGES, then lines "R1 [blocker|should|nit] file:line claim. Fix: ..."
```

Later rounds add the ledger (`fixed`/`disagree`/`declined` with evidence) and the
change since the last round, and ask only about open ids and changed code.

**Answering.** Re-judge every severity yourself. Agree: smallest fix, preferably
deleting or narrowing code, plus one focused failing-first test when needed.
Disagree: cite file:line, test or output. Nits and out-of-scope items: decline in
one line and list as follow-ups. If review grew the diff, cut first.

**Stop.** Done when the reviewer approves the current diff, no blocker/should is
open, and you have no concern. If an item stays disputed after two replies or
budget runs out, show both sides briefly and let the user decide. Push only if
asked. When updating a PR, add `Reviewed with review-loop: <N> rounds with
<model>; verdict <approved|disputed>.` Report rounds, model, fixes, declines,
diff size before and after, usage if known, and whether anything was pushed.
