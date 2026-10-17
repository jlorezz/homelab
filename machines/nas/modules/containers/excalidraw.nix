{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.excalidraw = {
    # Upstream publishes latest/commit tags rather than release-version tags.
    image = "docker.io/excalidraw/excalidraw@sha256:f7ee194addd607bf831d2af0f0a34463dd4225e426cf35199ef0b12a803398e9";
    ports = [ "127.0.0.1:3030:80" ];
    environment = {
      TZ = cfg.timezone;
    };
  };

}
