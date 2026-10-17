{
  config,
  lib,
  pkgs,
  ...
}:

let
  # Declarative Tailscale Serve map: HTTPS port -> local service port
  serveMap = {
    "443" = 3001; # Homepage
    "3000" = 3000; # AdGuard Home
    "2283" = 2283; # Immich
    "28981" = 28981; # Paperless
    "8090" = 8090; # Beszel
    "9443" = 9443; # Portainer
    "8123" = 8123; # Home Assistant
    "3030" = 3030; # Excalidraw
    "5050" = 5050; # pgAdmin
    "8088" = 8088; # Stirling PDF
    "9090" = 9090; # Linkding
    "5984" = 5984; # Obsidian LiveSync / CouchDB
  }
  // config.qnoxslab.tailscaleServe;

  serveCommands = lib.mapAttrsToList (
    httpsPort: localPort:
    let
      # Portainer serves HTTPS with a self-signed certificate on its local port.
      target =
        if localPort == 9443 then
          "https+insecure://localhost:9443"
        else
          "http://localhost:${toString localPort}";
    in
    "tailscale serve --bg --https=${httpsPort} ${target}"
  ) serveMap;
in
{
  options.qnoxslab.tailscaleServe = lib.mkOption {
    type = lib.types.attrsOf lib.types.port;
    default = { };
    description = "Additional HTTPS Tailscale Serve ports mapped to loopback listeners.";
  };

  config = {
    services.tailscale = {
      enable = true;
      openFirewall = true;
      useRoutingFeatures = "server";
      extraUpFlags = [
        "--advertise-exit-node"
        "--advertise-routes=192.168.2.0/24"
        "--ssh"
        "--accept-dns=false"
      ];
    };

    networking.firewall.trustedInterfaces = [ "tailscale0" ];

    boot.kernel.sysctl = {
      "net.ipv4.ip_forward" = 1;
      "net.ipv6.conf.all.forwarding" = 1;
    };

    # Declarative Tailscale Serve: HTTPS reverse proxy with auto TLS certs
    # Requires MagicDNS enabled in Tailscale admin console
    systemd.services.tailscale-serve = {
      description = "Configure Tailscale Serve for local services";
      after = [ "tailscaled.service" ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        Restart = "on-failure";
        RestartSec = "15s";
        TimeoutStartSec = "90s";
      };
      path = [
        pkgs.tailscale
        pkgs.jq
      ];
      script = ''
        # Wait for tailscale to be connected (30s timeout)
        for i in $(seq 1 30); do
          if tailscale status --json | jq -e '.BackendState == "Running"' > /dev/null; then
            break
          fi
          sleep 1
        done

        if ! tailscale status --json | jq -e '.BackendState == "Running"' > /dev/null; then
          echo "Tailscale is not connected yet; retrying after sign-in or network recovery." >&2
          exit 1
        fi

        # Reset stale serve configs before applying declarative set
        tailscale serve reset

        ${lib.concatStringsSep "\n" serveCommands}
      '';
    };

    environment.systemPackages = [ pkgs.tailscale ];
  };
}
