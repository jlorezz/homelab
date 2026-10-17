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

  virtualisation.docker = {
    enable = true;
    enableOnBoot = true;
    liveRestore = true;
    daemon.settings = {
      "log-driver" = "local";
      "log-opts" = { "max-size" = "10m"; "max-file" = "3"; };
    };
  };
  virtualisation.podman.enable = false;
  virtualisation.oci-containers.backend = "docker";
}
