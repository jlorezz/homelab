# Shared NAS Brain

The vault is authoritative at `/storage/obsidian`; open `Brain/Start Here.md`.
Desktop Obsidian directories are synced working copies. Do not delete them until
migration, LiveSync and restore have been verified; synced deletions propagate.
The former standalone Brain archive was intentionally removed by the user.
All remaining notes are organized under `Brain`: Projects, Areas, Personal, Inbox,
System, Templates and Archive. Existing notes remain reference, historical or
draft. Agents also publish sourced project records without a routine approval
step; only actual operator approvals carry approval provenance.
Repository documentation stays in Git; project Documentation pages link to the
current branch and the pinned imported catalog. See `Brain/System/How This Brain
Works.md` for the vault handbook.

## Components

`brain.py` uses Python's standard library and SQLite FTS5. The indexer reads the
allowlisted `sources.json`, splits Markdown by heading, and stores source links,
line citations, revisions and status. Git sources are committed default-branch
snapshots; the deployed administration files are labeled separately. Indexing
replaces each source transactionally, removes deleted/revoked content, and
preserves unavailable sources with an explicit stale state.

Vault project sources use `approval_metadata` to recognize operator approval
records. Folder placement alone never elevates a note. Inbox always remains
proposed, including files claiming approval. Historical and draft sources are
indexed separately from current reference.

The MCP service opens SQLite read-only and exposes only `search`, `get_document`
and `source_status`. It listens on a Unix socket owned by the agent group, with
no TCP listener. Containers receive that socket directory read-only and their
own writable proposal directory. They never receive the vault, GitHub collector
credentials, index storage, or another worker's home. Desktop clients use an SSH
stdio bridge to the same NAS service as user `jlorezz`. Host keys must be verified.
The socket's runtime directory is preserved across service restarts so container
bind mounts keep following the same directory inode. Replacing that directory
requires recreating the affected agent containers, not just restarting MCP.

Publishing changes must be committed and integrated before activating the NAS
generation. A temporary collector/CLI rollout is overwritten by the next NixOS
activation if its source is absent from that generation. Build from the exact
committed revision, retain that source with the deployment receipt, and verify
the active collector's `ExecStart` and desktop `brain-admin` resolve to its
`brain.py` (compare SHA-256), with `submit-record` in CLI help. Check the
container's `/usr/local/lib/agent-brain.py` separately: its file bind mount can
retain the previous inode after a host helper replacement. Update/recreate it
through the normal agent rollout if it differs; a host rebuild alone is not
proof that a running container has the same helper. Finally publish a sourced
record, retry it to verify idempotency, and confirm normal search visibility.

`collect` validates proposal JSON and publishes new notes exclusively in Inbox.
`submit-record` queues source-backed project sections in a separate records
subdirectory. The collector validates their project-local Markdown targets and
appends idempotent sections under Decisions, Learnings, Runbooks or Reference.
It preserves existing content, refuses symlink destinations and does not extend
operator-approved notes. Records are indexed as reference, never fabricated
operator approval. Explicit user decisions and verified facts use this route;
uncertain recommendations stay proposals.

A separate schema-validating route publishes sanitized handoff receipts as
historical evidence. Neither route fabricates operator approval. The operator's
`approve` command creates a new note under the project's Decisions, Learnings or
Runbooks. It does not apply repository patches or authorize a PR/merge.

Merged PR descriptions, review bodies and inline findings are historical
evidence. Inline resolution is explicitly unknown. The initial backfill includes
all 352 Dealpipe merged PRs and the homelab PR as of 2026-10-16. Refreshes cover up
to 10,000 merged PRs; increase the collector envelope before reaching that count.
The index refreshes every five minutes, proposals every minute, GitHub hourly.
Collection/indexing failures appear in `source_status`; indexing success does
not imply a successful upstream refresh. Embeddings are not enabled.

## Install and activate

Run the required source doctor and unit tests first. On the NAS, copy the reviewed
`stacks/agents` directory to an administration staging directory. Use a Python
with PyYAML for the configuration/Portainer adapters; the core needs no packages.

```sh
python3 stacks/agents/knowledge/install.py --python /absolute/path/to/python3 --apply
python3 stacks/agents/knowledge/connect_agents.py --apply
```

The first command installs only knowledge files and persistent systemd units.
It does not switch the NixOS generation. NixOS's normal unit directory is
read-only, so this narrow rollout uses `/etc/systemd/system.control`, with a
managed marker and explicit boot dependencies. The matching Nix module adopts
and removes these overrides at the next normal host activation. Keep the rollout
Python and gh store paths rooted until adoption. The second command stops only
the existing agent services, reconciles their owned policy settings and updates
the stack through Portainer. Images, environment values, ports and optional
services are preserved. A source hash label forces file mounts to refresh when
the helper changes. Private backups are stored in `knowledge/deployment` and
beside changed home configs. Restart/reload existing client sessions afterward.

`access.json` is the complete reviewed MCP opt-in list. It now preserves the existing
integrations and adds `shared-brain`; Linear is disabled after the GitHub cutover. OpenCode 1.x, Hermes and
Claude Code adapters honor that knowledge opt-in. Do not use the NAS OpenCode 1.x
schema for desktop OpenCode 2.x; use its CLI to register the desktop transport.

