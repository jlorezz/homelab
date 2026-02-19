{ ... }:

{
  services.immich = {
    enable = true;
    host = "127.0.0.1";
    port = 2283;
    mediaLocation = "/storage/media/photos";
  };

  # Ensure the photos directory exists with proper permissions
  systemd.tmpfiles.rules = [
    "d /storage/media/photos 0750 immich immich -"
  ];
}
