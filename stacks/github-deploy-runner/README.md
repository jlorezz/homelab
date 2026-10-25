# Disposable Dealpipe deployment runners

These runners are separate from the three PR/test lanes in `stacks/github-runner`.
PR lanes retain their rootless CI engine. Release workers run as an unprivileged
user in disposable containers in the host engine, with no engine socket, host
network, reusable home/work volume or controller credential mount. A shared
non-secret workload-lock inode is the only host bind mount. The release hook
takes that lock exclusively while a job runs.

Both deployment and preview image builders package the shared hook together with
`custody.py` and `workload.py`. The hook imports custody even for exclusive release
workers; omitting it prevents jobs from starting. The offline regression check
`python3 -m unittest discover -s stacks/github-runner/tests -p test_release_image.py -v`
checks each builder's actual context and Dockerfile copies in a fresh interpreter.
It does not certify a built image or live worker admission. Rebuild and verify a
staging worker before promoting the image to production.

The host controller gets a fresh short-lived repository registration token,
passes it to the entrypoint over stdin, waits for one ephemeral runner job, and
destroys the container. The broad runner-administration credential remains in a
root-only systemd credential outside every worker. Ephemeral registration alone
does not remove container state; the controller removes the complete container.
The two labels are `dealpipe-deploy-staging` and `dealpipe-deploy-production`.
Labels are routing, not access control. Secret release depends on GitHub's
environment and branch protections. An arbitrary job can consume a disposable
runner but cannot leave files or processes for the next release.

The `dealpipe-deploy` Docker bridge rejects access to the NAS, private/link-local
IPv4 destinations, Tailscale and IPv6. Workers need only public GitHub, package
registries, Vercel and environment-specific service endpoints. Resource ceilings
match one existing browser lane: four CPUs and 12 GiB, without swap. They are
admission ceilings, not measured build requirements. The bridge subnet is a
reserved local deployment range; confirm it does not overlap the NAS routes
before activation. The controller always addresses the host engine explicitly.

## Provision and activate

Editing these definitions does not activate services or change application
deployment settings. From an idle NAS maintenance window:

1. Review the image source and run
   `sudo python3 stacks/github-deploy-runner/build-image.py` from the NAS Homelab
   checkout. It uses the host engine and prints an immutable `sha256:` image ID.
   The runner base image is digest-pinned; GitHub requires timely runner updates
   when `--disableupdate` is used. Rebuild with a reviewed base image update when
   GitHub requires a newer runner. Do not indefinitely retain an expired runner.
2. Provision `/var/lib/dealpipe-deploy/github-token`, root-owned mode `0600`,
   outside the Nix store. Use a dedicated repository-scoped fine-grained GitHub
   credential with the Administration permission required to register/remove
   repository runners. Only this host controller receives it. Do not reuse a
   Vercel token, agent login, user session token or worker-visible secret.
3. Set `qnoxslab.githubDeploymentRunners.enable = true`, set `.image` to that
   immutable image ID, and initially set `.environments = [ "staging" ]`.
   Apply the reviewed NixOS configuration through the existing Clan procedure.
   Missing credentials prevent service startup; an unpinned image fails Nix
   assertions. Production remains absent until deliberately added later.
4. Inspect `systemctl status dealpipe-deploy-staging` and the corresponding
   journal. Confirm the repository shows one idle ephemeral deployment runner.
   Verify the worker has no Docker socket/shared workspace and cannot reach NAS,
   CI services, Tailscale or private destinations. Confirm public DNS/HTTPS work.
5. Configure the Dealpipe workflow and GitHub environment secrets as documented
   in its `docs/internals/prebuilt-deployments.md`. Run staging acceptance before
   changing Vercel build ownership. Never configure production settings as a
   side effect of activating this Homelab module.

### Accept a rebuilt image on staging first

`.stagingImage` pins a rebuilt image for the staging controller only; production
keeps `.image`. Use it for any image rebuild, such as the custody packaging fix
in #26:

1. Build the image as in step 1 and record its `sha256:` ID.
2. Set `.stagingImage` to it and leave `.image` on the live image. Confirm the
   evaluated production unit is byte-identical to the live one before switching.
3. After a successful staging release that ran on the new image, move the same
   ID to `.image` and clear `.stagingImage` in a separate reviewed change.

Rollback: clear `.stagingImage` and switch back; staging returns to `.image`.

Live note (2026-10-25): staging and production also carry operator drop-ins at
`/etc/systemd/system.control/dealpipe-deploy-{staging,production}.service.d/40-chromium.conf`.
They replace `ExecStart` to run the hand-layered Chromium image `sha256:852de79d...`,
so a pinned image only takes effect once that unit's drop-in is removed. The
Dockerfile now carries the same Chromium layer, so a rebuilt image can replace it.

## Operate and recover

`journalctl -u dealpipe-deploy-staging` shows worker lifecycle; GitHub Actions is
the release job log. A service restart creates a new registration and container.
Stop the service only when idle or when deliberately aborting that release;
stopping an active release can leave its API published before its web apps.
Inspect the sanitized release artifact before retrying. Container logs remain in
the service journal; do not copy them to Brain without sanitizing evidence.

The controller removes only the exact container/registration it created, including
an interrupted listener. It never deregisters or stops the PR/test runners. Rotate
the controller credential through the root-only credential file and restart an
idle service. The next job starts with a fresh home and filesystem. No Vercel
credentials are stored in the image or Nix definitions.

When the NAS is unavailable, use Dealpipe's operator-controlled Vercel native-build
recovery procedure in `docs/internals/pipeline-operations.md` and
`docs/internals/laptop-release-recovery.md`. There is no GitHub-hosted recovery
runner. Recovery requires an exact previously validated commit and exclusive
release ownership after the normal publishers are disabled and drained. GitHub
does not automatically switch a queued self-hosted label to a hosted machine. Stop the
deployment services and disable this module to retire this capacity; do not
remove application containers, CI registration or database volumes.
