{ config, ... }:
let
  c = config.qnoxslab.theme.colors;
  font = config.qnoxslab.theme.font;
in
{
  services.mako = {
    enable = true;
    settings = {
      anchor = "top-right";
      default-timeout = 5000;
      width = 420;
      outer-margin = "20";
      padding = "10,15";
      border-size = 2;
      border-radius = 0;
      max-icon-size = 32;
      font = "sans-serif 14";
      text-color = c.fg;
      border-color = c.accent;
      background-color = c.bg;

      "app-name=Spotify" = {
        invisible = 1;
      };

      "urgency=critical" = {
        default-timeout = 0;
        layer = "overlay";
      };
    };
  };
}
