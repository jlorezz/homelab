# Independent offsite recovery

Backblaze B2 in an EU region is the selected destination. The private bucket `qnoxslab-homelab-backups` and endpoint
`s3.eu-central-003.backblazeb2.com` are configured in `homelab.json`. Activation
is pending scoped credentials and a recovery-password copy outside the NAS. There is no active B2 upload or production DB job yet.
`qnoxslab.offsiteBackup.enable` defaults to false. No existing NAS or application
deployment is changed by adding these definitions.

## Homelab data and consistency

The offsite service copies the latest completed NAS and agent Restic snapshots
from `/storage/backups/nas-restic` into a separately initialized and encrypted
repository. It does not copy a live repository directory or dump running app
files a second time. The local NAS backup owns its application/database staging;
the agent backup owns online-consistent SQLite staging. Both groups must have a
successful snapshot within 36 hours (daily cadence plus a 12-hour retry window).

NAS snapshots include the vault, documents, photos, NAS definition tree,
application/database staging and configured secrets. Agent snapshots include
staged homes, policy, sanitized knowledge evidence and consistent SQLite copies.
These are private recovery data, never MCP content or agent-container mounts.
Local and offsite repository passwords differ. First activation copies only the
latest successful snapshot of each required group; local historical snapshots
are not automatically backfilled. Subsequent daily copies accumulate history
independently and never mirror local deletions.

Observed 2026-10-17 source sizes: NAS snapshot 167,361,054 bytes; agents snapshot
644,239,808 bytes, about 0.81 GB combined logical file content. These are source
sizes, not an upload/storage invoice. Compression, deduplication, history and B2
prior versions determine billed storage. Production size has not been measured.

## Account and credentials

Create a private Homelab bucket in an EU B2 account/region. Record the actual S3
endpoint from that bucket, not an inferred region. Use a separate private bucket
and credentials for Dealpipe production; do not mix recovery passwords or keys.
Protect the account with MFA and retain owner/recovery access outside the NAS.

The daily key is restricted to the Homelab bucket/prefix with listing, read and
write access. Do not grant bucket administration or permanent version deletion
(`deleteFiles`). S3 deletion by name uses `writeFiles`, while deletion of a
specific version requires `deleteFiles`. Verify these rights with expendable
objects before production use. Keep previous object versions for 90 days through
an owner-managed lifecycle rule; never automatically hide current repository
objects based on age. This rollback window is not immutable/WORM retention.

Restic lock deletion and a restore must pass with the restricted key. Test that
permanent version deletion is denied. A separate maintenance key remains with
the operator outside the NAS. Do not enable blanket Object Lock on the Restic
bucket without a separate tested lock/prune design. The current setup does not
claim verified B2 deletion protection until these cloud tests run.

Install credentials in an interactive NAS terminal, with hidden prompts:

```sh
sudo python3 /etc/nixos/homelab/stacks/backups/offsite.py install-credentials
```

This creates root-owned 0600 `homelab.env` and a random `homelab-password` under
`/var/lib/offsite-backup/secrets`. It never prints the values. Save the generated
repository password privately in an independently accessible password manager
before initialization. Losing the NAS must not also lose the only decryption
key. Do not paste credentials into chat, Git, notes, arguments or agent homes.

## Concrete activation

1. Review the confirmed bucket/endpoint in `homelab.json`; the generic
   `homelab.example.json` is only a template. Retain `/homelab` as the repository
   prefix. Copy the reviewed config to the
   root-owned `/var/lib/offsite-backup/config.json`.
2. Review `offsite.py plan --config ...` and confirm both source groups are fresh.
   If needed, run the existing local backup services first. Local backup staging
   remains owned by those services; a successful offsite copy cannot fix a failed
   local backup.
3. Load the 0600 environment through an operator-owned systemd unit, never by
   echoing its values. Run `init` once, then `copy`, `check`, `restore-test` and
   `health`. Use the shared workload guard for copy/check/restore.
