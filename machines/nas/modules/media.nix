{ ... }:

{
  # Shared media group for permission management
  users.groups.media = { };

  # ===========================================
  # Media Server
  # ===========================================

  services.jellyfin = {
    enable = true;
    openFirewall = true; # :8096
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
    openFirewall = true; # :7878
  };
  users.users.radarr.extraGroups = [ "media" ];

  # Sonarr - TV Show Management
  services.sonarr = {
    enable = true;
    openFirewall = true; # :8989
  };
  users.users.sonarr.extraGroups = [ "media" ];

  # Prowlarr - Indexer Management
  services.prowlarr = {
    enable = true;
    openFirewall = true; # :9696
  };

  # ===========================================
  # Download Client
  # ===========================================

  services.qbittorrent = {
    enable = true;
    openFirewall = true;
    webuiPort = 8080;
    torrentingPort = 50000;
  };
  users.users.qbittorrent.extraGroups = [ "media" ];

  # Storage layout configured in modules/defaults.nix via systemd.tmpfiles.rules
}
