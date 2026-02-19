{ config, ... }:

let
  cfg = config.qnoxslab;
  mkClanSecret = import ../../lib/mkClanSecret.nix;
  mkSecretsEnv = import ../../lib/mkSecretsEnv.nix { inherit config; };
in
{
  virtualisation.oci-containers.containers.postgres = {
    image = "postgres:17";
    ports = [ "127.0.0.1:5433:5432" ];
    volumes = [
      "${cfg.storage.containerDataPath}/postgres:/var/lib/postgresql/data"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    environmentFiles = [ "/run/postgres-secrets/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/postgres 0700 999 999 -"
  ];

  clan.core.vars.generators =
    mkClanSecret {
      name = "postgres-user";
      description = "PostgreSQL superuser username";
      promptType = "line";
    }
    // mkClanSecret {
      name = "postgres-password";
      description = "PostgreSQL superuser password";
    };
}
// mkSecretsEnv {
  name = "postgres";
  vars = {
    POSTGRES_USER = "postgres-user";
    POSTGRES_PASSWORD = "postgres-password";
  };
  extraLines = [
    "POSTGRES_DB=$(cat ${config.clan.core.vars.generators.postgres-user.files.secret.path})"
  ];
}
