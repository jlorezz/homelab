{ config, lib, ... }:

let
  cfg = config.qnoxslab;
  nasIP = cfg.network.nasIP;
  mkClanSecret = import ../../lib/mkClanSecret.nix;
  mkSecretsEnv = import ../../lib/mkSecretsEnv.nix { inherit config; };

  servicesYaml = import ../../config/homepage/services.nix {
    inherit nasIP;
    tailscaleHostname = cfg.tailscaleHostname;
  };
  settingsYaml = import ../../config/homepage/settings.nix;
  widgetsYaml = import ../../config/homepage/widgets.nix;
  bookmarksYaml = import ../../config/homepage/bookmarks.nix;
in
lib.mkMerge [
{
  virtualisation.oci-containers.containers.homepage = {
    image = "ghcr.io/gethomepage/homepage:v2.5.0";
    volumes = [
      "${cfg.storage.containerDataPath}/homepage:/app/config"
    ] ++ map (name: "/etc/homepage/${name}.yaml:/app/config/${name}.yaml:ro")
      [ "services" "settings" "widgets" "bookmarks" ];
    environment = {
      TZ = cfg.timezone;
      HOMEPAGE_ALLOWED_HOSTS = "${nasIP}:3001,localhost:3001,${cfg.tailscaleHostname}";
      PORT = "3001";
    };
    environmentFiles = [ "/run/homepage-secrets/secrets.env" ];
    extraOptions = [ "--network=host" ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/homepage 0755 root root -"
  ];

  systemd.services.docker-homepage.restartTriggers = map
    (name: config.environment.etc."homepage/${name}.yaml".source)
    [ "services" "settings" "widgets" "bookmarks" ];

  clan.core.vars.generators =
    mkClanSecret {
      name = "homepage-adguard-password";
      description = "AdGuard Home password for Homepage dashboard";
    };

  environment.etc = {
    "homepage/services.yaml".text = servicesYaml;
    "homepage/settings.yaml".text = settingsYaml;
    "homepage/widgets.yaml".text = widgetsYaml;
    "homepage/bookmarks.yaml".text = bookmarksYaml;
  };
}
(mkSecretsEnv {
  name = "homepage";
  vars = {
    HOMEPAGE_VAR_ADGUARD_PASSWORD = "homepage-adguard-password";
  };
})
]
