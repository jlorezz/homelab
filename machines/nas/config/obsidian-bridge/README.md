# LiveSync filesystem bridge compatibility

The source is pinned to upstream commit `5645cf6a74e863ccc29bc2e9dedef1c4941e096b`.
The checked-in Deno imports and lock use Commonlib 0.1.34, matching LiveSync
1.0.36. The upstream bridge's 0.1.19 database format is rejected by that client.

`compat.patch` supports an empty/root vault prefix and filters decrypted note
paths after loading them. Filtering protected metadata paths before decryption
can reject valid notes. Internal files remain excluded unless explicitly allowed.
The provisioning script creates an unlocked milestone document before the bridge
starts: without it the upstream bridge marks the remote empty and skips its watch.

The image builds with frozen dependencies and caches runtime imports in advance.
A clean NAS builds the pinned image on first start; subsequent starts use the local
image. Internet access is needed for that first image build, not for note storage.

Validation on 2026-10-14: TypeScript check; isolated NAS-file -> encrypted CouchDB
-> current Commonlib client read; client write -> decrypted NAS file. Re-run the
isolated integration check when changing either the plugin or bridge. Never point
`check-obsidian-sync.ts` at a production database.
