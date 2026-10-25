{
  pkgs,
  config,
  ...
}:

let
  mkClanSecret = import ./lib/mkClanSecret.nix;
in
{
  imports = [
    ./hardware-configuration.nix
    ./modules/defaults.nix
    ./modules/updates.nix
    ./modules/efficiency.nix
    ./modules/agent-platform.nix
    ./modules/github-runner.nix
    ./modules/github-deploy-runner.nix
    ./modules/agent-deploy.nix
    ./modules/backups.nix
    ./modules/obsidian.nix
    ./modules/networking.nix
    ./modules/dns.nix
    ./modules/media.nix
    ./modules/containers
    ./modules/tailscale.nix
    ./modules/beszel.nix
    ./modules/immich.nix
    ./modules/paperless.nix
  ];

  # ===========================================
  # Boot
  # ===========================================

  boot.loader.grub = {
    enable = true;
    device = "/dev/nvme0n1";
  };

  # ===========================================
  # System
  # ===========================================

  # Timezone is configured in modules/defaults.nix via qnoxslab.timezone

  nixpkgs.config.allowUnfree = true;

  qnoxslab.agents.enable = true;
  qnoxslab.githubRunner.enable = true;
  # Reconciled 2026-10-25 from the live NAS (previously only in the hand-edited
  # /etc/nixos/homelab copy): both controllers run this image today.
  qnoxslab.githubDeploymentRunners = {
    enable = true;
    image = "sha256:852de79d34f945188ee55c03dffe2dd834d1d0fde7829a6d49dd3e74d8ba01e2";
    environments = [ "staging" "production" ];
    isolatedStaging = false;
  };
  # Restricted forced-command deploy key for the T3 agent (stacks/agent-deploy/README.md).
  # Public key only; the private half stays in the T3 container. Mutating actions need
  # `agent-deploy-admin arm <action>` on the NAS. Source: T3 Docker network (src 172.18.0.2).
  qnoxslab.agentDeploy = {
    enable = true;
    authorizedKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJwrI2NsXj0V+ek+V60oKQhHO0+IyHHxkNnnVcE1m8Pw agent-deploy@t3";
    fromAddresses = [ "172.18.0.0/16" ];
  };

  environment.systemPackages = with pkgs; [
    vim
    git
    htop
    tmux
  ];

  # ===========================================
  # SSH
  # ===========================================

  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "prohibit-password"; # Hardened: key-based auth only
      PasswordAuthentication = false;
    };
  };

  # ===========================================
  # Users
  # ===========================================

  users.users.jlorezz = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAKNFrN6Szt/tugD8h6SPcZhTBa1wtz3BnwS0ndfu7gd florianbress@gmail.com"
    ];
    hashedPasswordFile = config.clan.core.vars.generators.jlorezz-password.files.secret.path;
    # SSH keys managed via Clan admin service in clan.nix
  };

  security.sudo.wheelNeedsPassword = false;

  # ===========================================
  # Secrets
  # ===========================================

  clan.core.vars.generators = mkClanSecret {
    name = "jlorezz-password";
    description = "Hashed password for user jlorezz (generate with: mkpasswd -m sha-512)";
    neededFor = "activation";
  };

  # ===========================================
  # State Version
  # ===========================================

  system.stateVersion = "24.11";
}
