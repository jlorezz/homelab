{ pkgs, lib, ... }:

let
  # Auto-discover all .nix files in this directory except default.nix
  containerFiles = lib.pipe (builtins.readDir ./.) [
    (lib.filterAttrs (name: type: type == "regular" && lib.hasSuffix ".nix" name && name != "default.nix"))
    (lib.mapAttrsToList (name: _: ./. + "/${name}"))
  ];
in
{
  imports = containerFiles;

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
