{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.jellyseerr = {
    image = "fallenbagel/jellyseerr:3.0.1";
    ports = [ "5055:5055" ];
    volumes = [
      "${cfg.storage.containerDataPath}/jellyseerr:/app/config"
    ];
    environment = {
      TZ = cfg.timezone;
      LOG_LEVEL = "info";
    };
    extraOptions = [
      "--memory=1g"
      "--cpus=1.0"
    ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/jellyseerr 0700 root root -"
  ];

  networking.firewall.allowedTCPPorts = [ 5055 ];
}
