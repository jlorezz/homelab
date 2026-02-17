{
  pkgs,
  config,
  inputs,
  ...
}:
{
  imports = [
    ./hardware-configuration.nix
    ./modules/defaults.nix
    ./modules/networking.nix
    ./modules/hyprland.nix
    ./modules/audio.nix
    ./modules/fonts.nix
    ./modules/input.nix
    ../../modules/nvidia.nix
    inputs.home-manager.nixosModules.home-manager
  ];

  qnoxslab.nvidia.enable = true;
  qnoxslab.nvidia.openKernel = true;

  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  nixpkgs.config.allowUnfree = true;

  environment.systemPackages = with pkgs; [
    vim
    git
    curl
    wget
    htop
  ];

  virtualisation.docker.enable = true;

  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "prohibit-password";
      PasswordAuthentication = false;
    };
  };

  users.users.jlorezz = {
    isNormalUser = true;
    extraGroups = [
      "wheel"
      "video"
      "audio"
      "docker"
      "input"
      "networkmanager"
    ];
    hashedPasswordFile = config.clan.core.vars.generators.jlorezz-desktop-password.files.secret.path;
    shell = pkgs.bash;
  };

  security.sudo.wheelNeedsPassword = false;

  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    extraSpecialArgs = { inherit inputs; };
    users.jlorezz = import ./modules/home;
  };

  clan.core.vars.generators.jlorezz-desktop-password = {
    prompts.value = {
      description = "Hashed password for jlorezz on desktop (generate with: mkpasswd -m sha-512)";
      type = "hidden";
    };
    files.secret = {
      secret = true;
      neededFor = "activation";
    };
    script = "cat $prompts/value > $out/secret";
  };

  system.stateVersion = "26.05";
}
