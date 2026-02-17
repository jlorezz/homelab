{ ... }:
{
  networking = {
    networkmanager.enable = true;
    firewall = {
      enable = true;
      allowedTCPPorts = [ 22 ];
    };
  };

  services.tailscale = {
    enable = true;
    openFirewall = true;
  };
}
