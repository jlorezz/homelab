{ config, lib, pkgs, ... }:

let
  arcMax = 8 * 1024 * 1024 * 1024;
  containerMemory = {
    homepage = "512m";
    postgres = "2g";
    portainer = "512m";
    pgadmin = "1g";
    linkding = "1g";
    excalidraw = "256m";
    homeassistant = "2g";
    stirling-pdf = "3g";
  };
in
{
  powerManagement = {
    enable = true;
    cpuFreqGovernor = "powersave";
  };

  # Preserve responsive boost; ask AMD CPPC to favor energy efficiency.
  systemd.services.nas-efficiency = {
    description = "Set NAS CPU energy preference and ZFS cache budget";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-modules-load.service" "zfs-import.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      for preference in /sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference; do
        [ -w "$preference" ] || continue
        printf 'balance_power\n' > "$preference"
      done
      if [ -w /sys/module/zfs/parameters/zfs_arc_max ]; then
        printf '${toString arcMax}\n' > /sys/module/zfs/parameters/zfs_arc_max
      fi
    '';
  };
  boot.extraModprobeConfig = "options zfs zfs_arc_max=${toString arcMax}";

  # Allocates physical RAM only when pages are actually swapped.
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 10;
    memoryMax = 4 * 1024 * 1024 * 1024;
  };

  virtualisation.oci-containers.containers = lib.mapAttrs (_: memory: {
    extraOptions = [ "--memory=${memory}" "--memory-swap=${memory}" ];
  }) containerMemory;

  systemd.services = {
    immich-machine-learning.serviceConfig = {
      MemoryHigh = "3G";
      MemoryMax = "6G";
      CPUWeight = 50;
    };
    immich-server.serviceConfig = {
      MemoryHigh = "3G";
      MemoryMax = "6G";
    };
    paperless-task-queue.serviceConfig.CPUWeight = 50;
  };

  services.journald.settings.Journal = {
    SystemMaxUse = "512M";
    RuntimeMaxUse = "64M";
    MaxRetentionSec = "30day";
  };
  services.fstrim.enable = true;
  services.smartd = {
    enable = true;
    autodetect = true;
    defaults.monitored = "-a -n standby,q";
  };

  nix = {
    settings = {
      max-jobs = 2;
      cores = 4;
      auto-optimise-store = true;
    };
    gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 30d";
    };
  };

  environment.systemPackages = with pkgs; [ powertop smartmontools ];
}
