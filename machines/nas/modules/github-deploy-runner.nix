{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab.githubDeploymentRunners;
  docker = config.virtualisation.docker.package;
  capacity = config.qnoxslab.githubRunnerCapacity;
  controller = ../../../stacks/github-deploy-runner/controller.py;
  isolatedController = ../../../stacks/github-preview-runner/controller.py;
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
    previewImage = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "Separately built immutable preview image; never changes the production image.";
    };
    environments = lib.mkOption {
      type = lib.types.listOf (lib.types.enum [ "staging" "production" "preview-build" "preview-publish" ]);
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
      { assertion = capacity.enable;
        message = "Reconcile live deployment drift and measure capacity before enabling controllers."; }
      { assertion = !(builtins.any (e: lib.hasPrefix "preview-" e) cfg.environments) || builtins.match "sha256:[0-9a-f]{64}" cfg.previewImage != null;
        message = "Build and pin the separate preview image before enabling preview roles."; }
      { assertion = builtins.match "sha256:[0-9a-f]{64}" cfg.image != null;
        message = "Build the deployment runner image first and configure its immutable image ID."; }
      { assertion = config.qnoxslab.githubRunner.enable;
        message = "Deployment admission requires the existing CI workload lock and CI isolation."; }
    ];
    systemd.tmpfiles.rules = [
      "d /var/lib/dealpipe-deploy 0700 root root -"
      "f /var/lib/agent-workloads/preview.lock 0666 root root -"
      "f /var/lib/agent-workloads/deploy.lock 0666 root root -"
    ];
    # Production slice and original controller stay unchanged. Staging has its own budget.
    systemd.slices.dealpipe-deploy.sliceConfig = {
      CPUQuota = "400%";
      CPUWeight = 20;
      MemoryHigh = "12G";
      MemoryMax = "12G";
      MemorySwapMax = 0;
      TasksMax = 4096;
    };
    systemd.slices.dealpipe-staging.sliceConfig = {
      CPUQuota = "${toString capacity.staging.cpuPercent}%";
      CPUWeight = 20;
      MemoryHigh = "${toString capacity.staging.memoryMiB}M";
      MemoryMax = "${toString capacity.staging.memoryMiB}M";
      MemorySwapMax = 0;
      TasksMax = 4096;
    };
    systemd.slices.dealpipe-preview.sliceConfig = {
      CPUQuota = "${toString capacity.previewPool.cpuPercent}%";
      CPUWeight = 20;
      MemoryHigh = "${toString capacity.previewPool.memoryMiB}M";
      MemoryMax = "${toString capacity.previewPool.memoryMiB}M";
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
        ExecStart = "${pkgs.python3}/bin/python3 ${if environment == "production" then controller else isolatedController} --environment ${environment} --image ${if lib.hasPrefix "preview-" environment then cfg.previewImage else cfg.image} --credential %d/github-token${lib.optionalString (environment != "production") (let limits = if environment == "staging" then capacity.staging else if environment == "preview-build" then capacity.previewBuild else capacity.previewPublish; in " --memory-mib ${toString limits.memoryMiB} --cpu-percent ${toString limits.cpuPercent}")}";
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
