# Supabase recovery and sanitized NAS testing

This operator flow keeps hosted Dealpipe production, encrypted recovery data and
sanitized testing separate. The NAS testing stack and synthetic export/restore,
privacy, Auth, tenant-isolation, file-placeholder and Realtime rehearsals are
verified. Production export credentials still require private installation;
production timers must remain disabled until real B2 recovery receipts pass.

The application-specific policy and synthetic personas belong in the **private**
Dealpipe repository at `ops/recovery/`. Deploy them as root-owned 0600 files under
`/var/lib/dealpipe-recovery/`, outside the public Homelab source, Nix store, agent
homes and shared-search collections. The public tests use a small synthetic
policy; they contain no production schema inventory or customer rows.

## Recovery data path

`backup.py` uses a dedicated non-superuser `BYPASSRLS` backup reader and a single
read-only repeatable-read snapshot. Native `pg_dump` is used because Supabase CLI
exports switch to the postgres role and cannot use that reader. The archive
contains reviewed application/Auth/Storage/migration/Vault schemas, extension
metadata and roles without role passwords. PostgreSQL large objects or unexpected
schemas require an explicit coverage review rather than silent omission.

Storage object bytes are downloaded separately. Object versions, metadata and
update times must match a fresh inventory after transfer. Changing/deleted
objects abort that run; no partial archive becomes a success receipt. Unchanged
versioned objects are reused from an AES-256-GCM encrypted NAS cache, whose names
are keyed hashes. Cache corruption causes a fresh download. The cache key is not
a recovery dependency: the independent Restic repository has its own password.

Restic encrypts the database, bucket configuration, checksummed file bytes and
optional `operator-recovery/` material into the private EU production B2 bucket.
The Homelab credential/bucket/password is never reused. The current bucket keeps
all versions and has Object Lock disabled. Verified immutable deletion protection
is not claimed. There is no automatic remote destructive prune; reviewed operator
maintenance targets 30 daily, 12 weekly and 12 monthly snapshots.

Raw workspaces are private `noswap` tmpfs mounts. Raw quarantine PostgreSQL data
uses a different RAM mount and an isolated Docker network. Raw-data jobs and the
quarantine container disable swap and core dumps. The current workspace budget
is 16 GiB, quarantine storage 2 GiB; these are explicit operator capacity budgets,
not streaming or unlimited-storage promises. Size growth must be reviewed.

## Sanitized testing

`refresh.py` restores and verifies a completed recovery archive, imports it into
an isolated `quarantine_*` database and runs the private policy transactionally.
Table/column/type/enum drift fails closed. Identifiers are consistently rekeyed;
contacts, names, addresses, text, paths, numbers and dates are replaced. JSON,
arrays, credentials, sessions, MFA, provider connections, transcripts and outbound
queues are cleared according to the policy. Constraints remain enabled; foreign
keys are explicitly checked and dependents of intentionally omitted parents are
removed. Source encryption keys and original uploaded bytes are never published.

Only sanitized data enters the migration-built testing database. Production SQL
functions/defaults/grants are never applied there. `baseline.py` seals definitions
and privileges from the reviewed migration build. Preserve Dealpipe's Data API
deny-by-default model and its minimal Realtime projection grants; do not add
blanket grants to make a check pass. The custom access-token hook is enabled only
after migrations are sealed. Auth identities need valid timestamps, even though
the schema permits NULLs.

Testing runs Postgres 17, Auth, PostgREST, Storage with image transforms, Realtime, Kong and captured
Mailpit email, using pinned image digests. Its Docker network has no external
gateway. Loopback proxies provide host ingress without adding outbound routing.
Generated PDFs/images/text replace files; unsupported media formats use a generic
placeholder and do not reproduce original media playback. This is a sanitized/
pseudonymized dataset, not a legal claim of irreversible anonymization or a
substitute for controlled financial-calculation fixtures.

Every refresh adds synthetic tenant A/B and admin personas from the private
application SQL. OTP email goes only to local Mailpit. `smoke.py` verifies actual
logins, positive permitted tenant reads, cross-tenant isolation, application-column
and anonymous denial. `realtime_smoke.py` verifies an actual own-tenant change
and absence of cross-tenant events/application columns. Success receipts must
match the latest dataset; empty denied responses alone do not pass readiness.

## Private credential installation and activation

Run the hidden-prompt installer from the operator's SSH terminal:

```sh
ssh -t root@100.125.180.36 /var/lib/dealpipe-recovery/bin/provision
```

It needs the production direct/session database URL on **5432**, a Storage server
credential and a separate B2 read/write key restricted to
`qnoxslab-dealpipe-production-backups` and prefix `production/`. It verifies the
project and TLS with `verify-full`, creates a dedicated SCRAM backup reader and
discards the admin connection. The server Storage credential can be privileged;
this process performs download operations only. None of these values belong in
chat, Git, receipts, CI, notes or coding-agent containers.

