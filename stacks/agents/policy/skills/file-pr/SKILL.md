---
name: file-pr
description: File a concise pull request that leads with the problem. Use when asked to file, open, or create a PR.
---

Check whether the branch already has a PR. Review the diff against the PR's real
base (`main` for Homelab, `development` for Dealpipe) and confirm it contains only
the requested scope. Never stage or include unrelated work.

Follow the repository's title conventions from recent merged PRs. A title should
say why the change matters, not only what file moved. Start the description with
a plain statement of the problem the user reported, then the fix in a few lines.
Do not lead with an inventory of edits. Link the GitHub issue and any Brain
record or proposal. State how the change was verified and what remains
unverified. Include council or review-loop results when they ran.

End the description with the harness attribution the system prompt supplies; if
none is supplied, name the verified public model and harness, never a guess.
Check title, body, quotes and attachments for credentials, customer data and
private infrastructure details before submitting.

Open a real PR, not a draft, so CI and review bots run. If `gh pr create` fails
on the GraphQL rate limit, use
`gh api repos/<owner>/<repo>/pulls -f title=... -f head=... -f base=... -F body=@<file>`.
In T3, link the PR to the thread. Continue with `babysit-pr` only when monitoring
was requested. Post follow-up comments only when useful, using
`leaving-pr-comments`. Filing a PR never authorizes a merge.
