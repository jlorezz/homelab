# Private NAS previews

The NAS retains its existing Tailscale name. Preview slots use
`https://nas.tailb80645.ts.net:4101` and `:4102`; no public Funnel or custom DNS is
configured. Each URL routes `/` to its dashboard, `/graphql` to its API,
`/supabase/` to its own Supabase gateway and `/mail/` to captured development email.
WebSocket upgrades pass through for Next hot reload and Realtime.
The dashboard binds to `localhost`, matching Next's internal rewrite hostname.
Using `127.0.0.1` here made Next treat the locale rewrite as an external HTTPS
request to its internal HTTP port; preserve the matching hostname.

Two backend slots are an explicit initial capacity budget, not a claim of measured
parallel throughput. Each slot has separate Postgres, Auth, Storage, Realtime,
Mailpit and Redis state. Only canonical development seeds are loaded. External
billing, AI, email and telephony credentials are not copied into the preview.
Preview data is disposable; never put customer data or production credentials in it.
Live Postgres files are not a restorable backup. The Nix backup definition excludes
slot databases and objects; rebuild them from migrations and development seeds.

## Use from a Dealpipe worktree on the NAS

```sh
nas-preview setup
nas-preview run --timeout 1800
nas-preview status
```

Setup preserves a slot's data on later runs. It refuses an existing unrelated
`.env` or app-level environment overrides. Both setup and preview servers pass
through `agent-workload`, so they wait behind CI and retain the existing cgroup
limits. Admission is exclusive: only one app session runs at a time, and CI jobs
that arrive during a preview wait until it stops. Stop idle previews promptly.
The default preview lifetime is 30 minutes; select an explicit longer
budget when needed. Ctrl-C or expiry stops the owned app processes and proxy;
backend data persists. A stopped preview returns 502 until started again.

Slot ownership is recorded under `~/.nas-previews/claims.json`; initialized
receipts bind a slot to its checkout. Moving/removing a worktree does not release
its backend or transfer its data. When both slots are assigned, setup fails with
an explicit capacity message instead of silently handing another worktree's data
to the next caller. Coordinate retirement with the operator.

Browser cookies ignore ports. Dealpipe's `localAuthCookieOptions` must accept its
explicit `sb-local-<port>-auth-token` name for HTTPS `.ts.net` origins as well as
loopback; this is the small application adapter in the NAS preview feature branch.
The same branch routes automatic `scripts/setup-worktree.sh` setup to `nas-preview`
when the operator adapter is installed. Until that application change is merged,
new worktrees from `development` still use the Docker-dependent setup and need
the adapter commit before NAS preparation. Do not remove the per-port cookie name
or reuse a hosted environment file.

## Operator deployment

Run from the reviewed Homelab administration checkout on the NAS with the
existing Python environment containing PyYAML. Provision through Portainer only:

```sh
python3 stacks/agents/previews/deploy.py --slot 1
python3 stacks/agents/previews/deploy.py --slot 2
```

Wrap these commands with the NAS workload guard for their bounded deployment
lifetime. The helper creates only `nas-preview-1` / `nas-preview-2`, using pinned
Supabase/Redis images. Credentials and data live under
`/var/lib/agent-platform/previews/<slot>`. Worker-facing configuration is written
to root-owned `bin/preview/configs` (0640, group 1100); agents receive that directory
read-only and receive no Docker/Portainer token, host SSH key or TLS private key.

Install `run.py`, `proxy.mjs` and `tailnet-relay.py` from this directory into
`/var/lib/agent-platform/bin/preview`, owned by root. The agent's `nas-preview`
launcher executes `python3 /opt/nas-preview/run.py`. Deploy T3 using
`deploy-agents --with-previews` to merge `compose.previews.yaml`. Preserve the
existing optional sanitized-testing network attachment during a narrow rollout.

`agent-platform.nix` declares the two Tailscale Serve ports and the TLS relay.
The relay binds only Docker's `172.17.0.1` address and forwards those two ports
to the NAS's Tailscale IP. T3 resolves the NAS hostname to that relay; the TLS
certificate is still issued/terminated by Tailscale Serve and is verified
normally. Only Docker bridge ingress to those two relay ports is added to the
host firewall. Existing service routes remain in place.

