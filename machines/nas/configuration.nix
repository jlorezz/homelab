{
  pkgs,
  config,
  ...
}:

{
  imports = [
    ./hardware-configuration.nix
    ./modules/defaults.nix
    ./modules/networking.nix
    ./modules/dns.nix
    ./modules/media.nix
    ./modules/containers
    ./modules/containers/portainer.nix
    ./modules/containers/jellyseerr.nix
    ./modules/containers/flaresolverr.nix
    ./modules/containers/excalidraw.nix
    ./modules/containers/stirling-pdf.nix
    ./modules/containers/homeassistant.nix
    ./modules/containers/postgres.nix
    ./modules/containers/linkding.nix
    ./modules/containers/homepage.nix
    ./modules/tailscale.nix
    ./modules/syncthing.nix
    ./modules/netdata.nix
    ./modules/immich.nix
    ./modules/paperless.nix

  ];

  # ===========================================
  # Boot
  # ===========================================

  boot.loader.grub = {
    enable = true;
    device = "/dev/nvme0n1";
    useOSProber = true;
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

  clan.core.vars.generators.jlorezz-password = {
    prompts.value = {
      description = "Hashed password for user jlorezz (generate with: mkpasswd -m sha-512)";
      type = "hidden";
    };
    files.secret = {
      secret = true;
      neededFor = "activation";
    };
    script = ''
      cat $prompts/value > $out/secret
    '';
  };

  # ===========================================
  # State Version
  # ===========================================

  system.stateVersion = "26.05";
}
