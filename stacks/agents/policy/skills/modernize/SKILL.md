---
name: modernize
description: Bring an old PR up to date and decide whether it should still exist. Use when asked to rebase an old PR or "modernize".
---

Modernize the intent, not just the branch. A conflict-free rebase does not prove
a PR still belongs. This authorizes rebasing and updating the PR branch only:
never merging, closing, changing its base, or expanding scope.

Fetch the PR head and actual base. Treat dirty worktrees as user-owned and work
in a disposable worktree when needed. Record the PR URL, state, base, head SHA,
author, checks and reviews. Read the body, linked issues, commits, review threads
and full diff, and state the smallest goal it tried to achieve.

Inspect what landed on the base since the merge base, and search merged and open
PRs for replacements. Ask whether the problem is already solved, whether APIs,
schemas, dependencies or ownership changed, whether the touched code was removed
or simplified, and whether landing it would restore old behavior or create a
second way to do the same thing.

Choose one disposition before pushing:

- **MODERNIZE**: goal and shape still right. Rebase onto the fresh base, resolve
  conflicts by current invariants, compare with `git range-diff`, run focused
  checks through `agent-workload`, and push with `--force-with-lease` against
  the head observed before the rebase. If the lease fails, refetch and reassess.
- **REWORK**: goal still useful, implementation wrong. Describe the smallest
  credible rewrite and stop unless that rework was requested.
- **CLOSE**: superseded, obsolete or not worth its cost. Cite evidence and stop;
  never close without explicit permission. Age alone is not evidence.

Lead the report with the disposition and reason, then base changes, conceptual
conflicts, rebase/push result, checks, and the remaining decision with the PR
link. Comment on the PR only when asked, using `leaving-pr-comments`.
