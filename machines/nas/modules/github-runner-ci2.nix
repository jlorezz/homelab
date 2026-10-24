{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab.githubRunnerCI2;
  docker = config.virtualisation.docker.rootless.package;
  # Only the official rootless script's child may prepare canonical paths.
  # RootlessKit tmpfs+symlink copy-up keeps the physical host directories intact.
  namespaceScript = ''
    set -eu
    fail() { echo "CI2 namespace setup refused: $*" >&2; exit 1; }
    [ "''${_DOCKERD_ROOTLESS_CHILD:-}" = 1 ] || fail "not a RootlessKit child"
    [ "$(${pkgs.coreutils}/bin/id -ru)" = 0 ] || fail "real UID must be namespace root"
    for ns in user mnt; do
      ours=$(${pkgs.coreutils}/bin/readlink "/proc/self/ns/$ns")
      host=$(${pkgs.coreutils}/bin/readlink "/proc/1/ns/$ns")
      [ -n "$ours" ] && [ -n "$host" ] && [ "$ours" != "$host" ] || fail "host $ns namespace"
    done
    base=/var/lib/dealpipe-ci
    # Require an exact private/slave tmpfs mount, not a directory on host storage.
    [ "$(${pkgs.util-linux}/bin/findmnt -rn -o TARGET,FSTYPE --mountpoint "$base")" = "$base tmpfs" ] || fail "missing copy-up tmpfs"
    propagation=$(${pkgs.util-linux}/bin/findmnt -rn -o PROPAGATION --mountpoint "$base")
    case "$propagation" in private|slave) ;; *) fail "outward mount propagation" ;; esac
    # Validate BOTH entries and their common copy-up backing before any mutation.
    [ -L "$base/ci2" ] || fail "missing ci2 copy-up entry"
    backing=$(${pkgs.coreutils}/bin/readlink "$base/ci2")
    case "$backing" in .ro*/ci2) ;; *) fail "unexpected ci2 copy-up target" ;; esac
    backing="''${backing%/ci2}"
    case "$backing" in */*) fail "nested copy-up backing" ;; esac
    for name in docker work; do
      [ -L "$base/$name" ] || fail "canonical $name is not a copy-up symlink"
      [ "$(${pkgs.coreutils}/bin/readlink "$base/$name")" = "$backing/$name" ] || fail "unexpected $name copy-up target"
      [ -d "$base/ci2/$name" ] && [ ! -L "$base/ci2/$name" ] || fail "invalid physical $name source"
    done
    # These are private tmpfs symlinks only; never recursively remove a path.
    for name in docker work; do
      ${pkgs.coreutils}/bin/unlink "$base/$name"
      ${pkgs.coreutils}/bin/mkdir "$base/$name"
      ${pkgs.util-linux}/bin/mount --bind "$base/ci2/$name" "$base/$name"
      ${pkgs.util-linux}/bin/mount --make-private "$base/$name"
      sourceIdentity=$(${pkgs.coreutils}/bin/stat -Lc '%d:%i' "$base/ci2/$name")
      targetIdentity=$(${pkgs.coreutils}/bin/stat -Lc '%d:%i' "$base/$name")
      [ -n "$sourceIdentity" ] && [ "$sourceIdentity" = "$targetIdentity" ] || fail "bind identity mismatch"
      ${pkgs.util-linux}/bin/findmnt -rn --mountpoint "$base/$name" >/dev/null || fail "missing bind mount"
    done
    exec ${docker}/bin/dockerd "$@"
  '';
  namespaceSetup = pkgs.writeShellScript "ci2-dockerd-namespace" namespaceScript;
  daemonSettings = {
    "data-root" = "/var/lib/dealpipe-ci/docker";
    "exec-root" = "/run/user/1102/dealpipe-ci2/exec";
    "pidfile" = "/run/user/1102/dealpipe-ci2/dockerd.pid";
    "hosts" = [ "unix:///run/user/1102/dealpipe-ci2/docker.sock" ];
    "exec-opts" = [ "native.cgroupdriver=systemd" ];
    "cgroup-parent" = "dealpipe-ci2.slice";
    "dns" = [ "1.1.1.1" "9.9.9.9" ];
    "log-driver" = "local";
    "log-opts" = { "max-size" = "10m"; "max-file" = "3"; };
  };
in {
  options.qnoxslab.githubRunnerCI2 = {
    enable = lib.mkEnableOption "experimental isolated second CI engine (requires measured admission rollout)";
    engineMemoryMiB = lib.mkOption {
      type = lib.types.ints.positive;
      default = 16384;
      description = "UNVERIFIED containment ceiling including runner/test services, not measured demand.";
    };
    engineCpuPercent = lib.mkOption { type = lib.types.ints.positive; default = 400; };
    namespaceSetupScript = lib.mkOption {
      type = lib.types.str;
      readOnly = true;
      internal = true;
      default = namespaceScript;
      description = "Evaluable child wrapper text; no store realization or import from derivation.";
    };
    daemonSettings = lib.mkOption {
      type = lib.types.attrs;
      readOnly = true;
      internal = true;
      default = daemonSettings;
      description = "Evaluable source of the exact daemon JSON, without realizing a store file.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = config.qnoxslab.githubRunner.enable; message = "CI2 requires existing UID1102 isolation and egress policy."; }
      { assertion = config.qnoxslab.githubRunnerCapacity.ciPool.memoryMiB == 32768
          && config.qnoxslab.githubRunnerCapacity.ciPool.cpuPercent == 800;
        message = "CI2 must share the unchanged 32 GiB / 8 CPU parent."; }
      { assertion = cfg.engineMemoryMiB <= 32768 && cfg.engineCpuPercent <= 800;
        message = "CI2 child cannot exceed the existing parent."; }
    ];
    systemd.user.slices.dealpipe-ci2.sliceConfig = {
      MemoryMax = "${toString cfg.engineMemoryMiB}M";
      MemorySwapMax = 0;
      CPUQuota = "${toString cfg.engineCpuPercent}%";
      TasksMax = 4096;
    };
    systemd.user.services.docker-ci2 = {
      description = "Opt-in isolated rootless CI2 engine; no permanent worker lifecycle management";
      wantedBy = [ "default.target" ];
      unitConfig.ConditionUser = "dealpipe-ci";
      # Operator must drain before any explicit restart. No automatic replacement
      # of a daemon containing a permanent Runner.Worker during source activation.
      restartIfChanged = false;
      stopIfChanged = false;
      # Match pinned nixpkgs docker-rootless: newuidmap/newgidmap must use
      # the privileged wrappers, never the unprivileged shadow binaries.
      path = [ "/run/wrappers" docker pkgs.rootlesskit pkgs.slirp4netns pkgs.fuse-overlayfs pkgs.iproute2 ];
      environment = {
        XDG_RUNTIME_DIR = "%t/dealpipe-ci2";
        DOCKERD = "${namespaceSetup}";
        DOCKERD_ROOTLESS_ROOTLESSKIT_FLAGS = "--copy-up=/var/lib/dealpipe-ci";
        DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR = "/run/user/1102/dealpipe-ci2/rootlesskit";
        DOCKERD_ROOTLESS_ROOTLESSKIT_NET = "slirp4netns";
        DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER = "none";
        DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS = "false";
        RES_OPTIONS = "timeout:1 attempts:1";
      };
      postStart = ''
        printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\n' > "$XDG_RUNTIME_DIR/dockerd-rootless/resolv.conf"
      '';
      serviceConfig = {
        Type = "notify";
        NotifyAccess = "all";
        Slice = "dealpipe-ci2.slice";
        Delegate = "cpu cpuset io memory pids";
        RuntimeDirectory = "dealpipe-ci2";
        RuntimeDirectoryMode = "0700";
        # Explicit separate config: never inherit the main engine daemon.json.
        ExecStart = "${docker}/bin/dockerd-rootless --config-file=${pkgs.writeText "ci2-daemon.json" (builtins.toJSON cfg.daemonSettings)}";
      };
    };
    systemd.tmpfiles.rules = [
      "d /var/lib/dealpipe-ci/ci2 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/ci2/docker 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/ci2/runner 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/ci2/home 0700 dealpipe-ci dealpipe-ci -"
      "d /var/lib/dealpipe-ci/ci2/work 0700 dealpipe-ci dealpipe-ci -"
      "d /etc/dealpipe-ci-admission 0755 root root -"
      "d /var/lib/agent-workloads/ci-admission 0700 dealpipe-ci dealpipe-ci -"
      "f /var/lib/agent-workloads/ci-admission/mutex.lock 0600 dealpipe-ci dealpipe-ci -"
      "f /var/lib/agent-workloads/ci-main.lock 0666 root root -"
      "f /var/lib/agent-workloads/ci-browser.lock 0666 root root -"
      "f /var/lib/agent-workloads/ci-2.lock 0666 root root -"
    ];
  };
}
