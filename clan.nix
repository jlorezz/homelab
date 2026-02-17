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
    desktop = {
      tags = [
        "nixos"
        "desktop"
        "workstation"
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

    # Docs: https://docs.clan.lol/25.11/services/official/certificates/
    # Internal PKI using step-ca for clan SSL/TLS certificates
    certificates = {
      roles.ca.machines.nas.settings.tlds = [ "lab" ];
      roles.default.machines.nas = { };
    };

    # Docs: https://docs.clan.lol/25.11/services/official/syncthing/
    # Continuous file synchronization across machines
    syncthing = {
      roles.peer.machines.nas.settings.folders = {
        obsidian = {
          path = "/storage/obsidian";
        };
      };
      # TODO: enable after running `clan vars generate --machine desktop`
      # roles.peer.machines.desktop.settings.folders = {
      #   obsidian = {
      #     path = "/home/jlorezz/obsidian";
      #   };
      # };
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
