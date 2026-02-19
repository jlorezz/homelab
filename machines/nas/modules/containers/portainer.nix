{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.portainer = {
    image = "portainer/portainer-ce:2.38.1";
    ports = [ "127.0.0.1:9443:9443" ];
    volumes = [
      "/run/podman/podman.sock:/var/run/docker.sock:ro"
      "${cfg.storage.containerDataPath}/portainer:/data"
    ];
    environment = {
      TZ = cfg.timezone;
    };
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/portainer 0700 root root -"
  ];

}
