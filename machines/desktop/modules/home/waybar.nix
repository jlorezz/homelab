{
  config,
  pkgs,
  ...
}:
let
  c = config.qnoxslab.theme.colors;
  font = config.qnoxslab.theme.font;
in
{
  programs.waybar = {
    enable = true;
    settings = [
      {
        layer = "top";
        position = "top";
        height = 26;

        modules-left = [
          "custom/logo"
          "hyprland/workspaces"
        ];
        modules-center = [ "clock" ];
        modules-right = [
          "tray"
          "bluetooth"
          "network"
          "pulseaudio"
          "cpu"
        ];

        "custom/logo" = {
          format = " ";
          tooltip = false;
          on-click = "walker";
        };

        "hyprland/workspaces" = {
          format = "{icon}";
          on-click = "activate";
          format-icons = {
            "1" = "1";
            "2" = "2";
            "3" = "3";
            "4" = "4";
            "5" = "5";
            "6" = "6";
            "7" = "7";
            "8" = "8";
            "9" = "9";
            "0" = "0";
            active = "󱓻";
            default = "";
          };
          persistent-workspaces = {
            "*" = [
              1
              2
              3
              4
              5
            ];
          };
        };

        clock = {
          format = "{:L%A %H:%M}";
          format-alt = "{:L%d %B W%V %Y}";
          tooltip-format = "<big>{:%Y %B}</big>\n<tt><small>{calendar}</small></tt>";
        };

        network = {
          format-wifi = " {essid}";
          format-ethernet = " {ifname}";
          format-disconnected = "󰌙";
          tooltip-format = "{ifname}: {ipaddr}/{cidr}";
        };

        pulseaudio = {
          format = "{icon} {volume}%";
          format-muted = "󰝟";
          format-icons = {
            default = [
              "󰕿"
              "󰖀"
              "󰕾"
            ];
          };
          scroll-step = 5;
          on-click = "pavucontrol";
        };

        cpu = {
          format = " {usage}%";
          interval = 2;
          on-click = "ghostty -e btop";
        };

        bluetooth = {
          format = " {status}";
          format-connected = " {device_alias}";
          on-click = "blueman-manager";
        };

        tray = {
          icon-size = 12;
          spacing = 17;
        };
      }
    ];

    style = ''
      @define-color foreground ${c.fg};
      @define-color background ${c.bg};

      * {
        background-color: @background;
        color: @foreground;
        border: none;
        border-radius: 0;
        font-family: '${font.mono}';
        font-size: 14px;
      }

      window#waybar {
        background-color: @background;
      }

      #custom-logo {
        padding: 0 12px;
        font-size: 16px;
        color: ${c.accent};
      }

      #workspaces button {
        padding: 0 8px;
        color: ${c.fg_dim};
        background: transparent;
      }

      #workspaces button.active {
        color: ${c.accent};
        border-bottom: 2px solid ${c.accent};
      }

      #workspaces button.urgent {
        color: ${c.red};
      }

      #clock {
        padding: 0 12px;
      }

      #network, #pulseaudio, #cpu, #bluetooth {
        padding: 0 12px;
      }

      #tray {
        padding: 0 8px;
      }
    '';
  };
}
