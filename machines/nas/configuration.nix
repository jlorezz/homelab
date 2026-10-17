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
