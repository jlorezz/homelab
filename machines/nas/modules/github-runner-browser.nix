{ config, lib, pkgs, ... }:
let
  docker = config.virtualisation.docker.rootless.package;
in {
  # Reconciled from the live docker-browser.service/daemon configuration on
  # 2026-10-24. Same UID and aggregate as main CI, separate network and data.
  config = lib.mkIf config.qnoxslab.githubRunner.enable {
    systemd.user.services.docker-browser = {
      description = "Isolated rootless Docker engine for Dealpipe browser CI";
      wantedBy = [ "default.target" ];
      unitConfig = { ConditionUser = "dealpipe-ci"; StartLimitBurst = 3; StartLimitIntervalSec = 60; };
      restartIfChanged = false;
      stopIfChanged = false;
      path = [ "/run/wrappers" docker pkgs.coreutils pkgs.findutils pkgs.gnugrep pkgs.gnused pkgs.systemd ];
      environment = {
        XDG_RUNTIME_DIR = "%t/dealpipe-browser";
        DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS = "false";
        DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER = "none";
        RES_OPTIONS = "timeout:1 attempts:1";
      };
      postStart = ''
        printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\n' > "$XDG_RUNTIME_DIR/dockerd-rootless/resolv.conf"
      '';
      serviceConfig = {
        Type = "notify";
        NotifyAccess = "all";
        Delegate = true;
        RuntimeDirectory = "dealpipe-browser";
        RuntimeDirectoryMode = "0700";
        ExecStart = "${docker}/bin/dockerd-rootless --config-file=${pkgs.writeText "dealpipe-browser-daemon.json" (builtins.toJSON {
          "data-root" = "/var/lib/dealpipe-ci/browser/docker";
          "dns" = [ "1.1.1.1" "9.9.9.9" ];
          "log-driver" = "local";
          "log-opts" = { "max-size" = "10m"; "max-file" = "3"; };
        })}";
        ExecReload = "${pkgs.procps}/bin/kill -s HUP $MAINPID";
        KillMode = "mixed";
        LimitCORE = "infinity";
        LimitNOFILE = "infinity";
        LimitNPROC = "infinity";
        Restart = "always";
        RestartSec = 2;
        TimeoutSec = 0;
      };
    };
  };
}
