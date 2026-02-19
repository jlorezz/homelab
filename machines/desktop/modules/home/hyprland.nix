{
  config,
  pkgs,
  ...
}:
let
  c = config.qnoxslab.theme.colors;
  hex = s: builtins.substring 1 6 s;
in
{
  wayland.windowManager.hyprland = {
    enable = true;
    systemd.enable = false; # UWSM handles systemd session

    settings = {
      # Monitors — DP-2: 34" ultrawide (main), DP-1: 27" vertical (left)
      monitor = [
        "DP-2, 3440x1440@240, 0x0, 1"
        "DP-1, 2560x1440@165, -1440x-1000, 1, transform, 1"
      ];

      env = [
        "GDK_SCALE,1"
        "XCURSOR_SIZE,24"
        "HYPRCURSOR_SIZE,24"
        "GDK_BACKEND,wayland,x11,*"
        "QT_QPA_PLATFORM,wayland;xcb"
        "QT_STYLE_OVERRIDE,kvantum"
        "SDL_VIDEODRIVER,wayland"
        "MOZ_ENABLE_WAYLAND,1"
        "ELECTRON_OZONE_PLATFORM_HINT,wayland"
        "OZONE_PLATFORM,wayland"
        "XDG_SESSION_TYPE,wayland"
        "XDG_CURRENT_DESKTOP,Hyprland"
        "XDG_SESSION_DESKTOP,Hyprland"
        # NVIDIA
        "NVD_BACKEND,direct"
        "LIBVA_DRIVER_NAME,nvidia"
        "__GLX_VENDOR_LIBRARY_NAME,nvidia"
      ];

      general = {
        gaps_in = 5;
        gaps_out = 10;
        border_size = 2;
        "col.active_border" = "rgb(${hex c.accent})";
        "col.inactive_border" = "rgba(${hex c.bg1}aa)";
        resize_on_border = false;
        layout = "dwindle";
      };

      decoration = {
        rounding = 0;
        active_opacity = 0.97;
        inactive_opacity = 0.9;
        shadow = {
          enabled = true;
          range = 2;
        };
        blur = {
          enabled = true;
          size = 2;
          passes = 2;
        };
      };

      animations = {
        enabled = true;
        bezier = [
          "easeOutQuint, 0.23, 1, 0.32, 1"
          "easeInOutCubic, 0.65, 0.05, 0.36, 1"
        ];
        animation = [
          "windows, 1, 3, easeOutQuint, popin 80%"
          "fade, 1, 2, easeOutQuint"
          "workspaces, 0"
        ];
      };

      input = {
        kb_layout = "de";
        kb_variant = "mac";
        kb_options = "compose:caps";
        follow_mouse = 1;
        sensitivity = 0;
        repeat_rate = 40;
        repeat_delay = 600;
        numlock_by_default = true;
        touchpad = {
          natural_scroll = false;
          scroll_factor = 0.4;
        };
      };

      dwindle = {
        pseudotile = true;
        preserve_split = true;
        force_split = 2;
      };

      misc = {
        disable_hyprland_logo = true;
        disable_splash_rendering = true;
        force_default_wallpaper = 0;
        focus_on_activate = true;
        key_press_enables_dpms = true;
        mouse_move_enables_dpms = true;
      };

      cursor = {
        hide_on_key_press = true;
      };

      xwayland = {
        force_zero_scaling = true;
      };

      ecosystem = {
        no_update_news = true;
      };

      exec-once = [
        "${pkgs.hypridle}/bin/hypridle"
        "${pkgs.mako}/bin/mako"
        "${pkgs.waybar}/bin/waybar"
        "fcitx5 -d"
        "${pkgs.swaybg}/bin/swaybg -i ~/.config/hypr/wallpaper.jpg -m fill"
        "${pkgs.swayosd}/bin/swayosd-server"
        "${pkgs.polkit_gnome}/libexec/polkit-gnome-authentication-agent-1"
      ];

      # Window rules
      windowrulev2 = [
        "suppressevent maximize, class:.*"
        "opacity 0.97 0.9, class:.*"
        "float, class:^(1Password)$"
        "float, class:^(polkit-gnome-authentication-agent-1)$"
      ];

      # Persistent workspaces on main monitor
      workspace = [
        "1, persistent:true, monitor:DP-2"
        "2, persistent:true, monitor:DP-2"
        "3, persistent:true, monitor:DP-2"
        "4, persistent:true, monitor:DP-2"
        "5, persistent:true, monitor:DP-2"
      ];

      "$mod" = "SUPER";

      # Application bindings — matching Omarchy layout
      bindd = [
        "$mod, RETURN, Terminal, exec, ghostty"
        "$mod SHIFT, F, File manager, exec, nautilus --new-window"
        "$mod SHIFT, B, Browser, exec, brave"
        "$mod SHIFT, M, Music, exec, spotify"
        "$mod SHIFT, N, Editor, exec, zed"
        "$mod SHIFT, D, Docker, exec, ghostty -e lazydocker"
        "$mod SHIFT, G, Signal, exec, signal-desktop"
        "$mod SHIFT, O, Obsidian, exec, obsidian --disable-gpu --enable-wayland-ime"
        "$mod SHIFT, SLASH, Passwords, exec, 1password"
      ];

      # Window management bindings
      bind = [
        "$mod, Q, killactive"
        "$mod, F, fullscreen"
        "$mod, V, togglefloating"
        "$mod, P, exec, walker"
        "$mod, ESCAPE, exec, hyprlock"

        # Focus
        "$mod, H, movefocus, l"
        "$mod, L, movefocus, r"
        "$mod, K, movefocus, u"
        "$mod, J, movefocus, d"

        # Move windows
        "$mod SHIFT, H, movewindow, l"
        "$mod SHIFT, L, movewindow, r"
        "$mod SHIFT, K, movewindow, u"
        "$mod SHIFT, J, movewindow, d"

        # Workspaces
        "$mod, 1, workspace, 1"
        "$mod, 2, workspace, 2"
        "$mod, 3, workspace, 3"
        "$mod, 4, workspace, 4"
        "$mod, 5, workspace, 5"
        "$mod, 6, workspace, 6"
        "$mod, 7, workspace, 7"
        "$mod, 8, workspace, 8"
        "$mod, 9, workspace, 9"
        "$mod, 0, workspace, 10"

        # Move to workspace
        "$mod SHIFT, 1, movetoworkspace, 1"
        "$mod SHIFT, 2, movetoworkspace, 2"
        "$mod SHIFT, 3, movetoworkspace, 3"
        "$mod SHIFT, 4, movetoworkspace, 4"
        "$mod SHIFT, 5, movetoworkspace, 5"
        "$mod SHIFT, 6, movetoworkspace, 6"
        "$mod SHIFT, 7, movetoworkspace, 7"
        "$mod SHIFT, 8, movetoworkspace, 8"
        "$mod SHIFT, 9, movetoworkspace, 9"
        "$mod SHIFT, 0, movetoworkspace, 10"

        # Monitor focus
        "$mod, comma, focusmonitor, -1"
        "$mod, period, focusmonitor, +1"
        "$mod SHIFT, comma, movewindow, mon:-1"
        "$mod SHIFT, period, movewindow, mon:+1"

        # Media keys
        ", XF86AudioRaiseVolume, exec, swayosd-client --output-volume raise"
        ", XF86AudioLowerVolume, exec, swayosd-client --output-volume lower"
        ", XF86AudioMute, exec, swayosd-client --output-volume mute-toggle"
        ", XF86AudioMicMute, exec, swayosd-client --input-volume mute-toggle"
        ", XF86AudioPlay, exec, playerctl play-pause"
        ", XF86AudioNext, exec, playerctl next"
        ", XF86AudioPrev, exec, playerctl previous"

        # Screenshots
        ", Print, exec, grimblast copy area"
        "SHIFT, Print, exec, grimblast copy screen"
      ];

      # Mouse bindings
      bindm = [
        "$mod, mouse:272, movewindow"
        "$mod, mouse:273, resizewindow"
      ];
    };
  };
}
