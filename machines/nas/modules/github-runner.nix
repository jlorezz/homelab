{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab.githubRunner;
  docker = config.virtualisation.docker.package;
  browserDaemonConfig = pkgs.writeText "dealpipe-browser-daemon.json" (builtins.toJSON {
    "data-root" = "/var/lib/dealpipe-ci/browser/docker";
    "dns" = [ "1.1.1.1" "9.9.9.9" ];
    "log-driver" = "local";
    "log-opts" = { "max-size" = "10m"; "max-file" = "3"; };
  });
in
{
  options.qnoxslab.githubRunner.enable = lib.mkEnableOption "dedicated rootless Docker engine for Dealpipe CI";
  config = lib.mkIf cfg.enable {
    users.groups.dealpipe-ci.gid = 1102;
    users.users.dealpipe-ci = {
      isSystemUser = true;
      uid = 1102;
      group = "dealpipe-ci";
      home = "/var/lib/dealpipe-ci";
      createHome = true;
      linger = true;
      subUidRanges = [ { startUid = 231072; count = 65536; } ];
      subGidRanges = [ { startGid = 231072; count = 65536; } ];
    };
    virtualisation.docker.rootless = {
      enable = true;
      setSocketVariable = false;
      daemon.settings = {
        "data-root" = "/var/lib/dealpipe-ci/docker";
        "dns" = [ "1.1.1.1" "9.9.9.9" ];
        "log-driver" = "local";
        "log-opts" = { "max-size" = "10m"; "max-file" = "3"; };
      };
    };
    # The distribution provides a user service globally; only CI may start it.
    systemd.user.services.docker.unitConfig.ConditionUser = lib.mkForce "dealpipe-ci";
    systemd.user.services.docker.environment.RES_OPTIONS = "timeout:1 attempts:1";
    # Docker 29.5+ otherwise gives --network=host the real NAS namespace.
    # Keep CI localhost and published test ports inside RootlessKit instead.
    systemd.user.services.docker.environment.DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS = "false";
    # No CI service needs inbound access from the NAS; publish only within CI.
    systemd.user.services.docker.environment.DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER = "none";

    # A second RootlessKit namespace keeps core database network changes out
    # of Chromium's network namespace. Both engines retain the CI user slice.
    systemd.user.services.docker-browser = {
      description = "Isolated rootless Docker engine for Dealpipe browser CI";
      wantedBy = [ "default.target" ];
      unitConfig = builtins.removeAttrs config.systemd.user.services.docker.unitConfig [ "Description" ];
      environment = config.systemd.user.services.docker.environment // {
        PATH = lib.mkForce config.systemd.user.services.docker.environment.PATH;
        XDG_RUNTIME_DIR = "%t/dealpipe-browser";
      };
      serviceConfig = config.systemd.user.services.docker.serviceConfig // {
        ExecStart = "${docker}/bin/dockerd-rootless --config-file=${browserDaemonConfig}";
        RuntimeDirectory = "dealpipe-browser";
        RuntimeDirectoryMode = "0700";
      };
      # RootlessKit's DNS proxy failed registry lookups during CI recovery.
      postStart = ''
        printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\n' > "$XDG_RUNTIME_DIR/dockerd-rootless/resolv.conf"
      '';
    };

    # Aggregate ceilings cover three CI lanes, the engine, and all test services.
    # These are admission limits, not measured build requirements.
    systemd.slices."user-1102".sliceConfig = {
      CPUQuota = "800%";
      CPUWeight = 20;
      MemoryHigh = "28G";
      MemoryMax = "32G";
      MemorySwapMax = 0;
      TasksMax = 8192;
      IOWeight = 20;
    };
    systemd.services."user@1102" = {
      overrideStrategy = "asDropin";
      requires = [ "dealpipe-ci-network.service" ];
      after = [ "dealpipe-ci-network.service" ];
      serviceConfig.Delegate = "cpu cpuset io memory pids";
    };
    systemd.services.dealpipe-ci-network = {
      description = "Restrict CI engine egress to public IPv4 destinations";
      wantedBy = [ "multi-user.target" ];
      before = [ "user@1102.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      path = [ pkgs.nftables ];
      script = ''
        nft list table inet dealpipe_ci >/dev/null 2>&1 && nft delete table inet dealpipe_ci
        nft -f - <<'RULES'
        table inet dealpipe_ci {
          chain egress {
            type filter hook output priority 0; policy accept;
            meta skuid 1102 ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 } counter reject
            meta skuid 1102 meta nfproto ipv6 counter reject
          }
        }
        RULES
      '';
      preStop = "${pkgs.nftables}/bin/nft delete table inet dealpipe_ci";
    };
    systemd.services.dealpipe-ci-ready = {
      description = "Wait for dedicated CI Docker socket before mounting it into Portainer";
      requires = [ "user@1102.service" ];
      after = [ "user@1102.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "90s"; };
      path = [ docker pkgs.coreutils ];
      script = ''
        # A bounded startup wait, independent of any build duration.
        timeout 80s ${pkgs.bash}/bin/bash -c 'until docker --host unix:///run/user/1102/docker.sock info >/dev/null 2>&1; do sleep 1; done'
      '';
    };
    systemd.services.dealpipe-ci-browser-ready = {
      description = "Wait for the isolated browser CI engine";
      requires = [ "user@1102.service" ];
      after = [ "user@1102.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "90s"; };
      path = [ docker pkgs.coreutils ];
      script = ''
        timeout 80s ${pkgs.bash}/bin/bash -c 'until docker --host unix:///run/user/1102/dealpipe-browser/docker.sock info >/dev/null 2>&1; do sleep 1; done'
      '';
    };
    # Portainer 2.45.2's Compose path ignores a non-default local Unix socket.
    # A dedicated agent makes Compose and direct API operations target the same
    # CI engine. The agent is trusted management infrastructure, not a CI job.
    systemd.services.portainer-management-network = {
      requires = [ "docker.service" ];
      after = [ "docker.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      path = [ docker ];
      script = ''
        docker network inspect portainer-management >/dev/null 2>&1 || docker network create portainer-management
      '';
    };
    virtualisation.oci-containers.containers = {
      portainer.extraOptions = [ "--network=portainer-management" ];
      portainer-ci-agent = {
        image = "docker.io/portainer/agent:2.45.2";
        volumes = [
          "/run/user/1102/docker.sock:/var/run/docker.sock:ro"
          "/var/lib/dealpipe-ci/docker/volumes:/var/lib/docker/volumes"
        ];
        extraOptions = [ "--network=portainer-management" "--memory=256m" "--memory-swap=256m" ];
      };
      portainer-ci-browser-agent = {
        image = "docker.io/portainer/agent:2.45.2";
        volumes = [
          "/run/user/1102/dealpipe-browser/docker.sock:/var/run/docker.sock:ro"
          "/var/lib/dealpipe-ci/browser/docker/volumes:/var/lib/docker/volumes"
        ];
        extraOptions = [ "--network=portainer-management" "--memory=256m" "--memory-swap=256m" ];
      };
    };
    systemd.services.docker-portainer-ci-agent = {
      requires = [ "dealpipe-ci-ready.service" "portainer-management-network.service" ];
      after = [ "dealpipe-ci-ready.service" "portainer-management-network.service" ];
    };
    systemd.services.docker-portainer-ci-browser-agent = {
      requires = [ "dealpipe-ci-browser-ready.service" "portainer-management-network.service" ];
      after = [ "dealpipe-ci-browser-ready.service" "portainer-management-network.service" ];
    };
    systemd.services.docker-portainer = {
      requires = [ "docker-portainer-ci-agent.service" ];
      after = [ "docker-portainer-ci-agent.service" ];
    };
    systemd.tmpfiles.rules = [
      "d /var/lib/dealpipe-ci 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/runner 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/home 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/work 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/secrets 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/browser/runner 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/browser/home 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/browser/work 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/static/runner 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/static/home 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/static/work 0700 dealpipe-ci dealpipe-ci -"
    ] ++ lib.optionals (!(config.qnoxslab.agents.enable or false)) [
      "d /var/lib/agent-workloads 0755 root root -"
      "f /var/lib/agent-workloads/heavy.lock 0666 root root -"
    ];
  };
}
