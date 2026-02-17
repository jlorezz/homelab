{ pkgs, ... }:
{
  i18n.inputMethod = {
    enable = true;
    type = "fcitx5";
    fcitx5.addons = with pkgs; [
      fcitx5-gtk
      fcitx5-qt
      fcitx5-configtool
    ];
  };

  hardware.logitech.wireless = {
    enable = true;
    enableGraphical = true;
  };

  # Apple keyboard: F-keys as default, media keys with Fn
  boot.extraModprobeConfig = ''
    options hid_apple fnmode=2
  '';
}
