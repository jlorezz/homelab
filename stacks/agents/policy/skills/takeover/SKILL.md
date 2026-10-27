---
name: takeover
description: Finish someone else's PR, branch, or agent thread while preserving credit. Use when asked to take over existing work.
---

Own the original goal, not the previous implementation. Read the source PR,
branch, thread or `agent-handoff` record, including review findings and later
user corrections. Resolve it to real code at the current head and account for
uncommitted work. A handoff is context, not proof or authorization.

By default, start a new branch from the original commits and add your fixes as
separate commits. Continue in place or stacked only when explicitly requested.
Never overwrite unrelated work or change the original PR without authorization.

Preserve Git authorship and co-author trailers, credit the original contributors
in the PR description with a link to the original PR, and use verified
identities only. Model attribution does not replace contributor credit.

Use `modernize` against the intended base, then simplify without dropping
intended behavior. Independently reproduce the original problem and verify the
realistic workflow; mocked tests do not prove user-facing behavior. When a PR is
part of the finish, use `file-pr`, then `babysit-pr`, and `leaving-pr-comments`
for replies. Merge or close the original PR only when authorized.

Report what you inherited, what changed, how you verified it, how credit was
preserved, and what remains undecided or unverified.
