# Homepage services YAML configuration
# Uses Nix interpolation for nasIP and tailscaleHostname

{ nasIP, tailscaleHostname }:

''
  - Media:
      - Immich:
          icon: immich.png
          href: https://${tailscaleHostname}:2283
          description: Photo Management

      - Jellyfin:
          icon: jellyfin.png
          href: https://${tailscaleHostname}:8443
          description: Media Server
          widget:
            type: jellyfin
            url: http://${nasIP}:8096
            key: {{HOMEPAGE_VAR_JELLYFIN_KEY}}
            enableBlocks: true
            enableNowPlaying: true

      - Jellyseerr:
          icon: jellyseerr.png
          href: https://${tailscaleHostname}:5055
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
          href: https://${tailscaleHostname}:3000
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
          href: https://${tailscaleHostname}:9443
          description: Container Management

      - Stirling PDF:
          icon: stirling-pdf.png
          href: http://${nasIP}:8088
          description: PDF Tools

      - Home Assistant:
          icon: home-assistant.png
          href: https://${tailscaleHostname}:8123
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
          href: https://${tailscaleHostname}:19999
          description: System Monitoring

      - Paperless:
          icon: paperless-ngx.png
          href: https://${tailscaleHostname}:28981
          description: Document Management

      - Syncthing:
          icon: syncthing.png
          href: https://${tailscaleHostname}:8384
          description: File Sync
''
