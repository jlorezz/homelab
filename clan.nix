{
  # Ensure this is unique among all clans you want to use.
  meta.name = "qnoxslab";
  meta.domain = "qnoxs.lab";
  meta.description = "Personal infrastructure management";

  inventory.machines = {
    nas = {
      tags = [
        "nixos"
        "server"
        "media-server"
        "dns-server"
        "always-on"
      ];
    };
  };

  # Docs: See https://docs.clan.lol/services/definition/
  inventory.instances = {

    # SSH access management (replaces deprecated admin service)
    sshd = {
      roles.server.tags.all = { };
      roles.server.settings.authorizedKeys = {
        "nas-jlorezz" =
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHOYvgPIeoUbctMtnM34lhIjrZrvcoGSuaCyJ03QPwvU florianbress@gmail.com";
      };
    };

    # Docs: https://docs.clan.lol/services/official/tor/
    # Tor network provides secure, anonymous connections to your machines
    # All machines will be accessible via Tor as a fallback connection method
    tor = {
      roles.server.tags.nixos = { };
    };
  };

  # Additional NixOS configuration can be added here.
  # machines/jon/configuration.nix will be automatically imported.
  # See: https://docs.clan.lol/guides/inventory/autoincludes/
  machines = {
    # jon = { config, ... }: {
    #   environment.systemPackages = [ pkgs.asciinema ];
    # };
  };
}
