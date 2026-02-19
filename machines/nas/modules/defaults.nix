{ config, lib, ... }:

let
  cfg = config.qnoxslab;
in
{
  options.qnoxslab = {
    network = {
      nasIP = lib.mkOption {
        type = lib.types.str;
        default = "192.168.2.2";
        description = "IP address of the NAS server";
      };

      gateway = lib.mkOption {
        type = lib.types.str;
        default = "192.168.2.1";
        description = "Default gateway address";
      };

      interface = lib.mkOption {
        type = lib.types.str;
        default = "enp7s0";
        description = "Primary network interface";
      };
    };

    tailscaleHostname = lib.mkOption {
      type = lib.types.str;
      default = "nas.tail9065b4.ts.net";
      description = "Tailscale MagicDNS hostname for this machine";
    };

    timezone = lib.mkOption {
      type = lib.types.str;
      default = "Europe/Berlin";
      description = "System timezone";
    };

    storage = {
      mediaPath = lib.mkOption {
        type = lib.types.str;
        default = "/storage/media";
        description = "Base path for media storage";
      };

      containerDataPath = lib.mkOption {
        type = lib.types.str;
        default = "/var/lib";
        description = "Base path for container persistent data";
      };
    };
  };

  config = {
    # Apply timezone system-wide
    time.timeZone = cfg.timezone;

    # Media storage hierarchy
    systemd.tmpfiles.rules = [
      "d ${cfg.storage.mediaPath} 0775 root media -"
      "d ${cfg.storage.mediaPath}/movies 0775 root media -"
      "d ${cfg.storage.mediaPath}/tv 0775 root media -"
      "d ${cfg.storage.mediaPath}/downloads 0775 root media -"
      "d ${cfg.storage.mediaPath}/downloads/complete 0775 qbittorrent media -"
      "d ${cfg.storage.mediaPath}/downloads/incomplete 0775 qbittorrent media -"
    ];
  };
}
