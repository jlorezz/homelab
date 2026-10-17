{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab;
  secrets = "/var/lib/beszel-secrets";
  provision = ../scripts/provision-beszel.py;
  ciEnabled = config.qnoxslab.githubRunner.enable;
  monitoring = pkgs.writeText "beszel-monitoring.json" (builtins.toJSON {
    email = "admin@qnoxs.lab";
    systems = [
      { name = "NAS"; host = "127.0.0.1"; port = "45876"; primary = true; }
    ] ++ lib.optional ciEnabled {
      name = "NAS · CI containers"; host = "127.0.0.1"; port = "45877"; primary = false;
    };
    # HTTP endpoints avoid treating a listening but broken application as healthy.
    monitors = map (url: { target = url; protocol = "http"; port = 0; }) [
      "http://127.0.0.1:8090/api/health"
      "http://127.0.0.1:3001/"
      "http://127.0.0.1:2283/api/server/ping"
      "http://127.0.0.1:28981/"
      "http://127.0.0.1:3000/"
      "http://127.0.0.1:3030/"
      "http://127.0.0.1:8088/"
      "http://127.0.0.1:8123/"
      "http://127.0.0.1:9090/"
      "http://127.0.0.1:5050/"
      "http://127.0.0.1:3773/"
      "http://127.0.0.1:9119/"
    ] ++ map (port: { target = "127.0.0.1"; protocol = "tcp"; inherit port; }) [
      22 53 5432 5433 5984 9443
    ] ++ [
      { target = "192.168.2.1"; protocol = "tcp"; port = 80; }
      { target = "example.com"; protocol = "dns"; port = 0; }
    ];
    alerts = [
      { name = "Status"; value = 0; min = 2; }
      { name = "CPU"; value = 90; min = 10; }
      { name = "Memory"; value = 90; min = 5; }
      { name = "Disk"; value = 80; min = 5; }
      { name = "Temperature"; value = 80; min = 5; }
      { name = "CPUIOWait"; value = 25; min = 10; }
      { name = "ContainerHealth"; value = 0; min = 2; }
      { name = "SystemdFailed"; value = 0; min = 0; }
      { name = "NetworkMonitorLoss"; value = 20; min = 0; }
    ];
  });
  dockerProxy = pkgs.writeText "beszel-docker-proxy.cfg" ''
    global
      maxconn 64
    defaults
      mode http
      timeout connect 5s
      timeout client 30s
      timeout server 30s
    frontend host
      bind /run/beszel-docker/host.sock mode 660 user beszel-agent group beszel-agent
      acl read_method method GET HEAD
      acl read_path path_reg ^(/v[0-9.]+)?/(version|info|_ping|containers/json|containers/[a-f0-9]+/(json|stats|logs))$
      http-request deny unless read_method read_path
      default_backend host_engine
    backend host_engine
      server docker /run/docker.sock
    ${lib.optionalString ciEnabled ''
    frontend ci
      bind /run/beszel-docker/ci.sock mode 660 user beszel-agent group beszel-agent
      acl read_method method GET HEAD
      acl read_path path_reg ^(/v[0-9.]+)?/(version|info|_ping|containers/json|containers/[a-f0-9]+/(json|stats|logs))$
      http-request deny unless read_method read_path
      default_backend ci_engine
    backend ci_engine
      server docker /run/user/1102/docker.sock
    ''}
  '';
in
{
  services.beszel.hub = {
    enable = true;
    host = "127.0.0.1";
    port = 8090;
    environmentFile = "${secrets}/hub.env";
    environment = {
      APP_URL = "https://${cfg.tailscaleHostname}:8090";
      CHECK_UPDATES = "false";
      USER_CREATION = "false";
    };
  };
  services.beszel.agent = {
    enable = true;
    openFirewall = false;
    smartmon = {
      enable = true;
      deviceAllow = [ "/dev/sda" "/dev/sdb" "/dev/nvme0" "/dev/nvme0n1" ];
    };
    environment = {
      LISTEN = "127.0.0.1:45876";
      KEY_FILE = "/run/beszel-agent-key/hub.pub";
      DOCKER_HOST = "unix:///run/beszel-docker/host.sock";
      DOCKER_IMAGE_CHECK = "false";
      EXTRA_FILESYSTEMS = "/storage,/storage/obsidian,/storage/media,/storage/docker-dataset";
      DISK_USAGE_CACHE = "5m";
      SMART_INTERVAL = "1h";
      ZFS_INTERVAL = "15m";
      SKIP_SYSTEMD = false;
      GPU_COLLECTOR = [ "amd_sysfs" ];
      NICS = "${cfg.network.interface},tailscale0";
    };
  };

  # Track every native service, including backup/scrub jobs, and their cgroup usage.
  systemd.settings.Manager = {
    DefaultCPUAccounting = true;
    DefaultMemoryAccounting = true;
    DefaultIOAccounting = true;
  };
  systemd.services.beszel-secrets = {
    description = "Initialize private Beszel bootstrap credentials";
    before = [ "beszel-hub.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      StateDirectory = "beszel-secrets";
      StateDirectoryMode = "0700";
      UMask = "0077";
    };
    script = "${pkgs.python3}/bin/python3 ${provision} --prepare ${monitoring}";
  };
  systemd.services.beszel-hub = {
    requires = [ "beszel-secrets.service" ];
    after = [ "beszel-secrets.service" ];
    serviceConfig = { MemoryHigh = "384M"; MemoryMax = "768M"; CPUWeight = 25; };
  };
  systemd.services.beszel-provision = {
    description = "Reconcile Beszel systems, dashboard alerts and application probes";
    wantedBy = [ "multi-user.target" ];
    requires = [ "beszel-hub.service" ];
    after = [ "beszel-hub.service" ];
    before = [ "beszel-agent.service" "beszel-ci-agent.service" ];
    restartTriggers = [ monitoring provision ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      RuntimeDirectory = "beszel-agent-key";
      RuntimeDirectoryMode = "0755";
      UMask = "0077";
      TimeoutStartSec = "120s";
    };
    script = "${pkgs.python3}/bin/python3 ${provision} ${monitoring}";
  };
  systemd.services.beszel-docker-proxy = {
    description = "Allow only Docker monitoring requests through private Unix sockets";
    wantedBy = [ "multi-user.target" ];
    after = [ "docker.service" ] ++ lib.optional ciEnabled "user@1102.service";
    serviceConfig = {
      ExecStartPre = "${pkgs.haproxy}/bin/haproxy -c -f ${dockerProxy}";
      ExecStart = "${pkgs.haproxy}/bin/haproxy -db -f ${dockerProxy}";
      RuntimeDirectory = "beszel-docker";
      RuntimeDirectoryMode = "0755";
      Restart = "on-failure";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      # ProtectHome also hides /run/user, where the CI engine's socket lives.
      ProtectHome = "tmpfs";
      BindReadOnlyPaths = lib.optional ciEnabled "-/run/user/1102";
      PrivateTmp = true;
      CapabilityBoundingSet = [ "CAP_DAC_OVERRIDE" "CAP_CHOWN" "CAP_FOWNER" ];
      MemoryMax = "128M";
    };
  };
  systemd.services.beszel-agent = {
    requires = [ "beszel-provision.service" "beszel-docker-proxy.service" ];
    after = [ "beszel-provision.service" "beszel-docker-proxy.service" ];
    # The upstream module otherwise grants unrestricted Docker administration.
    serviceConfig.SupplementaryGroups = lib.mkForce [ "disk" ];
    serviceConfig.MemoryHigh = "192M";
    serviceConfig.MemoryMax = "384M";
    serviceConfig.CPUWeight = 25;
  };
  systemd.services.beszel-ci-agent = lib.mkIf ciEnabled {
    description = "Beszel view of the separate CI Docker engine (same NAS host)";
    wantedBy = [ "multi-user.target" ];
    requires = [ "beszel-provision.service" "beszel-docker-proxy.service" ];
    after = [ "beszel-provision.service" "beszel-docker-proxy.service" ];
    environment = {
      LISTEN = "127.0.0.1:45877";
      KEY_FILE = "/run/beszel-agent-key/hub.pub";
      DATA_DIR = "/var/lib/beszel-ci-agent";
      DOCKER_HOST = "unix:///run/beszel-docker/ci.sock";
      DOCKER_IMAGE_CHECK = "false";
      SKIP_SYSTEMD = "true";
      SKIP_GPU = "true";
      SMART_DEVICES = "";
      NICS = "";
    };
    serviceConfig = {
      ExecStart = "${config.services.beszel.agent.package}/bin/beszel-agent";
      User = "beszel-agent";
      Group = "beszel-agent";
      StateDirectory = "beszel-ci-agent";
      Restart = "on-failure";
      RestartSec = "10s";
      PrivateTmp = true;
      PrivateDevices = true;
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      CapabilityBoundingSet = "";
      MemoryMax = "256M";
      CPUWeight = 20;
    };
  };

  # Feed NAS-specific failures into Beszel's systemd service health view, including
  # stale backups (a successful old backup is otherwise easy to overlook).
  systemd.services.nas-health-check = {
    description = "NAS health: required ZFS mounts, pool state and backup freshness";
    after = [ "zfs-import.target" ];
    path = [ pkgs.util-linux config.boot.zfs.package pkgs.restic ];
    environment = {
      RESTIC_REPOSITORY = config.services.restic.backups.nas.repository;
      RESTIC_PASSWORD_FILE = config.services.restic.backups.nas.passwordFile;
    };
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${../scripts/check-nas-health.py}";
      TimeoutStartSec = "90s";
      Nice = 15;
      CPUWeight = 10;
      MemoryMax = "256M";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
    };
  };
  systemd.timers.nas-health-check = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "5m"; OnUnitActiveSec = "5m"; };
  };
}
