{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab.offsiteBackup;
  source = ../../../stacks/backups;
  template = builtins.fromJSON (builtins.readFile (source + /homelab.json));
  manifest = pkgs.writeText "homelab-offsite.json" (builtins.toJSON (template // {
    destination_repository = cfg.repository;
    source_password_file = config.clan.core.vars.generators.nas-backup-password.files.secret.path;
    restic = "${pkgs.restic}/bin/restic";
  }));
  command = action: "${pkgs.python3}/bin/python3 ${source}/offsite.py ${action} --config ${manifest}";
  guarded = action: "${pkgs.python3}/bin/python3 ${../../../stacks/agents/workload.py} --lock /var/lib/agent-workloads/heavy.lock run --timeout 7200 -- ${command action}";
  service = action: {
    description = "Homelab offsite Restic ${action}";
    after = [ "network-online.target" "restic-backups-nas.service" "restic-backups-agents.service" ];
    wants = [ "network-online.target" ];
    unitConfig.RequiresMountsFor = [ "/storage/backups" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = guarded action;
      EnvironmentFile = cfg.credentialsFile;
      StateDirectory = "offsite-backup";
      StateDirectoryMode = "0700";
      UMask = "0077";
      Nice = 15;
      IOSchedulingClass = "idle";
      CPUQuota = "25%";
      MemoryMax = "1G";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/offsite-backup" "/storage/backups/nas-restic" "/var/lib/agent-workloads/heavy.lock" ];
      TimeoutStartSec = "3h";
    };
  };
in {
  options.qnoxslab.offsiteBackup = {
    enable = lib.mkEnableOption "independent encrypted EU B2 backups";
    repository = lib.mkOption { type = lib.types.str; default = template.destination_repository; description = "Actual EU B2 S3 URL including bucket and homelab prefix; no credentials."; };
    credentialsFile = lib.mkOption { type = lib.types.str; default = "/var/lib/offsite-backup/secrets/homelab.env"; description = "Root-owned 0600 AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY environment file."; };
  };
  config = lib.mkIf cfg.enable {
    assertions = [{ assertion = cfg.repository != "" && !(lib.hasInfix "REPLACE" cfg.repository); message = "Configure the actual private EU B2 repository before enabling offsite backup."; }];
    systemd.tmpfiles.rules = [ "d /var/lib/offsite-backup 0700 root root -" "d /var/lib/offsite-backup/secrets 0700 root root -" "d /var/lib/offsite-backup/state 0700 root root -" ];
    systemd.services.offsite-copy = service "copy";
    systemd.services.offsite-check = service "check";
    systemd.services.offsite-restore-test = service "restore-test";
    systemd.timers.offsite-copy = { wantedBy = [ "timers.target" ]; timerConfig = { OnCalendar = "*-*-* 05:30:00"; Persistent = true; RandomizedDelaySec = "15m"; }; };
    systemd.timers.offsite-check = { wantedBy = [ "timers.target" ]; timerConfig = { OnCalendar = "Sun *-*-* 07:00:00"; Persistent = true; }; };
    systemd.timers.offsite-restore-test = { wantedBy = [ "timers.target" ]; timerConfig = { OnCalendar = "*-*-01 08:00:00"; Persistent = true; }; };
    # The existing NAS health probe exposes failures through Beszel's systemd view.
    systemd.services.nas-health-check.environment.OFFSITE_BACKUP_STATUS = "/var/lib/offsite-backup/state/status.json";
  };
}
