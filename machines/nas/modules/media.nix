{ ... }:

{
  # Shared media group for permission management
  users.groups.media = { };

  # ===========================================
  # Media Server
  # ===========================================

  services.jellyfin = {
    enable = true;
  };

  users.users.jellyfin.extraGroups = [
    "video"
    "render"
    "media"
  ];

  # ===========================================
  # Arr Stack - Media Management
  # ===========================================

  # Radarr - Movie Management
  services.radarr = {
    enable = true;
  };
  users.users.radarr.extraGroups = [ "media" ];

  # Sonarr - TV Show Management
  services.sonarr = {
    enable = true;
  };
  users.users.sonarr.extraGroups = [ "media" ];

  # Prowlarr - Indexer Management
  services.prowlarr = {
    enable = true;
  };

  # ===========================================
  # Download Client
  # ===========================================

  services.qbittorrent = {
    enable = true;
    webuiPort = 8080;
    torrentingPort = 50000;
  };
  users.users.qbittorrent.extraGroups = [ "media" ];

  # Storage layout configured in modules/defaults.nix via systemd.tmpfiles.rules
}
