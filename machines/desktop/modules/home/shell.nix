{ pkgs, ... }:
{
  programs.bash = {
    enable = true;
    shellAliases = {
      ls = "eza --icons";
      lsa = "eza -la --icons";
      lt = "eza --tree --icons";
      lta = "eza --tree -a --icons";
      cat = "bat --style=plain";
      ".." = "cd ..";
      "..." = "cd ../..";
      "...." = "cd ../../..";
      open = "xdg-open";
      n = "nvim";
      d = "docker";
      g = "git";
      gcm = "git commit -m";
      gcam = "git commit -am";
    };
    sessionVariables = {
      EDITOR = "nvim";
      VISUAL = "nvim";
      SUDO_EDITOR = "nvim";
      TERMINAL = "xdg-terminal-exec";
      BAT_THEME = "ansi";
    };
    initExtra = ''
      eval "$(zoxide init bash)"
      eval "$(mise activate bash)"

      # zoxide wrapper for cd
      cd() {
        if [ $# -eq 0 ]; then
          builtin cd
        else
          z "$@"
        fi
      }

      # fzf + bat file finder
      ff() {
        fzf --preview 'bat --style=plain --color=always {}'
      }

      # Astro A50 as default source
      pactl set-default-source alsa_input.usb-Astro_Gaming_Astro_A50-00.mono-chat 2>/dev/null || true

      export PATH="$HOME/.local/bin:$PATH"
      export PATH="$HOME/.cargo/bin:$PATH"
    '';
  };

  programs.starship = {
    enable = true;
    settings = {
      add_newline = true;
      command_timeout = 200;
      format = "\$directory\$git_branch\$git_status\$character";

      character = {
        error_symbol = "[✗](bold cyan)";
        success_symbol = "[❯](bold cyan)";
      };

      directory = {
        truncation_length = 2;
        repo_root_style = "bold cyan";
      };

      git_branch = {
        style = "italic cyan";
      };

      git_status = {
        style = "cyan";
      };
    };
  };

  programs.fzf = {
    enable = true;
    defaultCommand = "fd --type f --hidden --follow --exclude .git";
    defaultOptions = [
      "--preview"
      "bat --style=plain --color=always {}"
    ];
  };

  programs.zoxide.enable = true;

  home.packages = with pkgs; [
    bat
    eza
    fd
    ripgrep
    btop
    fastfetch
    gum
    dust
    mise
    playerctl
  ];
}
