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

## Documentation References

- Clan docs: https://docs.clan.lol
- Service definitions: https://docs.clan.lol/services/
- Auto-includes: https://docs.clan.lol/guides/inventory/autoincludes/
