{ config, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.linkding = {
    image = "sissbruecker/linkding:latest";
    ports = [ "9090:9090" ];
    volumes = [
      "/var/lib/linkding/data:/etc/linkding/data"
    ];
    environment = {
      TZ = cfg.timezone;
      LD_SERVER_PORT = "9090";
    };
    environmentFiles = [ "/var/lib/linkding/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/linkding 0700 root root -"
    "d /var/lib/linkding/data 0700 root root -"
  ];

  networking.firewall.allowedTCPPorts = [ 9090 ];

  # Secrets generators
  clan.core.vars.generators = {
    linkding-superuser-name = {
      prompts.value = {
        description = "Linkding superuser username";
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

    linkding-superuser-password = {
      prompts.value = {
        description = "Linkding superuser password";
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
  };

  # Generate secrets environment file before container starts
  systemd.services.linkding-secrets = {
    description = "Generate Linkding secrets environment file";
    wantedBy = [ "podman-linkding.service" ];
    before = [ "podman-linkding.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      mkdir -p /var/lib/linkding
      cat > /var/lib/linkding/secrets.env << EOF
      LD_SUPERUSER_NAME=$(cat ${config.clan.core.vars.generators.linkding-superuser-name.files.secret.path})
      LD_SUPERUSER_PASSWORD=$(cat ${config.clan.core.vars.generators.linkding-superuser-password.files.secret.path})
      EOF
      chmod 600 /var/lib/linkding/secrets.env
    '';
  };
}
