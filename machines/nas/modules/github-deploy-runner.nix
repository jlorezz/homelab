{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab.githubDeploymentRunners;
  docker = config.virtualisation.docker.package;
  controller = ../../../stacks/github-deploy-runner/controller.py;
  deniedIPv4 = "{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 }";
in
{
  options.qnoxslab.githubDeploymentRunners = {
    enable = lib.mkEnableOption "disposable Dealpipe release runners outside the PR engine";
    image = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "Immutable sha256 image ID printed by stacks/github-deploy-runner/build-image.py on the NAS.";
    };
    environments = lib.mkOption {
      type = lib.types.listOf (lib.types.enum [ "staging" "production" ]);
      default = [ "staging" ];
      description = "Enable production only after staging acceptance and human release configuration.";
    };
    credentialFile = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/dealpipe-deploy/github-token";
      description = "Root-only repository runner-registration credential; never a Nix store value.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = builtins.match "sha256:[0-9a-f]{64}" cfg.image != null;
        message = "Build the deployment runner image first and configure its immutable image ID."; }
      { assertion = config.qnoxslab.githubRunner.enable;
        message = "Deployment admission requires the existing CI workload lock and CI isolation."; }
    ];
    systemd.tmpfiles.rules = [ "d /var/lib/dealpipe-deploy 0700 root root -" ];
    # Same admission ceilings as one existing browser lane; deployment hooks take
    # the shared workload lock exclusively, so builds cannot overlap CI workloads.
    systemd.slices.dealpipe-deploy.sliceConfig = {
      CPUQuota = "400%";
      CPUWeight = 20;
      MemoryHigh = "12G";
      MemoryMax = "12G";
      MemorySwapMax = 0;
      TasksMax = 4096;
    };
    systemd.services = {
      dealpipe-deploy-network = {
        description = "Public-egress-only Docker network for disposable release workers";
        requires = [ "docker.service" ];
        after = [ "docker.service" ];
        serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
        path = [ docker pkgs.nftables ];
        script = ''
          nft list table inet dealpipe_deploy >/dev/null 2>&1 && nft delete table inet dealpipe_deploy
          nft -f - <<'RULES'
          table inet dealpipe_deploy {
            chain host_access {
              type filter hook input priority -10; policy accept;
              iifname "br-dp-deploy" counter reject
            }
            chain egress {
              type filter hook forward priority -10; policy accept;
              iifname "br-dp-deploy" ip daddr ${deniedIPv4} counter reject
              iifname "br-dp-deploy" meta nfproto ipv6 counter reject
            }
          }
          RULES
          docker --host unix:///var/run/docker.sock network inspect dealpipe-deploy >/dev/null 2>&1 || \
            docker --host unix:///var/run/docker.sock network create \
              --driver bridge --opt com.docker.network.bridge.name=br-dp-deploy \
              --opt com.docker.network.bridge.enable_icc=false \
              --subnet 172.31.254.0/28 dealpipe-deploy
          deployment_network=$(docker --host unix:///var/run/docker.sock network inspect \
            --format '{{.Driver}}:{{index .Options "com.docker.network.bridge.name"}}:{{index .Options "com.docker.network.bridge.enable_icc"}}' dealpipe-deploy)
          if [ "$deployment_network" != 'bridge:br-dp-deploy:false' ]; then
            echo 'Refusing an existing deployment network with unexpected isolation settings.' >&2
            exit 1
          fi
        '';
      };
    } // lib.listToAttrs (map (environment: lib.nameValuePair "dealpipe-deploy-${environment}" {
      description = "One-job disposable Dealpipe ${environment} runner controller";
      wantedBy = [ "multi-user.target" ];
      requires = [ "dealpipe-deploy-network.service" ];
      after = [ "dealpipe-deploy-network.service" ];
      unitConfig.ConditionPathExists = cfg.credentialFile;
      path = [ docker ];
      serviceConfig = {
        Type = "simple";
        LoadCredential = [ "github-token:${cfg.credentialFile}" ];
        ExecStart = "${pkgs.python3}/bin/python3 ${controller} --environment ${environment} --image ${cfg.image} --credential %d/github-token";
        Restart = "always";
        # A controller creates a fresh registration/container after each job.
        RestartSec = 10;
        UMask = "0077";
        ProtectHome = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        NoNewPrivileges = true;
      };
    }) cfg.environments);
  };
}
