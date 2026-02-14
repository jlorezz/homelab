{ config, ... }:

let
  cfg = config.qnoxslab;
  nasIP = cfg.network.nasIP;
in
{
  virtualisation.oci-containers.containers.homepage = {
    image = "ghcr.io/gethomepage/homepage:v1.10.1";
    volumes = [
      "${cfg.storage.containerDataPath}/homepage:/app/config"
    ];
    environment = {
      TZ = cfg.timezone;
      HOMEPAGE_ALLOWED_HOSTS = "${nasIP}:3001,localhost:3001,${nasIP}:3000,localhost:3000,nas.tail9065b4.ts.net";
      PORT = "3001";
    };
    environmentFiles = [ "/var/lib/homepage/secrets.env" ];
    extraOptions = [
      "--network=host"
    ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/homepage 0755 root root -"
  ];

  networking.firewall.allowedTCPPorts = [ 3001 ];

  # Copy Homepage config files before container starts
  systemd.services.podman-homepage.preStart = ''
    cp -f /etc/homepage/*.yaml ${cfg.storage.containerDataPath}/homepage/
  '';

  # Secrets generators
  clan.core.vars.generators = {
    homepage-jellyfin-key = {
      prompts.value = {
        description = "Jellyfin API key for Homepage dashboard";
        type = "hidden";
      };
      files.secret = {
        secret = true;
        neededFor = "services";
      };
      script = ''
        cat $prompts/value > $out/secret
      '';
    };

    homepage-qbittorrent-password = {
      prompts.value = {
        description = "qBittorrent password for Homepage dashboard";
        type = "hidden";
      };
      files.secret = {
        secret = true;
        neededFor = "services";
      };
      script = ''
        cat $prompts/value > $out/secret
      '';
    };

    homepage-radarr-key = {
      prompts.value = {
        description = "Radarr API key for Homepage dashboard";
        type = "hidden";
      };
      files.secret = {
        secret = true;
        neededFor = "services";
      };
      script = ''
        cat $prompts/value > $out/secret
      '';
    };

    homepage-sonarr-key = {
      prompts.value = {
        description = "Sonarr API key for Homepage dashboard";
        type = "hidden";
      };
      files.secret = {
        secret = true;
        neededFor = "services";
      };
      script = ''
        cat $prompts/value > $out/secret
      '';
    };

    homepage-adguard-password = {
      prompts.value = {
        description = "AdGuard Home password for Homepage dashboard";
        type = "hidden";
      };
      files.secret = {
        secret = true;
        neededFor = "services";
      };
      script = ''
        cat $prompts/value > $out/secret
      '';
    };
  };

  # Generate secrets environment file before container starts
  systemd.services.homepage-secrets = {
    description = "Generate Homepage secrets environment file";
    wantedBy = [ "podman-homepage.service" ];
    before = [ "podman-homepage.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
            mkdir -p /var/lib/homepage
            cat > /var/lib/homepage/secrets.env << EOF
      HOMEPAGE_VAR_JELLYFIN_KEY=$(cat ${config.clan.core.vars.generators.homepage-jellyfin-key.files.secret.path})
      HOMEPAGE_VAR_QBITTORRENT_PASSWORD=$(cat ${config.clan.core.vars.generators.homepage-qbittorrent-password.files.secret.path})
      HOMEPAGE_VAR_RADARR_KEY=$(cat ${config.clan.core.vars.generators.homepage-radarr-key.files.secret.path})
      HOMEPAGE_VAR_SONARR_KEY=$(cat ${config.clan.core.vars.generators.homepage-sonarr-key.files.secret.path})
      HOMEPAGE_VAR_ADGUARD_PASSWORD=$(cat ${config.clan.core.vars.generators.homepage-adguard-password.files.secret.path})
      EOF
            chmod 600 /var/lib/homepage/secrets.env
    '';
  };

  # Homepage dashboard configuration
  environment.etc."homepage/services.yaml".text = ''
    - Media:
        - Immich:
            icon: immich.png
            href: http://${nasIP}:2283
            description: Photo Management

        - Jellyfin:
            icon: jellyfin.png
            href: http://${nasIP}:8096
            description: Media Server
            widget:
              type: jellyfin
              url: http://${nasIP}:8096
              key: {{HOMEPAGE_VAR_JELLYFIN_KEY}}
              enableBlocks: true
              enableNowPlaying: true

        - Jellyseerr:
            icon: jellyseerr.png
            href: http://${nasIP}:5055
            description: Media Requests

    - Downloads:
        - qBittorrent:
            icon: qbittorrent.png
            href: http://${nasIP}:8080
            description: Torrent Client
            widget:
              type: qbittorrent
              url: http://${nasIP}:8080
              username: qnoxs
              password: {{HOMEPAGE_VAR_QBITTORRENT_PASSWORD}}

        - Radarr:
            icon: radarr.png
            href: http://${nasIP}:7878
            description: Movie Management
            widget:
              type: radarr
              url: http://${nasIP}:7878
              key: {{HOMEPAGE_VAR_RADARR_KEY}}

        - Sonarr:
            icon: sonarr.png
            href: http://${nasIP}:8989
            description: TV Show Management
            widget:
              type: sonarr
              url: http://${nasIP}:8989
              key: {{HOMEPAGE_VAR_SONARR_KEY}}

        - Prowlarr:
            icon: prowlarr.png
            href: http://${nasIP}:9696
            description: Indexer Management

    - Tools:
        - AdGuard Home:
            icon: adguard-home.png
            href: http://${nasIP}:3000
            description: DNS & Ad Blocking
            widget:
              type: adguard
              url: http://${nasIP}:3000
              username: qnoxs
              password: {{HOMEPAGE_VAR_ADGUARD_PASSWORD}}

        - Excalidraw:
            icon: excalidraw.png
            href: http://${nasIP}:3030
            description: Whiteboard

        - Portainer:
            icon: portainer.png
            href: https://${nasIP}:9443
            description: Container Management

        - Stirling PDF:
            icon: stirling-pdf.png
            href: http://${nasIP}:8088
            description: PDF Tools

        - Home Assistant:
            icon: home-assistant.png
            href: http://${nasIP}:8123
            description: Home Automation

        - Linkding:
            icon: linkding.png
            href: http://${nasIP}:9090
            description: Bookmark Manager

        - FlareSolverr:
            icon: cloudflare.png
            href: http://${nasIP}:8191
            description: CloudFlare Bypass

        - Netdata:
            icon: netdata.png
            href: http://${nasIP}:19999
            description: System Monitoring
  '';

  environment.etc."homepage/settings.yaml".text = ''
    title: qnoxslab
    theme: dark
    color: slate
    headerStyle: clean
    layout:
      Media:
        style: row
        columns: 2
      Downloads:
        style: row
        columns: 4
      Tools:
        style: row
        columns: 4
  '';

  environment.etc."homepage/widgets.yaml".text = ''
    - resources:
        cpu: true
        memory: true
        disk: /

    - datetime:
        text_size: xl
        format:
          timeStyle: short
          dateStyle: short
          hourCycle: h23
  '';

  environment.etc."homepage/bookmarks.yaml".text = ''
    []
  '';
}
