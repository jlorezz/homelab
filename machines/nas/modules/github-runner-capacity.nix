{ config, lib, ... }:
let
  cfg = config.qnoxslab.githubRunnerCapacity;
  pool = count: memoryMiB: cpuPercent: {
    count = lib.mkOption { type = lib.types.ints.between 0 4; default = count; };
    memoryMiB = lib.mkOption { type = lib.types.ints.positive; default = memoryMiB; };
    cpuPercent = lib.mkOption { type = lib.types.ints.positive; default = cpuPercent; };
  };
  deployment = config.qnoxslab.githubDeploymentRunners;
  production = deployment.enable && builtins.elem "production" deployment.environments;
  staging = deployment.enable && builtins.elem "staging" deployment.environments;
  preview = deployment.enable && builtins.any (e: lib.hasPrefix "preview-" e) deployment.environments;
  total = cfg.ciPool.memoryMiB
    + (if staging then cfg.staging.memoryMiB else 0)
    + (if production then 12288 else 0)
    + (if preview then cfg.previewPool.memoryMiB else 0);
in {
  options.qnoxslab.githubRunnerCapacity = {
    enable = lib.mkEnableOption "measured runner capacity and separate light-job admission";
    measuredHeadroomMiB = lib.mkOption { type = lib.types.nullOr lib.types.ints.positive; default = null; description = "UNVERIFIED until step 0: minimum MemAvailable; do not add existing pool usage back."; };
    measuredTotalMiB = lib.mkOption { type = lib.types.nullOr lib.types.ints.positive; default = null; };
    reserveMiB = lib.mkOption { type = lib.types.ints.positive; default = 8192; description = "Reserve at least max(8 GiB, 25 percent of physical RAM)."; };
    measuredCpuPercent = lib.mkOption { type = lib.types.nullOr lib.types.ints.positive; default = null; description = "100 times measured logical CPUs, minus operator reserve."; };
    committedAtMeasurementMiB = lib.mkOption { type = lib.types.nullOr lib.types.ints.positive; default = null; description = "Pool ceilings that were already live (and so already reflected in measuredHeadroomMiB) when it was sampled. When set, only ceilings beyond it must fit the headroom minus reserve; unset keeps the strict sum of all ceilings."; };
    committedAtMeasurementCpuPercent = lib.mkOption { type = lib.types.nullOr lib.types.ints.positive; default = null; description = "Same for CPU quotas. CPU quotas only slow work, so they are checked against measuredCpuPercent as an addition to the live ones."; };
    ciPool = pool 1 32768 800;
    ci = pool 1 8192 400;
    browser = pool 1 8192 400;
    static = pool 1 4096 200;
    engineReserveMiB = lib.mkOption { type = lib.types.ints.positive; default = 4096; };
    # The first 8/2/10 GiB ceilings OOM-killed every preview (2026-10-26): Next
    # static generation (4 workers, 359 pages) exceeded 8 GiB, and the frozen
    # monorepo `bun install` in publish exceeded 2 GiB (exit 137). Publish ran in
    # the 12 GiB deploy-staging container before. Peak RSS is still unmeasured.
    previewPool = pool 1 16384 400;
    previewBuild = pool 0 12288 400;
    previewPublish = pool 0 4096 100;
    staging = pool 1 8192 200;
    production = pool 1 12288 400;
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = config.qnoxslab.githubRunner.enable; message = "Measured capacity requires the rootless CI engine."; }
      { assertion = cfg.reserveMiB >= 8192; message = "Runner reserve must be at least 8 GiB."; }
      { assertion = builtins.length deployment.environments == builtins.length (lib.unique deployment.environments); message = "Deployment environments must be unique."; }
      { assertion = cfg.measuredHeadroomMiB != null && cfg.measuredTotalMiB != null && cfg.measuredCpuPercent != null; message = "Runner capacity inputs are UNVERIFIED: perform operator step 0 first."; }
      { assertion = cfg.measuredTotalMiB == null || cfg.reserveMiB * 4 >= cfg.measuredTotalMiB; message = "Runner reserve must be at least 25 percent of physical RAM."; }
      { assertion = cfg.measuredHeadroomMiB == null || (if cfg.committedAtMeasurementMiB == null then total else total - cfg.committedAtMeasurementMiB) + cfg.reserveMiB <= cfg.measuredHeadroomMiB; message = "Runner pool MemoryMax beyond what was live at measurement, plus reserve, exceeds measured headroom."; }
      { assertion = cfg.measuredCpuPercent == null || (cfg.ciPool.cpuPercent + (if preview then cfg.previewPool.cpuPercent else 0) + (if staging then cfg.staging.cpuPercent else 0) + (if production then 400 else 0)) - (if cfg.committedAtMeasurementCpuPercent == null then 0 else cfg.committedAtMeasurementCpuPercent) <= cfg.measuredCpuPercent; message = "Runner CPU ceilings beyond what was live at measurement exceed the measured CPU budget."; }
      { assertion = cfg.ci.count == 1 && cfg.browser.count == 1 && cfg.static.count >= 1; message = "Fixed-port CI/browser lanes must each remain singletons; scale socket-free static instead."; }
      { assertion = cfg.ciPool.count == 1 && cfg.previewPool.count == 1 && cfg.staging.count == 1 && cfg.production.count == 1 && cfg.production.memoryMiB == 12288 && cfg.production.cpuPercent == 400; message = "Aggregate pools are singletons; production resources are frozen for unit equivalence."; }
      { assertion = cfg.ciPool.memoryMiB >= cfg.ci.memoryMiB + cfg.browser.memoryMiB + cfg.static.count * cfg.static.memoryMiB + cfg.engineReserveMiB; message = "CI pool must cover all runner ceilings plus the engine/test-service reserve."; }
      { assertion = cfg.previewBuild.count <= 1 && cfg.previewPublish.count <= 1 && cfg.previewPool.memoryMiB >= cfg.previewBuild.count * cfg.previewBuild.memoryMiB + cfg.previewPublish.count * cfg.previewPublish.memoryMiB; message = "Preview aggregate must cover both registered workers, including idle listeners."; }
      { assertion = (cfg.previewBuild.count == 1) == (deployment.enable && builtins.elem "preview-build" deployment.environments) && (cfg.previewPublish.count == 1) == (deployment.enable && builtins.elem "preview-publish" deployment.environments); message = "Preview counts must match enabled deployment environments."; }
    ];
    environment.etc."dealpipe-runner-capacity.json".text = builtins.toJSON cfg;
    systemd.tmpfiles.rules = [ "f /var/lib/agent-workloads/static.lock 0666 root root -" ];
  };
}
