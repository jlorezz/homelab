{ config, pkgs, ... }:

let
  password = config.clan.core.vars.generators.nas-backup-password.files.secret.path;
in
{
  imports = [ ./offsite-backup.nix ];

  qnoxslab.offsiteBackup.enable = true;

  clan.core.vars.generators.nas-backup-password = {
    files.secret.secret = true;
    runtimeInputs = [ pkgs.openssl ];
    script = "openssl rand -hex 32 > $out/secret";
  };

  services.restic.backups.nas = {
    initialize = true;
    repository = "/storage/backups/nas-restic";
    passwordFile = password;
    paths = [
      "/storage/obsidian"
      "/storage/documents"
      "/storage/media/photos"
      "/var/lib/nas-backup-staging"
      "/var/lib/sops-nix"
      "/etc/nixos/homelab"
      "/var/lib/beszel-secrets"
      "/var/lib/beszel-agent"
      "/var/lib/beszel-ci-agent"
    ];
    exclude = [ "**/__pycache__" "**/.cache" "**/node_modules" "**/logs" ];
    pruneOpts = [ "--keep-daily 14" "--keep-weekly 8" "--keep-monthly 6" ];
    timerConfig = {
      OnCalendar = "*-*-* 03:30:00";
      RandomizedDelaySec = "15m";
      Persistent = true;
    };
    backupPrepareCommand = ''
      ${pkgs.util-linux}/bin/mountpoint -q /storage/obsidian
      ${pkgs.coreutils}/bin/install -d -m 700 /var/lib/nas-backup-staging
      pausedContainers=""
      # Stage hub keys/config and replace copied SQLite databases using the online
      # backup API, so retained metrics and alert history include committed WAL data.
      ${pkgs.coreutils}/bin/rm -rf /var/lib/nas-backup-staging/beszel-hub
      ${pkgs.coreutils}/bin/install -d -m 700 /var/lib/nas-backup-staging/beszel-hub
      # DynamicUser's StateDirectory is a symlink into /var/lib/private. Copy the
      # contents, never that symlink (which would point back at the live database).
      ${pkgs.coreutils}/bin/cp -a /var/lib/beszel-hub/. /var/lib/nas-backup-staging/beszel-hub/
      ${pkgs.python3}/bin/python3 ${../scripts/backup-agent-sqlite.py} \
        /var/lib/beszel-hub /var/lib/nas-backup-staging/beszel-hub
      resumeContainers() {
        for container in $pausedContainers; do
          ${pkgs.docker}/bin/docker unpause "$container" > /dev/null || true
        done
      }
      trap resumeContainers EXIT
      for container in homeassistant linkding pgadmin portainer stirling-pdf obsidian-couchdb obsidian-bridge; do
        if ${pkgs.docker}/bin/docker container inspect "$container" >/dev/null 2>&1; then
          ${pkgs.docker}/bin/docker pause "$container" > /dev/null
          pausedContainers="$pausedContainers $container"
        fi
      done
      for application in homeassistant linkding pgadmin portainer stirling-pdf paperless immich obsidian-couchdb obsidian-bridge; do
        ${pkgs.coreutils}/bin/rm -rf "/var/lib/nas-backup-staging/$application"
        ${pkgs.coreutils}/bin/cp -a "/var/lib/$application" /var/lib/nas-backup-staging/
      done
      resumeContainers
      pausedContainers=""
      # Replace the copied Paperless database with an online-consistent SQLite backup.
      ${pkgs.coreutils}/bin/rm -f /var/lib/nas-backup-staging/paperless/db.sqlite3{,-wal,-shm}
      ${pkgs.python3}/bin/python3 - <<'PYTHON'
      import sqlite3
      source = sqlite3.connect("file:/var/lib/paperless/db.sqlite3?mode=ro", uri=True)
      target = sqlite3.connect("/var/lib/nas-backup-staging/paperless/db.sqlite3")
      source.backup(target)
      target.close()
      source.close()
      PYTHON
      ${pkgs.util-linux}/bin/runuser -u postgres -- \
        ${config.services.postgresql.package}/bin/pg_dumpall \
        > /var/lib/nas-backup-staging/postgresql-native.sql
      ${pkgs.docker}/bin/docker exec postgres sh -c \
        'pg_dumpall -U "$POSTGRES_USER"' \
        > /var/lib/nas-backup-staging/postgresql-container.sql
      ${pkgs.coreutils}/bin/chmod 600 /var/lib/nas-backup-staging/*.sql
    '';
  };

  systemd.services.restic-backups-nas = {
    requires = [ "zfs-import.target" ];
    after = [ "zfs-import.target" "postgresql.service" "docker-postgres.service" ];
    unitConfig.RequiresMountsFor = [ "/storage" "/storage/obsidian" ];
    serviceConfig = {
      Nice = 15;
      IOSchedulingClass = "idle";
      CPUWeight = 10;
      MemoryMax = "2G";
    };
  };
  systemd.tmpfiles.rules = [
    "d /storage/backups 0700 root root -"
    "d /var/lib/nas-backup-staging 0700 root root -"
  ];
  services.zfs.autoSnapshot.monthly = 6;
  environment.systemPackages = [
    pkgs.restic
    (pkgs.writeShellScriptBin "restic-nas" ''
      export RESTIC_REPOSITORY=/storage/backups/nas-restic
      export RESTIC_PASSWORD_FILE=${password}
      exec ${pkgs.restic}/bin/restic "$@"
    '')
  ];
}
