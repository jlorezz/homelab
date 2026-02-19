{ ... }:
{
  services.syncthing = {
    enable = true;
    user = "jlorezz";
    group = "users";
    dataDir = "/storage/obsidian";
    configDir = "/home/jlorezz/.config/syncthing";
    guiAddress = "127.0.0.1:8384";
    overrideDevices = false;
    overrideFolders = false;
    settings.options.urAccepted = -1;
  };

  systemd.tmpfiles.rules = [
    "d /storage/obsidian 0750 jlorezz users -"
  ];

  # Sync ports open for LAN device discovery — GUI access via Tailscale only
  networking.firewall = {
    allowedTCPPorts = [ 22000 ];
    allowedUDPPorts = [
      22000
      21027
    ];
  };
}
