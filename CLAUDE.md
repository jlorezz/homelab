# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a personal infrastructure management project ("qnoxslab") using the Clan framework - a declarative infrastructure management system built on NixOS. It manages distributed machines with shared services and encrypted secrets.

**Repository**: https://github.com/jlorezz/homelab
**Domain**: qnoxs.lab

## Development Commands

```bash
# Enter development shell (includes Clan CLI)
nix develop

# Direnv will auto-activate the shell when entering the directory
# (requires direnv installed and `direnv allow`)
```

The Clan CLI provides commands for machine management, deployment, and secrets. Run `clan --help` in the dev shell for available commands.

## Architecture

**Core Files**:
- `flake.nix` - Nix flakes entry point; imports clan-core and defines dev shells for all platforms (x86_64-linux, aarch64-linux, aarch64-darwin, x86_64-darwin)
- `clan.nix` - Main infrastructure definition: machine inventory, service instances, and machine-specific NixOS configurations

**Key Directories**:
- `modules/` - Reusable NixOS modules (import into machine configurations)
- `sops/` - Encrypted secrets management using SOPS
- `machines/<name>/` - Per-machine configurations (auto-imported by Clan)

**Service Architecture** (defined in `clan.nix`):
- `admin` - Root access and SSH key management
- `zerotier` - Mesh networking for machine connectivity (requires a controller machine)
- `tor` - Anonymous network access as fallback connection

## Key Technologies

- **Clan Framework**: https://docs.clan.lol
- **NixOS**: Declarative Linux distribution
- **SOPS**: Secrets encryption
- **ZeroTier**: Mesh networking
- **Disko**: Declarative disk partitioning

## Adding a New Machine

1. Add machine to `inventory.machines` in `clan.nix`
2. Create `machines/<name>/configuration.nix` (auto-imported)
3. Configure SSH keys in the admin service
4. If using ZeroTier, deploy the controller machine first

## Documentation

### Obsidian Vault (Internal Docs)

Project documentation lives in the Obsidian vault under `Brain/Projects/homelab/`:

The main vault is `/storage/obsidian` on the NAS and is the single source of truth
for homelab documentation. Agents maintain source-backed project knowledge through
`brain-record`; uncertain recommendations remain Inbox proposals. Routine capture
needs no human approval. Do not keep a vault documentation mirror in this repository.
The desktop working copy is `G:\obsidian-notes\NAS`. The central organized Brain
is `/storage/obsidian/Brain`; the former standalone archive was intentionally
removed by the user. Open `Brain/Start Here.md` for project entry
points. Desktop folders are synced working copies; deleting their contents can
propagate to the NAS. Agents search the read-only `shared-brain` MCP service and
publish sourced project records through the collector and keep uncertain suggestions
as proposals. Knowledge records never grant execution authorization. Notes use self-hosted LiveSync/CouchDB, not paid Obsidian Sync.
Syncthing was removed on 2026-10-15; LiveSync is the only notes sync service.

Current runbooks cover power/memory, maintenance, backups, restore, disaster
recovery, desktop/iPhone onboarding, troubleshooting and incident templates.
Never commit client plugin `data.json`, setup URIs, recovery keys or passwords.
Use `lib.mkMerge` to compose full NixOS configuration fragments; shallow `//`
merging can discard systemd settings.

- **Reference/** and **Runbooks/**
  - `Overview.md` - Architecture, technologies, machines summary
  - `NAS.md` - NAS machine config, storage layout, installed services
  - `Networking.md` - Network topology, firewall, DNS, Tailscale setup
  - `Secrets Management.md` - Clan vars, SOPS, how to add secrets
  - `Adding a New Machine.md` - Step-by-step guide with checklist
- **Reference/**
  - `Services.md` - Full service catalog with port map
- **Decisions/**, **Learnings/** - reusable project knowledge
- **History/** and **Plans/** - dated evidence and design drafts

When making infrastructure changes, record evidenced Brain updates through
the agent-knowledge workflow. The vault handbook is
`Brain/System/How This Brain Works.md`.

### External References

- Clan docs: https://docs.clan.lol
- Service definitions: https://docs.clan.lol/services/
- Auto-includes: https://docs.clan.lol/guides/inventory/autoincludes/