The T3 container uses public upstream DNS for development downloads. The NAS's
AdGuard rules resolved `releases.posthog.com` to `0.0.0.0`, which broke the
repository's pinned PostHog CLI postinstall. Docker's internal service-name DNS
continues to work. This changes only the agent container's DNS configuration.

## Verification and recovery

Verify login through captured OTP email, a protected dashboard route and a real
GraphQL request through the private URL. Check browser console/network errors,
hot reload and a second client. A healthy blank T3 preview proves only that Chrome
starts, not that this application stack works.

The entire preview path depends on the NAS and Tailscale being available. It does
not depend on the Windows PC. Host reboot recovery should be checked separately
from a container replacement; do not describe an untested reboot as verified.

Before retiring a slot, stop its owned preview, confirm its claim and preserve any
wanted changes in Git. Delete only its named Portainer stack and its own disposable
data directory, then remove only that slot's claim/initialization receipt and
reprovision it. Never run the laptop's shared Supabase reset/stop commands here,
and never delete the sanitized testing, production or CI stacks. Retained private
credentials and backend state must match; do not copy a different slot's config.

Sources: [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve),
[Mailpit reverse proxy configuration](https://mailpit.axllent.org/docs/configuration/proxy/),
and Dealpipe's current `docs/worktree-setup.md` / installed Next.js
`allowedDevOrigins` documentation. Existing full-stack worktree services are the
application contract; Homelab adapts their hosting for the socket-free NAS worker.

The 2026-10-23 narrow rollout uses `/etc/systemd/system.control` service/drop-in
files for the relay, Serve ports, firewall and backup exclusion. A later full
NixOS activation must remove those matching temporary overrides after adopting
the declarations here; otherwise an override can retain an older Nix store path.
No NAS reboot was performed during this setup.

## Verified rollout: 2026-10-23

Slot 1 belongs to `/workspace/dealpipe-preview`, branch `feat/nas-private-preview`.
Dealpipe [PR #813](https://github.com/dealpipe/dealpipe/pull/813), commit
`0643b88a6`, adds NAS worktree setup and per-port Tailscale
cookies. This is a feature-branch implementation, not a merged default-branch
change. Slot 2 is provisioned and unassigned. T3 has manual **Prepare NAS preview**
and **Start NAS preview** project actions; automatic issue delivery remains off.

A desktop browser over Tailscale completed OTP login from the captured mailbox,
loaded the protected property dashboard with canonical development data, and
received successful real GraphQL responses. The session used
`sb-local-4101-auth-token`. The current Next HMR endpoint `/_next/hmr` connected,
and a Supabase Realtime WebSocket channel joined successfully through the prefix.
The NAS itself also reached the verified HTTPS endpoint through the Docker relay.
Stopping the owned preview released its workload lease and returned the public
preview URL to 502 while T3 stayed healthy. Rerunning setup preserved seeded data.

The Homelab source doctor, 114 unit tests and Nix evaluation passed. Dealpipe lint
passed with eight existing generated-code warnings; commit hooks passed. Full NAS
reboot and the user's Mac/phone were not tested. Third-party integrations without
development credentials are outside this verification.

Native npm modules such as Sharp also require the Nix C++ runtime on
`LD_LIBRARY_PATH`; Chrome's library list alone did not provide it. The image
now includes `stdenv.cc.cc.lib` in that path. For the already-running image,
the operator installed a read-only `native-libs` link beside `run.py`, pointing
to that image's existing GCC library directory. The preview launcher adds it
when present; no running user shell was interrupted. Commands outside the
preview launcher on that older image can use
`export LD_LIBRARY_PATH=/opt/nas-preview/native-libs:$LD_LIBRARY_PATH`.
Adopt the updated image during the next idle container replacement.

Next can detach a telemetry shutdown child even when telemetry is disabled.
The launcher acts as a Linux child subreaper and terminates those owned
reparented descendants before returning; it never selects processes by name.
The detached-child cleanup check also passed inside the NAS container. The
complete Dealpipe pre-push checks passed after the native library correction.
