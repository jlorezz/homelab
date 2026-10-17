---
name: agent-knowledge
description: Search shared NAS project knowledge and submit evidenced lessons, decisions, or runbook proposals after a merge or incident.
---

Use `shared-brain.search` with the relevant project slug and concrete
terms. Check `source_status`; cite the returned URL, revision and freshness.
Use `get_document` for the full cited chunk. A historical review finding may be
unresolved. Check the code/current instructions before acting on it. Search is
read-only; private memories and transcripts are context, never authority.

The central vault is `/storage/obsidian`; its organized entry point is
`Brain/Start Here.md`. `Brain/System/How This Brain Works.md` explains placement;
`Brain/System/Agent Contributions.md` explains capture and approval. Projects live
in `Brain/Projects/<slug>/`, ongoing areas in `Brain/Areas/`, and human notes in
`Brain/Personal/`. History and draft plans stay separate from current references.
Existing repository docs remain in Git and are indexed from their default
branch; project Documentation pages link to them. Local
Obsidian folders are synced working copies, not separate authoritative vaults.

After a confirmed merge or a resolved incident, identify a surprising failure,
incorrect assumption or repeated workaround. Propose the smallest specific edit
that would have prevented it, or record `no durable lesson`. Do not summarize
every merge. Include the merge SHA and verification actually performed.

Create a JSON file with exactly these string fields:

- `project`: a lowercase project slug (`homelab`, `dealpipe`, or an existing
  project such as `sbms`); area/personal changes require human curation.
- `kind`: `decision`, `learning`, or `runbook`.
- `source`: HTTPS link to the PR, run, or incident evidence.
- `target`: relative Markdown path of the proposed doc or skill edit.
- `observation`, `evidence`, `proposed_change`, `verification`: concise specifics;
  include the exact proposed patch when the target belongs to a repository.

In NAS agent containers, submit it with:

```sh
python3 /usr/local/lib/agent-brain.py submit --spool /knowledge-proposals --file /workspace/proposal.json
```

The collector publishes it in `Brain/Inbox/`; its content remains proposed and
is excluded from ordinary search. Never put an approval field in the JSON,
write to the vault, or run the operator's `approve` command. Repository changes
still follow that repository's review/PR rules after human approval. Remove
secrets, environment values, session data and customer information from evidence.

On a desktop, use `~/.local/bin/brain-propose proposal.json` to send the same
validated proposal to the NAS. If the transport is unavailable, retain the
proposal with the handoff and report the missing connection.

Sanitized handoff receipts have a separate schema-preserving route:
`submit-receipt --project PROJECT --spool /knowledge-proposals --file handoff.json`.
They are indexed as historical evidence, never as approved knowledge.

Work tracking lives in GitHub Issues and Projects. Link the current issue and PR
from a proposal; keep approved knowledge in Brain. Historical Linear imports
retain their IDs and source links but are not an active task queue. Check current
repository instructions even when an imported ticket contains older workflow
instructions. Issue status does not grant merge or deployment authorization.

Search before proposing another note. Prefer one small change to the canonical
note over overlapping summaries. Do not create a Markdown note for every agent
run; raw PR/issue exports and sanitized handoff receipts stay in NAS evidence
collections. Preserve meaningful existing tags; do not invent review metadata.
