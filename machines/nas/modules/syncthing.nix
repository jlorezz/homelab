{ ... }:
{
  services.syncthing = {
    enable = true;
    user = "jlorezz";
    group = "users";
    dataDir = "/storage/obsidian";
    configDir = "/home/jlorezz/.config/syncthing";
    guiAddress = "0.0.0.0:8384";
    overrideDevices = false;
    overrideFolders = false;
    settings.options.urAccepted = -1;
  };

  systemd.tmpfiles.rules = [
    "d /storage/obsidian 0750 jlorezz users -"
  ];

  networking.firewall = {
    allowedTCPPorts = [
      8384
      22000
    ];
    allowedUDPPorts = [
      22000
      21027
    ];
  };
}
