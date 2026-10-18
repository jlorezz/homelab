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
    };

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
