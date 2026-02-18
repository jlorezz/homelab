{ config, pkgs, ... }:

let
  cfg = config.qnoxslab;
  openclawConfig = builtins.toJSON {
    models = {
      mode = "merge";
      providers = {
        ollama = {
          baseUrl = "http://100.118.57.99:11434";
          apiKey = "ollama";
          api = "ollama";
          models = [
            {
              id = "qwen2.5:32b-instruct-q4_K_M";
              name = "Qwen 2.5 32B Instruct";
              reasoning = false;
              input = [ "text" ];
              cost = {
                input = 0;
                output = 0;
                cacheRead = 0;
                cacheWrite = 0;
              };
              contextWindow = 32000;
              maxTokens = 4096;
            }
          ];
        };
      };
    };
    agents = {
      defaults = {
        model = {
          primary = "ollama/qwen2.5:32b-instruct-q4_K_M";
        };
        blockStreamingDefault = "on";
        blockStreamingBreak = "text_end";
        blockStreamingChunk = {
          minChars = 100;
          maxChars = 1500;
        };
        blockStreamingCoalesce = {
          minChars = 1500;
          maxChars = 2000;
          idleMs = 500;
        };
        typingMode = "instant";
        typingIntervalSeconds = 6;
      };
    };
    messages = {
      inbound = {
        debounceMs = 0;
      };
    };
    commands = {
      native = "auto";
      nativeSkills = "auto";
    };
    channels = {
      discord = {
        enabled = true;
        groupPolicy = "allowlist";
        ackReaction = "🤔";
      };
    };
    gateway = {
      port = 18789;
      mode = "local";
      bind = "lan";
      trustedProxies = [ "10.88.0.0/16" ];
      auth = {
        mode = "token";
        rateLimit = {
          maxAttempts = 10;
          windowMs = 60000;
          lockoutMs = 300000;
        };
      };
      controlUi = {
        dangerouslyDisableDeviceAuth = true;
      };
    };
    plugins = {
      entries = {
        discord = {
          enabled = true;
        };
      };
    };
  };
  openclawConfigFile = pkgs.writeText "openclaw.json" openclawConfig;
in
{
  virtualisation.oci-containers.containers.openclaw = {
    image = "ghcr.io/openclaw/openclaw:latest";
    ports = [ "18789:18789" ];
    volumes = [
      "${cfg.storage.containerDataPath}/openclaw:/home/node/.openclaw"
    ];
    environment = {
      TZ = cfg.timezone;
    };
    environmentFiles = [ "/run/openclaw-secrets/secrets.env" ];
  };

  systemd.tmpfiles.rules = [
    "d ${cfg.storage.containerDataPath}/openclaw 0700 1000 1000 -"
    "d ${cfg.storage.containerDataPath}/openclaw/workspace 0700 1000 1000 -"
  ];

  # Discord bot token
  clan.core.vars.generators.openclaw-discord-token = {
    prompts.value = {
      description = "OpenClaw Discord bot token";
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

  # Gateway auth token
  clan.core.vars.generators.openclaw-gateway-token = {
    prompts.value = {
      description = "OpenClaw gateway authentication token";
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

  # Generate secrets + config before container starts
  systemd.services.openclaw-secrets = {
    description = "Generate OpenClaw secrets and configuration";
    wantedBy = [ "podman-openclaw.service" ];
    before = [ "podman-openclaw.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script =
      let
        gatewayTokenPath = config.clan.core.vars.generators.openclaw-gateway-token.files.secret.path;
        discordTokenPath = config.clan.core.vars.generators.openclaw-discord-token.files.secret.path;
      in
      ''
        mkdir -p /run/openclaw-secrets
        install -m 600 /dev/stdin /run/openclaw-secrets/secrets.env <<EOF
        OPENCLAW_GATEWAY_TOKEN=$(cat ${gatewayTokenPath})
        DISCORD_BOT_TOKEN=$(cat ${discordTokenPath})
        EOF

        # Write openclaw.json config (copied so OpenClaw can modify it at runtime)
        install -m 600 ${openclawConfigFile} ${cfg.storage.containerDataPath}/openclaw/openclaw.json
        chown -R 1000:1000 ${cfg.storage.containerDataPath}/openclaw
      '';
  };
}
