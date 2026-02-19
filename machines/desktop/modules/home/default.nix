{ pkgs, ... }:
{
  imports = [
    ./theme.nix
    ./hyprland.nix
    ./waybar.nix
    ./mako.nix
    ./hyprlock.nix
    ./hypridle.nix
    ./terminals.nix
    ./shell.nix
    ./apps.nix
    ./services.nix
  ];

  home = {
    username = "jlorezz";
    homeDirectory = "/home/jlorezz";
    stateVersion = "26.05";
  };

  xdg.enable = true;
  xdg.userDirs = {
    enable = true;
    createDirectories = true;
  };

  # Wallpaper — referenced by swaybg in Hyprland autostart
  home.file.".config/hypr/wallpaper.jpg".source = ./wallpapers/gruvbox.jpg;
}
