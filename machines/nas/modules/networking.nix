{ config, ... }:
let
  cfg = config.qnoxslab;
in
{
  networking = {
    hostName = "nas";

    # Resolve the local certificate authority before DNS services are ready.
    hosts.${cfg.network.nasIP} = [ "ca.lab" ];

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
    # Keep the fallback resolver when the local DNS service is unavailable.
    resolvconf.useLocalResolver = false;
    resolvconf.extraConfig = "resolv_conf_local_only=NO";

    firewall = {
      enable = true;
      allowedTCPPorts = [
        22 # SSH
        53 # DNS
      ];
      allowedUDPPorts = [
        53 # DNS
      ];
    };
  };

  # Disable systemd-resolved to free port 53 for AdGuard Home
  services.resolved.enable = false;
}
