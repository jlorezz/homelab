{ config, lib, ... }:

let
  cfg = config.qnoxslab;
in
{
  virtualisation.oci-containers.containers.homeassistant = {
    image = "ghcr.io/home-assistant/home-assistant:2026.10.1";
    volumes = [
      "${cfg.storage.containerDataPath}/homeassistant:/config"
      "/etc/homeassistant/tailscale-http.yaml:/config/tailscale-http.yaml:ro"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    extraOptions = [
      "--network=host"
    ];
  };

  environment.etc."homeassistant/tailscale-http.yaml".text = ''
    server_host: 127.0.0.1
    use_x_forwarded_for: true
    trusted_proxies:
      - 127.0.0.1
      - "::1"
  '';

  systemd.services.docker-homeassistant.restartTriggers = [
    config.environment.etc."homeassistant/tailscale-http.yaml".source
  ];

  # Keep the editable application config; add the managed HTTP include once.
  systemd.services.docker-homeassistant.preStart = lib.mkAfter ''
    configFile=${lib.escapeShellArg "${cfg.storage.containerDataPath}/homeassistant/configuration.yaml"}
    if [ ! -f "$configFile" ]; then
      printf 'default_config:\n' > "$configFile"
    fi
    if ! grep -qE '^http[[:space:]]*:' "$configFile"; then
      printf '\nhttp: !include tailscale-http.yaml\n' >> "$configFile"
    fi
  '';

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/homeassistant 0700 root root -"
  ];

}
