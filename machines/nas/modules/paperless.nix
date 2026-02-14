{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  services.paperless = {
    enable = true;
    address = "0.0.0.0";
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

  networking.firewall.allowedTCPPorts = [ 28981 ];

  # Admin password secret via Clan vars
  clan.core.vars.generators.paperless-admin-password = {
    prompts.value = {
      description = "Paperless-ngx admin password";
      type = "hidden";
    };
    files.secret = {
      secret = true;
      neededFor = "services";
    };
    script = ''
      cat $prompts/value > $out/secret
    '';
  };
}
