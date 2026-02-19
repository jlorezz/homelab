{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkOption types;
in
{
  options.qnoxslab = {
    timezone = mkOption {
      type = types.str;
      default = "Europe/Berlin";
    };
  };

  config = {
    time.timeZone = config.qnoxslab.timezone;
    networking.hostName = "desktop";
  };
}