For a desktop, supply a previously verified NAS known-host file:

```sh
python3 stacks/agents/knowledge/desktop.py install --known-hosts /path/to/verified-known-hosts --client codex --client opencode
claude mcp add --scope user --transport stdio shared-brain -- ~/.local/bin/brain-mcp
```

The generated `brain-mcp`, `brain-record`, `brain-propose`, and `brain-receipt` wrappers keep all
storage and retrieval on the NAS. Tailscale SSH may require an interactive login
check. Its host key differs from LAN OpenSSH; obtain it from the authenticated
Tailscale network map, not from an unverified scan. Desktop skills and startup
instructions must refer to the repository-owned `agent-knowledge` skill.

## GitHub credential

Homelab is public and uses unauthenticated read requests. Dealpipe uses a separate
fine-grained token selecting only `dealpipe/dealpipe`, Contents/read, Pull
requests/read and Issues/read (Metadata/read is implicit). GitHub requires the owner to create
it. On the NAS, install it without placing it in shell history or chat:

```sh
sudo /var/lib/agent-platform/bin/brain-admin install-github-token
sudo systemctl start agent-knowledge-refresh.service
```

The token file is root-owned mode 0600 at
`/var/lib/agent-platform/secrets/knowledge-dealpipe-github-token`. Only the
collector reads it. It is not reused from a coding agent's GitHub login.
Repository permission choices remain enforced by GitHub. No application CI,
branch protection, deployment, or production settings are changed.

## Verify and recover

```sh
systemctl status agent-knowledge.service
systemctl list-timers 'agent-knowledge-*'
/var/lib/agent-platform/bin/brain-admin status
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ~/.local/bin/brain-mcp
```

Check search and source freshness from each actual runtime. Submit one clearly
labeled verification proposal, ensure it appears only with `include_proposed`,
and confirm a container cannot access `/storage/obsidian`. Remove only that
verification submission/note after the check; never auto-approve it.

The SQLite index and GitHub exports can be rebuilt. Back up the vault and
proposals with the existing encrypted Restic jobs. The NAS repository is local,
not an offsite backup. Test a restore before retiring desktop originals.
For rollback, stop the knowledge units, restore the saved Portainer Compose/env
and home config backups, and reload the clients. Preserve the vault, Inbox,
credential file and receipts. No application rollback is required.

## GitHub work tracking

GitHub Issues and Projects own ongoing tasks. Dealpipe uses
<https://github.com/orgs/dealpipe/projects/1>; Homelab uses
<https://github.com/users/jlorezz/projects/3>. Existing personal GitHub projects
are preserved. `migrate_linear.py` consumes a connector export and records a
resumable UUID-to-GitHub mapping. It preserves descriptions, attributed historical
comments, original metadata and links; milestones preserve Linear workstreams.
Closed and canceled work stays closed. Native sub-issues preserve the hierarchy,
and native blocking relationships preserve dependencies. Phase names and target
dates are project fields; milestone descriptions retain the original phase
definitions. Historical comments are attributed within the imported issue body,
rather than posted as if their authors had just written them.
Linear remains untouched for provenance and is disabled in agent MCP opt-ins.
The raw export and migration mapping stay private on the NAS, not in Git.

The collector includes GitHub issue descriptions and discussion. After initial
backfill, unchanged PRs/issues skip comment fetches based on GitHub's update time.
Project boards have Active board, Backlog and History views. When creating a new
issue, explicitly add it to the appropriate project and set its status; automatic
issue enrollment is not configured. GitHub remains the source for current board
fields; indexed issue content carries its own fetch time.

## Vault file ownership

NAS note files are owned by `obsidian-bridge`, with shared group `users` and mode
0660. Vault directories are group-writable/setgid. The bridge preserves remote
modification times; group write access alone cannot set an arbitrary timestamp
on another owner's file. The root proposal publisher preserves this owner when
creating Inbox/approved notes. Operator bulk changes must do the same. A running
bridge/healthy heartbeat alone does not prove note synchronization: verify note
content in CouchDB and the desktop working copy after structural changes.

Atomic publication also creates hidden staging files group-readable from the
first filesystem event. Do not create a 0600 temporary file inside the watched
vault and chmod it later: Chokidar can fail its watch before that chmod. The
publisher temporarily uses umask 0007 with exclusive 0660 creation, restores the
caller's umask, and links completed content without overwriting an existing note.

## Source change review

`source_review.py` compares exact GitHub commit/file citations in canonical Brain
project notes against registered local mirror branches. The hourly review timer
publishes a single proposed Inbox queue and private state; it never rewrites or
approves summaries. Unchanged file blobs do not trigger review. Missing/stale
upstream state (over three hours) is reported as unavailable and fails the unit.
Coverage excludes unlinked claims, moving-branch links, history/plans and new
uncited files. See Brain/System/Source Change Review.md for human reconciliation.

All clients currently share all indexed projects, including Personal, Deal Pipe UG and
SBMS. Project filtering is not authorization. Only trusted operator development
clients may connect; new audiences require a separate enforced index boundary.
Registry, access policy, glossary and operations recovery are canonical NAS notes,
not repository mirrors: Brain/System and Brain/Projects/homelab/Runbooks.
