{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.postgres = {
    image = "postgres:17";
    ports = [ "5433:5432" ];
    volumes = [
      "${cfg.storage.containerDataPath}/postgres:/var/lib/postgresql/data"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    environmentFiles = [ "/var/lib/postgres/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/postgres 0700 999 999 -"
  ];

  networking.firewall.allowedTCPPorts = [ 5433 ];

  # Clan vars generators for Postgres credentials
  clan.core.vars.generators.postgres-user = {
    prompts.value = {
      description = "PostgreSQL superuser username";
      type = "line";
    };
    files.secret = {
      secret = true;
      neededFor = "services";
    };
    script = ''
      cat $prompts/value > $out/secret
    '';
  };

  clan.core.vars.generators.postgres-password = {
    prompts.value = {
      description = "PostgreSQL superuser password";
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

  # Generate secrets environment file before container starts
  systemd.services.postgres-secrets = {
    description = "Generate PostgreSQL secrets environment file";
    wantedBy = [ "podman-postgres.service" ];
    before = [ "podman-postgres.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = let
      userPath = config.clan.core.vars.generators.postgres-user.files.secret.path;
      passPath = config.clan.core.vars.generators.postgres-password.files.secret.path;
    in ''
      mkdir -p /var/lib/postgres
      PG_USER=$(cat ${userPath})
      cat > /var/lib/postgres/secrets.env << EOF
      POSTGRES_USER=$PG_USER
      POSTGRES_PASSWORD=$(cat ${passPath})
      POSTGRES_DB=$PG_USER
      EOF
      chmod 600 /var/lib/postgres/secrets.env
    '';
  };
}
