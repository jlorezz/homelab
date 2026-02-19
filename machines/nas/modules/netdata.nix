{ pkgs, ... }:
{
  services.netdata = {
    enable = true;
    package = pkgs.netdata.override { withCloudUi = true; };
    config = {
      global = {
        "update every" = 2;
        "memory mode" = "dbengine";
      };
      web = {
        "bind to" = "127.0.0.1";
      };
      db = {
        "dbengine multihost disk space MB" = 1024;
      };
    };
  };

  # Allow netdata to monitor Podman containers via socket
  users.users.netdata.extraGroups = [ "podman" ];

}
