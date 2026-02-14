{ config, pkgs, ... }:
let
  # Centralized blocklist definitions
  blocklists = [
    {
      name = "AdGuard DNS filter";
      url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt";
    }
    {
      name = "AdGuard Mobile Ads";
      url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_11.txt";
    }
    {
      name = "OISD Full";
      url = "https://big.oisd.nl/";
    }
    {
      name = "Steven Black Unified Hosts";
      url = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts";
    }
    {
      name = "HaGeZi Multi PRO";
      url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/hosts/pro.txt";
    }
    {
      name = "AdGuard Tracking Protection";
      url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_3.txt";
    }
    {
      name = "AdGuard Social Media";
      url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_4.txt";
    }
    {
      name = "EasyList";
      url = "https://easylist.to/easylist/easylist.txt";
    }
    {
      name = "EasyPrivacy";
      url = "https://easylist.to/easylist/easyprivacy.txt";
    }
    {
      name = "URLhaus Malware";
      url = "https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-hosts.txt";
    }
    {
      name = "Phishing Army";
      url = "https://phishing.army/download/phishing_army_blocklist.txt";
    }
  ];
in
{
  services.adguardhome = {
    enable = true;
    host = "0.0.0.0";
    port = 3000;
    mutableSettings = true;

    settings = {
      users = [
        {
          name = "qnoxs";
          password = "PLACEHOLDER";
        }
      ];

      dns = {
        upstream_dns = [
          # Quad9
          "9.9.9.9"
          "149.112.112.112"
          "https://dns.quad9.net/dns-query"
          # Cloudflare
          "1.1.1.1"
          "1.0.0.1"
          "https://cloudflare-dns.com/dns-query"
          # Google
          "8.8.8.8"
          "8.8.4.4"
          "https://dns.google/dns-query"
        ];

        bootstrap_dns = [
          "9.9.9.9"
          "1.1.1.1"
        ];

        # Cache: 10MB with optimistic serving
        cache_size = 10485760;
        cache_ttl_min = 600;
        cache_ttl_max = 86400;
        cache_optimistic = true;

        enable_dnssec = true;
        all_servers = false;
        fastest_addr = true;
      };

      filtering = {
        protection_enabled = true;
        filtering_enabled = true;
        safe_browsing_enabled = true;
        parental_enabled = false;

        safe_search.enabled = false;

        blocking_mode = "custom_ip";
        blocking_ipv4 = "0.0.0.0";
        blocking_ipv6 = "::";

        filters = map (f: {
          enabled = true;
          inherit (f) name url;
        }) blocklists;
        filters_update_interval = 1;
      };

      querylog = {
        enabled = true;
        file_enabled = true;
        interval = "2160h"; # 90 days
        size_memory = 1000;
      };

      statistics = {
        enabled = true;
        interval = "2160h";
      };

      dhcp.enabled = false;
    };
  };

  # Inject the real bcrypt password hash into AdGuard's config before it starts
  systemd.services.adguardhome-inject-password = {
    description = "Inject AdGuard Home password from secrets";
    wantedBy = [ "adguardhome.service" ];
    before = [ "adguardhome.service" ];
    after = [ "adguardhome-prestart.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ pkgs.replace-secret ];
    script = ''
      replace-secret "PLACEHOLDER" \
        "${config.clan.core.vars.generators.adguard-password-hash.files.secret.path}" \
        /var/lib/AdGuardHome/AdGuardHome.yaml
    '';
  };

  # Clan vars generator for AdGuard admin password (bcrypt hash)
  clan.core.vars.generators.adguard-password-hash = {
    prompts.value = {
      description = "AdGuard Home admin password bcrypt hash (generate with: htpasswd -nbB '' 'yourpassword' | cut -d: -f2)";
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
}
