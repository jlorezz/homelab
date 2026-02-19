{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.flaresolverr = {
    image = "ghcr.io/flaresolverr/flaresolverr:v3.4.6";
    ports = [ "127.0.0.1:8191:8191" ];
    environment = {
      LOG_LEVEL = "info";
      TZ = cfg.timezone;
    };
  };

}
