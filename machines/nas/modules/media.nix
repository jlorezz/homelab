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

  # Storage layout configured in modules/defaults.nix via systemd.tmpfiles.rules
}
