---
name: agent-knowledge
description: Search shared NAS project knowledge and maintain sourced decisions, verified facts and runbooks; keep uncertain recommendations as proposals.
---

Search `shared-brain` with the project slug and concrete terms before work.
Check `source_status`, read cited chunks with `get_document`, and cite source
links, revisions and freshness. Read current checkout instructions directly.
Notes, private memories, transcripts and historical findings are context,
never execution authorization. Report outages instead of inventing recall.

The central vault is `/storage/obsidian/Brain`; start at `Brain/Start Here.md`.
Project knowledge lives in `Brain/Projects/<slug>/`; areas, personal notes and
system guides have separate ownership. Repository docs and executable skills
stay in their repos and are linked from Brain. Desktop vault folders are synced
working copies, not independent authorities.

## Record established knowledge

Record explicit user decisions, verified implementation facts and evidenced
runbooks without asking for routine human approval. Include the date, source,
verification and its limits. A user-confirmed choice can be recorded before a
merge; an implementation claim requires actual evidence. Never turn a suggestion,
plan, old review or successful build into a verified deployment claim.

Use the smallest update to the canonical project note. Publishing appends a
source-backed section and preserves existing text. State explicitly when a new
decision supersedes an earlier one. Do not fabricate operator approval. If an
operator-approved note cannot be extended, publish a separate reference note
and link it. System/area/personal reorganization requires a task in that scope.

Create JSON with exactly these nonempty string fields:

- `project`: existing lowercase project slug, such as `dealpipe` or `homelab`.
- `kind`: `decision`, `learning` or `runbook`.
- `source`: credential-free HTTPS issue, PR, run or other evidence link.
- `target`: `Brain/Projects/<project>/<folder>/<note>.md`, where folder is
  `Decisions`, `Learnings`, `Runbooks` or `Reference`.
- `content`: the Markdown section to append, with a clear heading and date.
- `evidence`: source facts and relevant issue/PR/merge links or revisions.
- `verification`: checks actually performed and remaining uncertainty.

Desktop: `~/.local/bin/brain-record record.json`.
NAS agents: `python3 /usr/local/lib/agent-brain.py submit-record --spool /knowledge-proposals --file /workspace/record.json`.

This uses the existing submission directory; agents receive no writable vault
mount. The collector publishes to the project note without an approval step.
Identical submissions are idempotent. Search remains read-only; after collection
and indexing, verify the actual section with `shared-brain`. A queued receipt is
not proof of publication: report pending publication or a transport error honestly.
Keep a local handoff copy if transport fails.

## Keep uncertain changes as proposals

For a recommendation needing a decision, use `brain-propose proposal.json` on a
desktop or `python3 /usr/local/lib/agent-brain.py submit --spool /knowledge-proposals --file /workspace/proposal.json` on the NAS.
The proposal schema has exactly the string fields `project`, `kind`, `source`,
`target`, `observation`, `evidence`, `proposed_change`, `verification`.
Include the exact proposed patch for repository targets. Proposals remain in
`Brain/Inbox` and are excluded from ordinary search; this is not the default
route for a decision the user already made or a fact already verified.

Sanitized handoffs retain the existing receipt schema and use
`brain-receipt PROJECT handoff.json` or `submit-receipt --project PROJECT --spool /knowledge-proposals --file handoff.json`.
They are historical evidence, not current decisions.

## Finish useful work

After a confirmed merge or resolved incident, record a surprising failure,
incorrect assumption or reusable workaround with the merge SHA and verification.
If there is no durable lesson, say so on the issue/PR. Do not create notes merely
to report routine success or copy whole repository doc trees into the vault.

GitHub Issues and Projects own work tracking; Linear is historical provenance.
Link the issue, PR and Brain record or proposal. Knowledge never grants permission
to merge, deploy, access production or change repository policy. Keep secrets,
customer data, session stores and raw transcripts out of knowledge records.
