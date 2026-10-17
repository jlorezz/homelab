# Homepage services YAML configuration
# Uses Nix interpolation for nasIP and tailscaleHostname

{ nasIP, tailscaleHostname }:

''
  - Infrastructure:
      - Beszel:
          icon: beszel.png
          href: https://${tailscaleHostname}:8090
          description: NAS resources, containers, services & disk health
      - Portainer:
          icon: portainer.png
          href: https://${tailscaleHostname}:9443
          description: Application & CI container management

  - Media:
      - Immich:
          icon: immich.png
          href: https://${tailscaleHostname}:2283
          description: Photo Management

  - Tools:
      - AdGuard Home:
          icon: adguard-home.png
          href: https://${tailscaleHostname}:3000
          description: DNS & Ad Blocking

      - Excalidraw:
          icon: excalidraw.png
          href: https://${tailscaleHostname}:3030
          description: Whiteboard

      - Stirling PDF:
          icon: stirling-pdf.png
          href: https://${tailscaleHostname}:8088
          description: PDF Tools

      - Home Assistant:
          icon: home-assistant.png
          href: https://${tailscaleHostname}:8123
          description: Home Automation

      - Linkding:
          icon: linkding.png
          href: https://${tailscaleHostname}:9090
          description: Bookmark Manager

      - Paperless:
          icon: paperless-ngx.png
          href: https://${tailscaleHostname}:28981
          description: Document Management

  - Development:
      - T3 Code:
          icon: mdi-code-braces
          href: https://${tailscaleHostname}:3773
          description: Coding Workspace

      - Hermes:
          icon: mdi-robot
          href: https://${tailscaleHostname}:9119
          description: Agent Dashboard

''
