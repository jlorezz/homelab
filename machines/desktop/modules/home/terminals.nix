{ config, ... }:
let
  c = config.qnoxslab.theme.colors;
  font = config.qnoxslab.theme.font;
in
{
  # Ghostty — primary terminal (size 12)
  programs.ghostty = {
    enable = true;
    settings = {
      font-family = font.mono;
      font-style = "Regular";
      font-size = font.monoSize;
      window-theme = "ghostty";
      window-padding-x = 14;
      window-padding-y = 14;
      confirm-close-surface = false;
      resize-overlay = "never";
      gtk-toolbar-style = "flat";
      cursor-style = "block";
      cursor-style-blink = false;
      shell-integration-features = "no-cursor,ssh-env";
      mouse-scroll-multiplier = 0.95;

      keybind = [
        "shift+insert=paste_from_clipboard"
        "control+insert=copy_to_clipboard"
        "super+control+shift+alt+arrow_down=resize_split:down,100"
        "super+control+shift+alt+arrow_up=resize_split:up,100"
        "super+control+shift+alt+arrow_left=resize_split:left,100"
        "super+control+shift+alt+arrow_right=resize_split:right,100"
      ];

      background = c.bg;
      foreground = c.fg;
      cursor-color = c.cursor;
      selection-background = c.selection_bg;
      selection-foreground = c.selection_fg;
      palette = [
        "0=${c.bg1}"
        "1=${c.red}"
        "2=${c.green}"
        "3=${c.yellow}"
        "4=${c.blue}"
        "5=${c.purple}"
        "6=${c.aqua}"
        "7=${c.fg}"
        "8=${c.bg1}"
        "9=${c.red}"
        "10=${c.green}"
        "11=${c.yellow}"
        "12=${c.blue}"
        "13=${c.purple}"
        "14=${c.aqua}"
        "15=${c.fg}"
      ];
    };
  };

  # Kitty — alt terminal (size 9)
  programs.kitty = {
    enable = true;
    font = {
      name = font.mono;
      size = 9;
    };
    settings = {
      window_padding_width = 14;
      hide_window_decorations = "yes";
      confirm_os_window_close = 0;
      allow_remote_control = "yes";
      cursor_shape = "block";
      cursor_blink_interval = 0;
      shell_integration = "no-cursor";
      enable_audio_bell = "no";

      tab_bar_edge = "bottom";
      tab_bar_style = "powerline";
      tab_powerline_style = "slanted";
      tab_title_template = "{title}{' :{}:'.format(num_windows) if num_windows > 1 else ''}";

      background = c.bg;
      foreground = c.fg;
      cursor = c.cursor;
      selection_background = c.selection_bg;
      selection_foreground = c.selection_fg;
      active_border_color = c.accent;
      active_tab_background = c.accent;

      color0 = c.bg1;
      color1 = c.red;
      color2 = c.green;
      color3 = c.yellow;
      color4 = c.blue;
      color5 = c.purple;
      color6 = c.aqua;
      color7 = c.fg;
      color8 = c.bg1;
      color9 = c.red;
      color10 = c.green;
      color11 = c.yellow;
      color12 = c.blue;
      color13 = c.purple;
      color14 = c.aqua;
      color15 = c.fg;
    };
    keybindings = {
      "ctrl+insert" = "copy_to_clipboard";
      "shift+insert" = "paste_from_clipboard";
    };
  };

  # Alacritty — alt terminal (size 9)
  programs.alacritty = {
    enable = true;
    settings = {
      env = {
        TERM = "xterm-256color";
      };
      font = {
        normal = {
          family = font.mono;
        };
        bold = {
          family = font.mono;
        };
        italic = {
          family = font.mono;
        };
        size = 9.0;
      };
      window = {
        padding = {
          x = 14;
          y = 14;
        };
        decorations = "None";
      };
      colors = {
        primary = {
          background = c.bg;
          foreground = c.fg;
        };
        cursor = {
          text = c.bg;
          cursor = c.cursor;
        };
        selection = {
          text = c.selection_fg;
          background = c.selection_bg;
        };
        normal = {
          black = c.bg1;
          red = c.red;
          green = c.green;
          yellow = c.yellow;
          blue = c.blue;
          magenta = c.purple;
          cyan = c.aqua;
          white = c.fg;
        };
        bright = {
          black = c.bg1;
          red = c.red;
          green = c.green;
          yellow = c.yellow;
          blue = c.blue;
          magenta = c.purple;
          cyan = c.aqua;
          white = c.fg;
        };
      };
      keyboard = {
        bindings = [
          {
            key = "Insert";
            mods = "Shift";
            action = "Paste";
          }
          {
            key = "Insert";
            mods = "Control";
            action = "Copy";
          }
        ];
      };
    };
  };
}
