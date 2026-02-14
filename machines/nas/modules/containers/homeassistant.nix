{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.homeassistant = {
    image = "ghcr.io/home-assistant/home-assistant:2026.2.2";
    volumes = [
      "${cfg.storage.containerDataPath}/homeassistant:/config"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    extraOptions = [
      "--network=host"
    ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/homeassistant 0700 root root -"
  ];

  networking.firewall.allowedTCPPorts = [ 8123 ];
}
