{ config, ... }:

let
  cfg = config.qnoxslab;
  mkClanSecret = import ../lib/mkClanSecret.nix;
in
{
  services.paperless = {
    enable = true;
    address = "127.0.0.1";
    port = 28981;
    mediaDir = "/storage/documents/media";
    consumptionDir = "/storage/documents/consume";
    passwordFile = config.clan.core.vars.generators.paperless-admin-password.files.secret.path;
    settings = {
      PAPERLESS_TIME_ZONE = cfg.timezone;
      PAPERLESS_OCR_LANGUAGE = "deu+eng";
      PAPERLESS_ADMIN_USER = "admin";
    };
  };

  # Ensure storage directories exist on ZFS pool
  systemd.tmpfiles.rules = [
    "d /storage/documents 0750 paperless paperless -"
    "d /storage/documents/media 0750 paperless paperless -"
    "d /storage/documents/consume 0750 paperless paperless -"
  ];

  clan.core.vars.generators = mkClanSecret {
    name = "paperless-admin-password";
    description = "Paperless-ngx admin password";
  };
}
