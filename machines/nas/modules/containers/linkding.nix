{ config, ... }:

let
  cfg = config.qnoxslab;
  mkClanSecret = import ../../lib/mkClanSecret.nix;
  mkSecretsEnv = import ../../lib/mkSecretsEnv.nix { inherit config; };
in
{
  virtualisation.oci-containers.containers.linkding = {
    image = "sissbruecker/linkding:1.45.0";
    ports = [ "127.0.0.1:9090:9090" ];
    volumes = [
      "/var/lib/linkding/data:/etc/linkding/data"
    ];
    environment = {
      TZ = cfg.timezone;
      LD_SERVER_PORT = "9090";
    };
    environmentFiles = [ "/run/linkding-secrets/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/linkding 0700 root root -"
    "d /var/lib/linkding/data 0700 root root -"
  ];

  clan.core.vars.generators =
    mkClanSecret {
      name = "linkding-superuser-name";
      description = "Linkding superuser username";
      promptType = "line";
    }
    // mkClanSecret {
      name = "linkding-superuser-password";
      description = "Linkding superuser password";
    };
}
// mkSecretsEnv {
  name = "linkding";
  vars = {
    LD_SUPERUSER_NAME = "linkding-superuser-name";
    LD_SUPERUSER_PASSWORD = "linkding-superuser-password";
  };
}
