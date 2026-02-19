{
  lib,
  pkgs,
  ...
}:

let
  # Declarative Tailscale Serve map: HTTPS port -> local service port
  serveMap = {
    "443" = 3001; # Homepage
    "8443" = 8096; # Jellyfin
    "3000" = 3000; # AdGuard Home
    "8384" = 8384; # Syncthing
    "2283" = 2283; # Immich
    "28981" = 28981; # Paperless
    "19999" = 19999; # Netdata
    "5055" = 5055; # Jellyseerr
    "9443" = 9443; # Portainer
    "8123" = 8123; # Home Assistant
  };

  serveCommands = lib.mapAttrsToList (
    httpsPort: localPort:
    "tailscale serve --bg --https=${httpsPort} http://localhost:${toString localPort}"
  ) serveMap;
in
{
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
    };
    path = [ pkgs.tailscale ];
    script = ''
      # Wait for tailscale to be connected (30s timeout)
      for i in $(seq 1 30); do
        if tailscale status --json > /dev/null 2>&1; then
          break
        fi
        sleep 1
      done

      # Reset stale serve configs before applying declarative set
      tailscale serve reset

      ${lib.concatStringsSep "\n" serveCommands}
    '';
  };

  environment.systemPackages = [ pkgs.tailscale ];
}
