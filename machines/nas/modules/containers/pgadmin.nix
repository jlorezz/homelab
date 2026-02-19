{ config, ... }:

let
  cfg = config.qnoxslab;
  mkClanSecret = import ../../lib/mkClanSecret.nix;
  mkSecretsEnv = import ../../lib/mkSecretsEnv.nix { inherit config; };
in
{
  virtualisation.oci-containers.containers.pgadmin = {
    image = "dpage/pgadmin4:9.12";
    ports = [ "127.0.0.1:5050:80" ];
    volumes = [
      "${cfg.storage.containerDataPath}/pgadmin:/var/lib/pgadmin"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    environmentFiles = [ "/run/pgadmin-secrets/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/pgadmin 0700 5050 5050 -"
  ];

  clan.core.vars.generators =
    mkClanSecret {
      name = "pgadmin-email";
      description = "pgAdmin default login email address";
      promptType = "line";
    }
    // mkClanSecret {
      name = "pgadmin-password";
      description = "pgAdmin default login password";
    };
}
// mkSecretsEnv {
  name = "pgadmin";
  vars = {
    PGADMIN_DEFAULT_EMAIL = "pgadmin-email";
    PGADMIN_DEFAULT_PASSWORD = "pgadmin-password";
  };
}
