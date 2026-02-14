{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.excalidraw = {
    image = "excalidraw/excalidraw:latest";
    ports = [ "3030:80" ];
    environment = {
      TZ = cfg.timezone;
    };
  };

  networking.firewall.allowedTCPPorts = [ 3030 ];
}