Provisioning failures report the failing stage and only allowlisted HTTP/SQLSTATE
codes. The private `provision-status.json` contains the same safe diagnostics;
provider response bodies, SQL exception text and credential values are excluded.
Bucket restrictions are checked against the ID from the operator's bucket
screenshot; a missing bucket name does not require granting list-all-bucket-names.
Check whether credentials and the backup-reader role exist before retrying a
partially completed installation. Never overwrite an existing reader blindly.

From the separate trusted operator machine, `escrow_password.py` preserves the
new Restic password bytes in a private independent file and records its checksum
on the NAS. Import that copy into an independently accessible password manager/
offline recovery kit. Initialization refuses to proceed without that receipt.

Using the Nix-built Python runtime, the remaining operator sequence is:

```sh
/var/lib/dealpipe-recovery/bin/recovery init
/var/lib/dealpipe-recovery/bin/operator prepare-production
systemctl start dealpipe-production-backup.service
systemctl start dealpipe-production-check.service
systemctl start dealpipe-production-restore-verify.service
systemctl start dealpipe-production-refresh.service
/var/lib/dealpipe-recovery/bin/operator health
/var/lib/dealpipe-recovery/bin/operator activate-production
```

The entrypoints use the pinned Python environment with psycopg, PyYAML and
cryptography. Operator jobs run through the shared
NAS workload guard. `--prepare-production` installs manual jobs but enables no
timers. Activation requires fresh real backup, integrity, artifact restore,
production-derived sanitization and matching API/Realtime receipts. Set
`qnoxslab.dealpipeRecovery.enableProduction = true` only after this verification.
The narrow marked systemd rollout is adopted by a later normal Nix activation;
this setup does not require a full host switch.

Daily export is 02:00 with up to 15 minutes jitter; integrity checks Sunday 04:30;
full artifact checksum restoration first-of-month 06:00; sanitized refresh Sunday
06:30, Europe/Berlin. A refresh replaces disposable testing data: coordinate with
active tests. The NAS health probe rejects missing/stale receipts or newer
failures. Actual Beszel notification delivery remains a separate verification.
Daily export alone has roughly a 24-hour recovery-point target, not PITR.

## Developer and agent access

Use a dedicated checkout and the private `client_env.py` output. Keep production
environments and provider/encryption keys out of it. The desktop tunnel is:

```sh
ssh -N -L 18785:127.0.0.1:18785 -L 18786:127.0.0.1:18786 \
  -L 18788:127.0.0.1:18788 root@100.125.180.36
```

API is `http://127.0.0.1:18785`; Postgres 18786; Mailpit 18788. Existing laptop
8785/8786/8788 services remain separate. Synthetic login emails are
`tenant-a@testing.example.invalid`, `tenant-b@testing.example.invalid` and
`admin@testing.example.invalid`; use captured verification emails, not production
passwords. Generate environment files privately; never paste their contents.

After readiness, `agent_access.py --apply` connects trusted T3/Hermes workers only
to the testing network and writes UID-owned 0600 **opt-in** backend environments
at `/workspace/.dealpipe-testing.env`. It grants no raw recovery mounts/network,
NAS SSH key, production token or Docker socket. Internal URLs suit backend tests;
desktop browser previews use the tunnel configuration. Existing environment files,
agent provider settings and CI databases are not overwritten or automatically
retargeted. Worktree isolation and explicit refresh/reset authorization still
follow the application instructions. Do not run the laptop reset script on NAS.

## Real disaster recovery

Use a separate machine, independently retained repository password and owner/
recovery credentials. Verify checksums and import into a fresh isolated Supabase
instance before activating services. Restore file bytes through the Storage API,
not only database metadata. Reconfigure original or rotated JWT/OAuth/SMTP/domain
settings and provider credentials; never test recovery over production.

`store_key.py` privately escrows an **existing** production `TOKEN_ENCRYPTION_KEY`
under `operator-recovery/`, which is included in the next encrypted archive and
excluded from testing. Do not generate a replacement as a backup step. Vercel
Production/Preview Secrets may be unreadable after saving, so an env pull is not
proof of key escrow. Missing keys require provider reconnection/rotation during
recovery. Supabase Vault ciphertext alone similarly does not prove independent
secret recovery. The initial inspected project has no Vault or provider-connection
records, but future use requires a separate secret-recovery review.

The automated artifact/quarantine checks and sanitized backend tests do not prove
a full production application rebuild or measured recovery time. Managed Supabase
backups remain another layer; no paid PITR, hosted staging change or production
promotion is activated by this flow. Restore retired notes only when explicitly
requested; historical recovery does not reverse intentional deletions.

Sources checked 2026-10-17: [dedicated backup reader](https://supabase.com/docs/guides/troubleshooting/pg_dump-fails-with-query-would-be-affected-by-row-level-security-policy-1d5783),
[platform-to-self-hosted restore](https://supabase.com/docs/guides/self-hosting/restore-from-platform),
[B2 scope response](https://www.backblaze.com/apidocs/b2-authorize-account),
[Vercel secret behavior](https://vercel.com/docs/environment-variables/sensitive-environment-variables).
