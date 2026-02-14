{ ... }:

{
  services.immich = {
    enable = true;
    host = "0.0.0.0";
    port = 2283;
    openFirewall = true;
    mediaLocation = "/storage/media/photos";
  };

  # Ensure the photos directory exists with proper permissions
  systemd.tmpfiles.rules = [
    "d /storage/media/photos 0750 immich immich -"
  ];
}
