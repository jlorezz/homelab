{ config, lib, pkgs, ... }:
let
  cfg = config.qnoxslab.dealpipeRecovery;
  source = ../../../stacks/dealpipe-recovery;
  python = pkgs.python3.withPackages (p: [ p.psycopg p.pyyaml p.cryptography ]);
  operator = pkgs.writeShellScriptBin "dealpipe-recovery" ''
    exec ${python}/bin/python3 ${source}/backup.py "$@"
  '';
  provision = pkgs.writeShellScriptBin "dealpipe-recovery-provision" ''
    exec ${python}/bin/python3 ${source}/provision.py "$@"
  '';
  operations = pkgs.writeShellScriptBin "dealpipe-recovery-operator" ''
    exec ${python}/bin/python3 ${source}/operations.py "$@"
  '';
  allNames = builtins.attrNames (builtins.readDir (source + /units));
  names = builtins.filter (name: cfg.enableProduction || lib.hasPrefix "dealpipe-testing-" name) allNames;
  path = lib.makeBinPath [ pkgs.postgresql_18 pkgs.restic pkgs.docker pkgs.util-linux pkgs.coreutils ];
  render = name: builtins.replaceStrings
    [ "@python@" "@source@" "Environment=PATH=/run/current-system/sw/bin" "[Service]" ]
    [ "${python}/bin/python3" "${source}" "Environment=PATH=${path}" "[Service]\nEnvironment=AGENT_OPERATOR_LIBRARY=${../../../stacks/agents}" ]
    (builtins.readFile (source + "/units/${name}"));
in {
  options.qnoxslab.dealpipeRecovery = {
    enableTesting = lib.mkEnableOption "isolated Dealpipe testing runtime (Portainer stack deployed separately)";
    enableProduction = lib.mkEnableOption "verified production Supabase backup and sanitized refresh timers";
  };
  config = lib.mkIf cfg.enableTesting {
    assertions = [{ assertion = !cfg.enableProduction || config.qnoxslab.offsiteBackup.enable; message = "Review the NAS offsite recovery setup before production recovery activation."; }];
    systemd.units = builtins.listToAttrs (map (name: {
      inherit name;
      value = {
        text = render name;
        wantedBy = lib.optional (lib.hasPrefix "dealpipe-testing-" name) "multi-user.target"
          ++ lib.optional (lib.hasSuffix ".timer" name) "timers.target";
      };
    }) names);
    system.activationScripts.dealpipeRecoveryAdopt = lib.stringAfter [ "etc" ] ''
      if [ -f /etc/systemd/system.control/.dealpipe-recovery-managed ]; then
        ${lib.concatMapStringsSep "\n" (name: ''
          rm -f /etc/systemd/system.control/${name}
          rm -f /etc/systemd/system.control/multi-user.target.wants/${name}
          rm -f /etc/systemd/system.control/timers.target.wants/${name}
        '') allNames}
        rm -f /etc/systemd/system.control/nas-health-check.service.d/60-dealpipe.conf
        rm -f /etc/systemd/system.control/.dealpipe-recovery-managed
        rm -f /nix/var/nix/gcroots/dealpipe-recovery-python
      fi
    '';
    environment.systemPackages = [ operator provision operations ];
    systemd.tmpfiles.rules = [
      "d /var/lib/dealpipe-recovery 0700 root root -"
      "d /var/lib/dealpipe-recovery/bin 0700 root root -"
      "L+ /var/lib/dealpipe-recovery/bin/provision - - - - ${provision}/bin/dealpipe-recovery-provision"
      "L+ /var/lib/dealpipe-recovery/bin/recovery - - - - ${operator}/bin/dealpipe-recovery"
      "L+ /var/lib/dealpipe-recovery/bin/operator - - - - ${operations}/bin/dealpipe-recovery-operator"
      "d /var/lib/dealpipe-testing 0700 root root -"
      "d /storage/backups/dealpipe-encrypted-object-cache 0700 root root -"
    ];
    systemd.services.nas-health-check.environment = lib.mkIf cfg.enableProduction {
      DEALPIPE_RECOVERY_STATUS = "/var/lib/dealpipe-recovery/status.json";
    };
  };
}
