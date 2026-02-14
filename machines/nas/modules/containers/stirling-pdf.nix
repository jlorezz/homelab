{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.stirling-pdf = {
    image = "stirlingtools/stirling-pdf:2.4.6";
    ports = [ "8088:8080" ];
    volumes = [
      "${cfg.storage.containerDataPath}/stirling-pdf/data:/usr/share/tessdata"
      "${cfg.storage.containerDataPath}/stirling-pdf/config:/configs"
      "${cfg.storage.containerDataPath}/stirling-pdf/logs:/logs"
    ];
    environment = {
      TZ = cfg.timezone;
      SECURITY_ENABLELOGIN = "false";
      SECURITY_CSRF_DISABLED = "true";
    };
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/stirling-pdf 0700 root root -"
    "d ${cfg.storage.containerDataPath}/stirling-pdf/data 0700 root root -"
    "d ${cfg.storage.containerDataPath}/stirling-pdf/config 0700 root root -"
    "d ${cfg.storage.containerDataPath}/stirling-pdf/logs 0700 root root -"
  ];

  networking.firewall.allowedTCPPorts = [ 8088 ];
}
