{ config, lib, pkgs, ... }:

# Clean shutdown on UPS battery via NUT. Off until a UPS is physically connected:
# enable it, plug the UPS in by USB, check `upsc nas-ups@localhost`, then pull
# the plug once to confirm the shutdown. Also set the BIOS to power on after AC
# loss so the NAS returns by itself when mains power comes back.
let
  cfg = config.qnoxslab.ups;
in
{
  options.qnoxslab.ups = {
    enable = lib.mkEnableOption "NUT monitoring of a locally attached UPS";
    driver = lib.mkOption {
      type = lib.types.str;
      default = "usbhid-ups";
      description = "NUT driver; usbhid-ups covers most USB consumer UPS units (APC, Eaton, CyberPower).";
    };
    port = lib.mkOption { type = lib.types.str; default = "auto"; };
  };

  config = lib.mkIf cfg.enable {
    clan.core.vars.generators.nas-ups-password = {
      files.secret.secret = true;
      runtimeInputs = [ pkgs.openssl ];
      script = "openssl rand -hex 32 > $out/secret";
    };

    power.ups = {
      enable = true;
      mode = "standalone";
      ups.nas-ups = {
        inherit (cfg) driver port;
        description = "UPS powering the NAS";
      };
      users.upsmon = {
        passwordFile = config.clan.core.vars.generators.nas-ups-password.files.secret.path;
        upsmon = "primary";
      };
      # Default NUT policy: shut down when the UPS reports low battery while on battery.
      upsmon.monitor.nas-ups = {
        system = "nas-ups@localhost";
        user = "upsmon";
        powerValue = 1;
      };
    };
  };
}