4. Confirm bucket scope, version retention and denied permanent deletion against
   disposable test objects. Check actual uploaded bytes and set account spending
   alerts. Only then activate the matching Nix service/timer definitions, either
   in a narrow reviewed rollout or a separately authorized NixOS activation.
5. Verify the NAS health probe receives `OFFSITE_BACKUP_STATUS`. Missing/stale
   daily copy, weekly check, monthly restore or a newer failure must fail health.
   Beszel's existing systemd health view receives that failed probe; verify its
   actual notification delivery before relying on an alert.

The Nix module is imported by `backups.nix`; enabling requires the real
`qnoxslab.offsiteBackup.repository` and private environment file. Daily copy is
05:30 with up to 15 minutes jitter, after the local scheduled backups; checks run
Sunday 07:00 and a sample restore on the first of each month at 08:00, NAS time.
The services use the same NAS workload lock, preserving local/CI admission.
Checks read a random 5% data sample plus repository metadata. The restore writes
one Brain navigation note to a private temporary directory, compares its bytes
with the corresponding local snapshot, then removes that temporary directory.
These checks demonstrate sampled recovery, not a full disaster recovery test.

## Retention and recovery

Target snapshot retention is 30 daily, 12 weekly and 12 monthly. Run reviewed
`restic forget --keep-daily 30 --keep-weekly 12 --keep-monthly 12 --prune` from a
separate operator environment using the offsite repository password and the
maintenance credential. No automatic destructive pruning is enabled on the NAS.
Account lifecycle retention of prior object versions is separate from Restic
snapshot retention and can increase storage cost.

On NAS loss, use a separate machine, owner/read recovery access and the offsite
password. Inspect snapshots, run `restic check`, and restore to a new private
location. Verify note hashes, database staging and SQLite integrity before
publishing a restored vault or activating services. Recover secrets carefully;
restore to current identities rather than overwriting a live host. A local
repository or snapshot ID is not needed once the offsite copy is independently
readable. Test a full selected NAS/agent restore before decommissioning local
recovery. Historical backup recovery does not authorize restoring notes the
operator intentionally retired into the live vault.

## Dealpipe production boundary

Production backup implementation and activation remain separate from Homelab
recovery. B2 is storage, not a production database server or a replacement for
Supabase recovery. The design is:

- A private `dealpipe-production` repository/bucket, separate key and recovery
  password, and an operator-owned export job outside development agents.
- Consistent logical database exports covering the application and the required
  Auth/role/schema/extension restoration paths, using the current Supabase export
  guidance. Inspect exact coverage; do not assume an app-only dump recreates a
  project. Do not create backup roles or read production customer data as a side
  effect of Homelab setup.
- A separate uploaded-file backup. Supabase database backups contain Storage
  metadata, not object bytes. Inventory actual providers (including any files
  held outside Supabase) and define the permitted recovery scope before copying.
- Existing Supabase daily backups, where the current plan includes them. PITR is
  a separate Supabase purchase/decision based on the accepted recovery point;
  B2 fees do not include PITR.
- A restore to an explicitly isolated recovery environment, with tenant/Auth
  checks, file restoration, consistency checks and a measured recovery time.
  Never restore over production to test a backup.

Pending production inputs: verified project/provider inventory, approved export
credential/runner, acceptable data-loss window and recovery-time target, account
and bucket, allowed file scope, and actual restore proof. No production job or
paid add-on is activated by this design document.

Sources checked 2026-10-17:
[Restic repository copy](https://restic.readthedocs.io/en/stable/045_working_with_repos.html),
[B2 pricing](https://www.backblaze.com/cloud-storage/pricing),
[B2 application capabilities](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys),
[B2 lifecycle rules](https://www.backblaze.com/docs/en/cloud-storage-lifecycle-rules),
[Supabase backup coverage](https://supabase.com/docs/guides/platform/backups).
