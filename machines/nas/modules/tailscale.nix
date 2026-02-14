{ pkgs, ... }:
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
      # Wait for tailscale to be connected
      tailscale status --json | head -c 0 2>/dev/null || sleep 5

      # Reset stale serve configs before applying declarative set
      tailscale serve reset

      # Homepage
      tailscale serve --bg --https=443 http://localhost:3001
      # Jellyfin
      tailscale serve --bg --https=8443 http://localhost:8096
      # AdGuard Home
      tailscale serve --bg --https=3000 http://localhost:3000
      # Syncthing
      tailscale serve --bg --https=8384 http://localhost:8384
    '';
  };

  environment.systemPackages = [ pkgs.tailscale ];
}
