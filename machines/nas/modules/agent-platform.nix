{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.qnoxslab.agents;
in
{
  imports = [ ./agent-knowledge.nix ];
  options.qnoxslab.agents = {
    enable = lib.mkEnableOption "host support for Portainer-managed development agents";
    stateDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/agent-platform";
      description = "Persistent SSD-backed agent state; never a live infrastructure checkout.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.groups.agent-platform.gid = 1100;
    users.users.agent-platform = {
      isSystemUser = true;
      uid = 1100;
      group = "agent-platform";
      home = cfg.stateDirectory;
    };

    # Portainer owns container lifecycle. This module only prepares the host.
    systemd.tmpfiles.rules =
      map (path: "d ${cfg.stateDirectory}/${path} 0700 agent-platform agent-platform -") [
        "t3"
        "opencode"
        "projects"
        "projects/t3"
        "projects/opencode"
      ]
      ++ [
        "d ${cfg.stateDirectory} 0711 root root -"
        "d ${cfg.stateDirectory}/secrets 0700 root root -"
        "d ${cfg.stateDirectory}/policy 0755 root root -"
        "d ${cfg.stateDirectory}/bin 0755 root root -"
        # All workers can flock the same inode; none can unlink/replace it.
        "d /var/lib/agent-workloads 0755 root root -"
        "f /var/lib/agent-workloads/heavy.lock 0666 root root -"
      ];

    # Compete below the default-weight user/system slices, which contain CI.
    # Weights on agent-workloads alone cannot prioritize a different parent tree.
    systemd.slices.agent.sliceConfig = {
      CPUWeight = 20;
      IOWeight = 20;
    };

    # Shared ceiling, including build/test subprocesses inside the containers.
    systemd.slices.agent-workloads = {
      description = "Resource budget for Portainer-managed agent containers";
      sliceConfig = {
        CPUAccounting = true;
        MemoryAccounting = true;
        CPUQuota = "400%";
        CPUWeight = 25;
        MemoryHigh = "12G";
        MemoryMax = "16G";
        MemorySwapMax = "0";
        TasksMax = 4096;
      };
    };

    # Local listeners are published only through the existing tailnet HTTPS proxy.
    qnoxslab.tailscaleServe = {
      "3773" = 3773;
      "4096" = 4096;
      "4101" = 4101; # Private worktree preview slot 1
      "4102" = 4102; # Private worktree preview slot 2
    };

    # Docker clients cannot dial the host's userspace Tailscale listeners directly.
    # Carry TLS unchanged from the Docker-only address; no certificate key enters T3.
    systemd.services.agent-preview-relay = {
      description = "Private Docker ingress to NAS Tailscale previews";
      requires = [ "docker.service" ];
      after = [ "docker.service" "tailscale-serve.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 ${../../../stacks/agents/previews/tailnet-relay.py}";
        Restart = "on-failure";
        RestartSec = 5;
        DynamicUser = true;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
      };
    };
    networking.firewall.extraCommands = ''
      iptables -C nixos-fw -i br+ -d 172.17.0.1 -p tcp -m multiport --dports 4101,4102 -m comment --comment agent-preview-relay -j nixos-fw-accept 2>/dev/null || \
        iptables -I nixos-fw 1 -i br+ -d 172.17.0.1 -p tcp -m multiport --dports 4101,4102 -m comment --comment agent-preview-relay -j nixos-fw-accept
    '';
    networking.firewall.extraStopCommands = ''
      iptables -D nixos-fw -i br+ -d 172.17.0.1 -p tcp -m multiport --dports 4101,4102 -m comment --comment agent-preview-relay -j nixos-fw-accept 2>/dev/null || true
    '';

    # Retained state, including retired worker homes, remains backed up.
    services.restic.backups.agents = {
      initialize = true;
      repository = "/storage/backups/nas-restic";
      passwordFile = config.clan.core.vars.generators.nas-backup-password.files.secret.path;
      paths = [ "/var/lib/agent-backup-staging" ];
      pruneOpts = [
        "--keep-daily 14"
        "--keep-weekly 8"
        "--keep-monthly 6"
      ];
      timerConfig = {
        OnCalendar = "*-*-* 04:30:00";
        RandomizedDelaySec = "15m";
        Persistent = true;
      };
      backupPrepareCommand = ''
        ${pkgs.coreutils}/bin/install -d -m 700 /var/lib/agent-backup-staging
        status=0
        ${pkgs.rsync}/bin/rsync -a --delete \
          --exclude=node_modules --exclude=.cache --exclude=.next \
          --exclude=dist --exclude=result --exclude=logs \
          --exclude='*-wal' --exclude='*-shm' \
          --exclude='/previews/*/db' --exclude='/previews/*/objects' \
          ${lib.escapeShellArg cfg.stateDirectory}/ /var/lib/agent-backup-staging/ || status=$?
        # A file disappearing during a live sync is expected; other errors are not.
        if [ "$status" -ne 0 ] && [ "$status" -ne 24 ]; then exit "$status"; fi
        ${pkgs.python3}/bin/python3 ${../scripts/backup-agent-sqlite.py} \
          ${lib.escapeShellArg cfg.stateDirectory} /var/lib/agent-backup-staging
      '';
    };
    systemd.services.restic-backups-agents = {
      unitConfig.RequiresMountsFor = [
        "/storage/backups"
        cfg.stateDirectory
      ];
      serviceConfig = {
        Nice = 15;
        IOSchedulingClass = "idle";
        CPUWeight = 10;
        MemoryMax = "2G";
      };
    };
  };
}
