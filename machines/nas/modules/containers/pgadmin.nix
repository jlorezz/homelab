{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.pgadmin = {
    image = "dpage/pgadmin4:latest";
    ports = [ "5050:80" ];
    volumes = [
      "${cfg.storage.containerDataPath}/pgadmin:/var/lib/pgadmin"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    environmentFiles = [ "/var/lib/pgadmin-secrets/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/pgadmin 0700 5050 5050 -"
  ];

  networking.firewall.allowedTCPPorts = [ 5050 ];

  # Clan vars generators for pgAdmin credentials
  clan.core.vars.generators.pgadmin-email = {
    prompts.value = {
      description = "pgAdmin default login email address";
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

  clan.core.vars.generators.pgadmin-password = {
    prompts.value = {
      description = "pgAdmin default login password";
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
  systemd.services.pgadmin-secrets = {
    description = "Generate pgAdmin secrets environment file";
    wantedBy = [ "podman-pgadmin.service" ];
    before = [ "podman-pgadmin.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      mkdir -p /var/lib/pgadmin-secrets
      cat > /var/lib/pgadmin-secrets/secrets.env << EOF
      PGADMIN_DEFAULT_EMAIL=$(cat ${config.clan.core.vars.generators.pgadmin-email.files.secret.path})
      PGADMIN_DEFAULT_PASSWORD=$(cat ${config.clan.core.vars.generators.pgadmin-password.files.secret.path})
      EOF
      chmod 600 /var/lib/pgadmin-secrets/secrets.env
    '';
  };
}
