{ pkgs, ... }:

{
  # Enable Podman as container backend
  virtualisation.podman = {
    enable = true;
    dockerCompat = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  virtualisation.oci-containers.backend = "podman";

  virtualisation.containers.containersConf.settings = {
    engine.helper_binaries_dir = [ "${pkgs.netavark}/bin" ];
  };

  # Ensure podman socket is available
  systemd.sockets.podman.wantedBy = [ "sockets.target" ];
}
