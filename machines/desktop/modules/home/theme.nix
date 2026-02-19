# Gruvbox Material theme — single source of truth for all desktop theming.
# All other Home Manager modules reference config.qnoxslab.theme.colors.*
{ lib, ... }:
let
  inherit (lib) mkOption types;
in
{
  options.qnoxslab.theme = {
    colors = {
      # Background shades
      bg = mkOption {
        type = types.str;
        default = "#282828";
      };
      bg1 = mkOption {
        type = types.str;
        default = "#3c3836";
      };
      bg2 = mkOption {
        type = types.str;
        default = "#504945";
      };
      bg3 = mkOption {
        type = types.str;
        default = "#665c54";
      };

      # Foreground shades
      fg = mkOption {
        type = types.str;
        default = "#d4be98";
      };
      fg_bright = mkOption {
        type = types.str;
        default = "#ebdbb2";
      };
      fg_dim = mkOption {
        type = types.str;
        default = "#bdae93";
      };

      # Accent colors (Gruvbox Material palette from your Omarchy theme)
      red = mkOption {
        type = types.str;
        default = "#ea6962";
      };
      green = mkOption {
        type = types.str;
        default = "#a9b665";
      };
      yellow = mkOption {
        type = types.str;
        default = "#d8a657";
      };
      blue = mkOption {
        type = types.str;
        default = "#7daea3";
      };
      purple = mkOption {
        type = types.str;
        default = "#d3869b";
      };
      aqua = mkOption {
        type = types.str;
        default = "#89b482";
      };
      orange = mkOption {
        type = types.str;
        default = "#d65d0e";
      };

      # Selection / cursor
      cursor = mkOption {
        type = types.str;
        default = "#bdae93";
      };
      selection_bg = mkOption {
        type = types.str;
        default = "#d65d0e";
      };
      selection_fg = mkOption {
        type = types.str;
        default = "#ebdbb2";
      };

      # Accent (used for borders, highlights)
      accent = mkOption {
        type = types.str;
        default = "#7daea3";
      };
    };

    font = {
      mono = mkOption {
        type = types.str;
        default = "MonoLisa";
        description = "Primary monospace font family";
      };
      monoFallback = mkOption {
        type = types.str;
        default = "JetBrainsMono Nerd Font";
        description = "Fallback monospace font family";
      };
      monoSize = mkOption {
        type = types.number;
        default = 12;
        description = "Default terminal font size";
      };
    };
  };
}
