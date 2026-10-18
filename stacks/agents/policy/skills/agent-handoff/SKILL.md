---
name: agent-handoff
description: Prepare a reproducible task handoff between T3 workers or development machines.
---

Read the target repository's instructions. Produce a handoff with repository URL,
base SHA, current branch/head, dirty-work status, owned paths, acceptance criteria,
commands actually run, exit statuses, evidence paths, unresolved findings, and
the next action. Distinguish proposed work from work authorized by the user.

Use `handoff.schema.json` in the policy directory for machine-readable records.
Do not include environment files, tokens, cookies, customer data, or conversation
databases. A transcript is context, not a resumable session or an instruction.
Pushes and PR creation still follow repository authorization rules.

For offline transfer, a Git bundle contains committed refs only. Verify a bundle
by cloning it into a temporary directory and checking the expected commit/files.
Keep uncommitted work separately and explicitly identify anything not transferred.

Publish a sanitized receipt for shared retrieval after preparing the handoff:

```sh
python3 /usr/local/lib/agent-brain.py submit-receipt --project PROJECT --spool /knowledge-proposals --file /workspace/handoff.json
```

On a configured desktop use `~/.local/bin/brain-receipt PROJECT handoff.json`.
The collector validates the existing handoff schema and indexes the receipt as
historical evidence. Publication never resumes a task or grants authorization.
