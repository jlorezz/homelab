{ config, ... }:
let
  c = config.qnoxslab.theme.colors;
  font = config.qnoxslab.theme.font;
  hex = s: builtins.substring 1 6 s;
in
{
  programs.hyprlock = {
    enable = true;
    settings = {
      general = {
        ignore_empty_input = true;
      };

      background = [
        {
          monitor = "";
          color = "rgba(40, 40, 40, 1.0)";
          path = "~/.config/hypr/wallpaper.jpg";
          blur_passes = 3;
        }
      ];

      animations = {
        enabled = false;
      };

      input-field = [
        {
          monitor = "";
          size = "650, 100";
          position = "0, 0";
          halign = "center";
          valign = "center";
          inner_color = "rgba(40, 40, 40, 0.8)";
          outer_color = "rgba(${hex c.fg}, 1.0)";
          outline_thickness = 4;
          font_family = font.mono;
          font_color = "rgba(${hex c.fg}, 1.0)";
          placeholder_text = "Enter Password";
          check_color = "rgba(${hex c.accent}, 1.0)";
          fail_text = "<i>$FAIL ($ATTEMPTS)</i>";
          rounding = 0;
          shadow_passes = 0;
          fade_on_empty = false;
        }
      ];

      # Fingerprint auth can be enabled via security.pam.services.hyprlock if hardware is present
    };
  };
}
