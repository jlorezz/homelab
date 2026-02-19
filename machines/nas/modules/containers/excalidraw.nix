{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.excalidraw = {
    image = "excalidraw/excalidraw:0.18.0";
    ports = [ "127.0.0.1:3030:80" ];
    environment = {
      TZ = cfg.timezone;
    };
  };

}
