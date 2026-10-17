{ config, lib, pkgs, ... }:
let
  source = ../../../stacks/agents/knowledge;
  state = "/var/lib/agent-platform/knowledge";
  runtime = pkgs.linkFarm "agent-knowledge-runtime" [
    { name = "brain.py"; path = source + /brain.py; }
    { name = "source_review.py"; path = source + /source_review.py; }
    { name = "handoff.schema.json"; path = ../../../stacks/agents/policy/handoff.schema.json; }
  ];
  mcp = pkgs.writeShellScriptBin "brain-mcp" ''
    exec ${pkgs.python3}/bin/python3 ${runtime}/brain.py bridge
  '';
  admin = pkgs.writeShellScriptBin "brain-admin" ''
    exec ${pkgs.python3}/bin/python3 ${runtime}/brain.py "$@"
  '';
  render = name:
    builtins.replaceStrings
      [ "@python@" "@runtime@" "@state@" "[Service]" ]
      [ "${pkgs.python3}/bin/python3" "${runtime}" state "[Service]\nEnvironment=PATH=${lib.makeBinPath [ pkgs.git pkgs.gh pkgs.openssh pkgs.coreutils ]}" ]
      (builtins.readFile (source + "/units/${name}"));
  names = builtins.attrNames (builtins.readDir (source + /units));
in {
  config = lib.mkIf config.qnoxslab.agents.enable {
    users.users.jlorezz.extraGroups = [ "agent-platform" ];
    environment.systemPackages = [ mcp admin ];
    system.activationScripts.agentKnowledgeAdopt = lib.stringAfter [ "etc" ] ''
      if [ -f /etc/systemd/system.control/.agent-knowledge-managed ]; then
        ${lib.concatMapStringsSep "\n" (name: ''
          rm -f /etc/systemd/system.control/${name}
          rm -f /etc/systemd/system.control/timers.target.wants/${name}
          rm -f /etc/systemd/system.control/multi-user.target.wants/${name}
        '') names}
        rm -f /etc/systemd/system.control/.agent-knowledge-managed
      fi
    '';
    environment.etc."agent-knowledge/sources.json".source = source + /sources.json;
    # The manifest lives outside the indexer's writable state.
    systemd.units = builtins.listToAttrs (map (name: {
      inherit name;
      value = {
        text = builtins.replaceStrings [ "${state}/sources.json" ] [ "/etc/agent-knowledge/sources.json" ] (render name);
        wantedBy = lib.optional (name == "agent-knowledge.service") "multi-user.target"
          ++ lib.optional (lib.hasSuffix ".timer" name) "timers.target";
      };
    }) names);
    systemd.tmpfiles.rules = [
      "d ${state} 0755 root root -"
      "d ${state}/review 0700 root root -"
      "d ${state}/repos 0755 agent-platform agent-platform -"
      "d ${state}/evidence 0755 agent-platform agent-platform -"
      "d ${state}/proposals 0755 root root -"
      "d ${state}/proposals/t3 0700 agent-platform agent-platform -"
      "d ${state}/proposals/opencode 0700 agent-platform agent-platform -"
      "d ${state}/proposals/desktop 0700 jlorezz users -"
      "d ${state}/evidence/homelab/receipts 0755 agent-platform agent-platform -"
      "d ${state}/evidence/dealpipe/receipts 0755 agent-platform agent-platform -"
      "d /storage/obsidian/Brain 2770 jlorezz users -"
      "d /storage/obsidian/Brain/Inbox 2770 jlorezz users -"
      "L+ /var/lib/agent-platform/bin/brain-mcp - - - - ${mcp}/bin/brain-mcp"
      "L+ /var/lib/agent-platform/bin/brain-admin - - - - ${admin}/bin/brain-admin"
    ];
  };
}
