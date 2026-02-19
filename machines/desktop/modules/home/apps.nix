{ pkgs, ... }:
{
  home.packages = with pkgs; [
    # Hyprland ecosystem
    walker
    hyprpicker
    hyprsunset
    grimblast
    swaybg
    swayosd
    wl-clipboard

    # Browsers
    brave
    chromium

    # Editors
    zed-editor
    neovim

    # Communication
    signal-desktop
    vesktop
    obsidian

    # Media
    spotify
    mpv
    imv
    kdenlive
    obs-studio

    # Office / documents
    libreoffice
    evince

    # File management
    nautilus

    # Dev tools
    docker-compose
    lazydocker
    lazygit

    # Password manager
    _1password-gui

    # Network / sync
    localsend
    # tailscale CLI is installed at system level via services.tailscale.enable

    # Remote desktop
    freerdp3

    # System tools
    pavucontrol
    blueman
    networkmanagerapplet
    xdg-utils
  ];
}
