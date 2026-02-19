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
    ./modules/networking.nix
    ./modules/dns.nix
    ./modules/media.nix
    ./modules/containers
    ./modules/tailscale.nix
    ./modules/netdata.nix
    ./modules/immich.nix
    ./modules/paperless.nix
    ./modules/syncthing.nix
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
