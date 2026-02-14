{ config, ... }:
let
  cfg = config.qnoxslab;
in
{
  networking = {
    hostName = "nas";

    # Static IP - no NetworkManager needed for servers
    networkmanager.enable = false;

    interfaces.${cfg.network.interface} = {
      useDHCP = false;
      ipv4.addresses = [
        {
          address = cfg.network.nasIP;
          prefixLength = 24;
        }
      ];
    };

    defaultGateway = {
      address = cfg.network.gateway;
      interface = cfg.network.interface;
    };

    # Use local AdGuard Home for DNS resolution with external fallback
    # Fallback ensures DNS works during boot before AdGuard starts
    nameservers = [
      "127.0.0.1"
      "1.1.1.1"
    ];

    firewall = {
      enable = true;
      allowedTCPPorts = [
        22 # SSH
        53 # DNS
        3000 # AdGuard Home web interface
      ];
      allowedUDPPorts = [
        53 # DNS
        1900 # DLNA/UPnP discovery
        7359 # Jellyfin client discovery
      ];
    };
  };

  # Disable systemd-resolved to free port 53 for AdGuard Home
  services.resolved.enable = false;
}
