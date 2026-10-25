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
  # /etc/nixos/homelab copy). This is the image in the generated units; the
  # persistent operator drop-ins under /etc/systemd/system.control
  # (40-chromium.conf) override ExecStart to run sha256:852de79d... at runtime.
  # NixOS does not manage those drop-ins; adopting them is a separate change.
  qnoxslab.githubDeploymentRunners = {
    enable = true;
    image = "sha256:840686ed03a267899e312868e6287a5436e6b34d5b85afc1fac00124eca56b0b";
    environments = [ "staging" "production" "preview-build" "preview-publish" ];
    isolatedStaging = false;
    # Dedicated preview workers (dealpipe #827): `dealpipe-nas-preview` and
    # `dealpipe-preview-publish`, built on the NAS 2026-10-25 from the shared Dockerfile.
    previewImage = "sha256:c057290062ef1cb57cd90f90803dc30a9ff9d105599d0f875adfdbc3794daec4";
    # Staging acceptance of the rebuilt image (custody #26 + Chromium #33),
    # built on the NAS 2026-10-25. Production keeps `image` until it passes.
    stagingImage = "sha256:e5ea27e982f038e50fa62ce4b8537921bb1393f5f2772858b388830bf46717f8";
  };
  # Restricted forced-command deploy key for the T3 agent (stacks/agent-deploy/README.md).
  # Public key only; the private half stays in the T3 container. Mutating actions need
  # `agent-deploy-admin arm <action>` on the NAS. Source: T3 Docker network (src 172.18.0.2).
  qnoxslab.agentDeploy = {
    enable = true;
    authorizedKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJwrI2NsXj0V+ek+V60oKQhHO0+IyHHxkNnnVcE1m8Pw agent-deploy@t3";
    fromAddresses = [ "172.18.0.0/16" ];
  };

  # Measured on the NAS 2026-10-24 (5 min x 5 s, CI pool and deploy runners live):
  # 12 logical CPUs, 47,458 MiB RAM, minimum MemAvailable 34,797 MiB, 11,865 MiB reserve
  # (25 percent). Only the preview pool is new: the CI pool, staging and production
  # ceilings were already live when sampled. Peak behaviour with preview running is
  # UNVERIFIED; re-measure under load and restate these numbers if they change.
  qnoxslab.githubRunnerCapacity = {
    enable = true;
    measuredHeadroomMiB = 34797;
    measuredTotalMiB = 47458;
    reserveMiB = 11865;
    measuredCpuPercent = 1000;
    committedAtMeasurementMiB = 53248; # CI pool 32768 + staging 8192 + production 12288
    committedAtMeasurementCpuPercent = 1400; # 800 + 200 + 400
    previewBuild.count = 1;
    previewPublish.count = 1;
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
