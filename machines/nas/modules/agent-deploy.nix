{
  config,
  lib,
  pkgs,
  ...
}:

# Restricted host-deploy path for one agent SSH key. Disabled by default.
# The key can only run `agent-deploy ssh`, which accepts named actions from a
# fixed allowlist (stacks/agent-deploy/agent_deploy.py). Mutating actions need
# a single-use operator arm (`agent-deploy-admin arm <action>`, max 30 min).
# See stacks/agent-deploy/README.md.
let
  cfg = config.qnoxslab.agentDeploy;
  # The deployed generation's own reviewed source: actions run these exact files.
  source = lib.cleanSource ../../..;
  runtimePath = lib.makeBinPath [
    pkgs.bash
    pkgs.coreutils
    pkgs.gawk
    pkgs.gnugrep
    pkgs.git
    pkgs.jq
    pkgs.nix
    pkgs.python3
    pkgs.systemd
    pkgs.util-linux
    pkgs.docker-client
  ];
  settings = pkgs.writeText "agent-deploy-config.json" (
    builtins.toJSON {
      user = "agent-deploy";
      source = "${source}";
      stateDir = "/var/lib/agent-deploy";
      auditLog = "/var/log/agent-deploy/audit.log";
      logger = "${pkgs.util-linux}/bin/logger";
      sudo = "/run/wrappers/bin/sudo";
      path = "${runtimePath}:/run/current-system/sw/bin";
      repository = "https://github.com/jlorezz/homelab.git";
      toplevelAttr = "nixosConfigurations.nas.config.system.build.toplevel";
      productionUnit = "/etc/systemd/system/dealpipe-deploy-production.service";
      healthUnits = [
        "sshd.service"
        "tailscaled.service"
      ];
      confirmMinutes = cfg.confirmMinutes;
      heavyLock = "/var/lib/agent-workloads/heavy.lock";
      queueSeconds = 900;
      ciPolicy = "/var/lib/agent-workloads/ci-policy/policy.json";
    }
  );
  program = "${pkgs.python3}/bin/python3 ${source}/stacks/agent-deploy/agent_deploy.py --config ${settings}";
  # The only command the sudo rule permits; it re-validates every argument.
  rootHelper = pkgs.writeShellScript "agent-deploy-root" ''exec ${program} root "$@"'';
  sshCommand = pkgs.writeShellScript "agent-deploy-ssh" "exec ${program} --root-helper ${rootHelper} ssh";
  admin = pkgs.writeShellScriptBin "agent-deploy-admin" ''
    if [ "$(id -u)" != 0 ]; then echo "agent-deploy-admin: run as root" >&2; exit 2; fi
    exec ${program} admin "$@"
  '';
in
{
  options.qnoxslab.agentDeploy = {
    enable = lib.mkEnableOption "the restricted agent-deploy SSH path (forced command, allowlisted actions)";
    authorizedKey = lib.mkOption {
      type = lib.types.str;
      default = "";
      example = "ssh-ed25519 AAAAC3... agent-deploy@t3";
      description = "Public ed25519 key only; the private key never enters this repository.";
    };
    fromAddresses = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "172.18.0.0/16" ];
      description = "Source addresses/CIDRs sshd accepts this key from (the T3 container's network).";
    };
    confirmMinutes = lib.mkOption {
      type = lib.types.ints.between 5 30;
      default = 15;
      description = "Minutes before an unconfirmed nixos-switch automatically reverts.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = builtins.match "ssh-ed25519 [A-Za-z0-9+/]+=*( [^\",]*)?" cfg.authorizedKey != null;
        message = "qnoxslab.agentDeploy.authorizedKey must be one ed25519 public key without options.";
      }
      {
        assertion =
          cfg.fromAddresses != [ ]
          && lib.all (a: builtins.match "[0-9a-f.:/]+" a != null && !(lib.elem a [ "0.0.0.0/0" "::/0" ])) cfg.fromAddresses;
        message = "qnoxslab.agentDeploy.fromAddresses must list the T3 source addresses.";
      }
    ];

    users.groups.agent-deploy = { };
    users.users.agent-deploy = {
      isSystemUser = true;
      group = "agent-deploy";
      home = "/var/empty";
      # sshd runs the forced command through the login shell; no password exists.
      shell = pkgs.bash;
      openssh.authorizedKeys.keys = [
        ''restrict,command="${sshCommand}",from="${lib.concatStringsSep "," cfg.fromAddresses}" ${cfg.authorizedKey}''
      ];
    };

    services.openssh.extraConfig = ''
      Match User agent-deploy
        ForceCommand ${sshCommand}
        AuthenticationMethods publickey
        PermitTTY no
        AllowTcpForwarding no
        AllowAgentForwarding no
        AllowStreamLocalForwarding no
        X11Forwarding no
        PermitTunnel no
    '';

    security.sudo.extraRules = [
      {
        users = [ "agent-deploy" ];
        commands = [
          {
            command = "${rootHelper}";
            options = [ "NOPASSWD" ];
          }
        ];
      }
    ];

    environment.systemPackages = [ admin ];

    systemd.tmpfiles.rules = [
      "d /var/lib/agent-deploy 0700 root root -"
      "d /var/lib/agent-deploy/builds 0700 root root -"
      "d /var/log/agent-deploy 0750 root root -"
      "f /var/log/agent-deploy/audit.log 0640 root root -"
      # Append-only where the filesystem supports it (UNVERIFIED on the NAS root fs).
      "h /var/log/agent-deploy/audit.log - - - - +a"
    ];
  };
}
