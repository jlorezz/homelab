#!/usr/bin/env python3
"""CI-only evaluated contract for the agent-deploy module (needs nix, not root)."""
import json
import subprocess

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE ci-example"

expression = '''let
  f = builtins.getFlake (toString ./.);
  base = f.nixosConfigurations.nas;
  lib = base.pkgs.lib;
  enabled = base.extendModules { modules = [{ qnoxslab.agentDeploy = {
    enable = true; authorizedKey = lib.mkForce "%s"; fromAddresses = lib.mkForce [ "172.18.0.0/16" ]; }; }]; };
  c = enabled.config;
in {
  defaultDisabled = base.options.qnoxslab.agentDeploy.enable.default == false;
  nasEnabled = base.config.qnoxslab.agentDeploy.enable;
  nasKey = base.config.qnoxslab.agentDeploy.authorizedKey;
  nasFrom = base.config.qnoxslab.agentDeploy.fromAddresses;
  nasFailed = map (a: a.message) (builtins.filter (a: !a.assertion) base.config.assertions);
  keys = c.users.users.agent-deploy.openssh.authorizedKeys.keys;
  sudo = c.security.sudo.extraRules;
  sshd = c.services.openssh.extraConfig;
  shell = toString c.users.users.agent-deploy.shell;
  system = c.users.users.agent-deploy.isSystemUser;
  tmpfiles = c.systemd.tmpfiles.rules;
  failed = map (a: a.message) (builtins.filter (a: !a.assertion) c.assertions);
  baseProduction = base.config.systemd.services ? dealpipe-deploy-production;
  enabledProduction = c.systemd.services ? dealpipe-deploy-production;
}''' % KEY


def evaluate(expr):
    return json.loads(subprocess.check_output(["nix", "eval", "--impure", "--json", "--expr", expr]))


result = evaluate(expression)
assert result["defaultDisabled"], "module must be disabled by default"
# The NAS enables it with the committed T3 public key (public half only).
if result["nasEnabled"]:
    assert result["nasKey"].startswith("ssh-ed25519 ") and result["nasKey"].endswith(" agent-deploy@t3"), result["nasKey"]
    assert result["nasFrom"] == ["172.18.0.0/16"], result["nasFrom"]
    assert not result["nasFailed"], result["nasFailed"]
assert not result["failed"], result["failed"]
assert result["system"]
(key,) = result["keys"]
assert key.startswith('restrict,command="/nix/store/') and '-agent-deploy-ssh",from="172.18.0.0/16" ' in key, key
assert key.endswith(KEY)
rules = [r for r in result["sudo"] if r.get("users") == ["agent-deploy"]]
assert len(rules) == 1 and len(rules[0]["commands"]) == 1, rules
command = rules[0]["commands"][0]
assert command["command"].startswith("/nix/store/") and command["command"].endswith("-agent-deploy-root"), command
assert command["options"] == ["NOPASSWD"]
for line in ("Match User agent-deploy", "ForceCommand /nix/store/", "PermitTTY no", "AllowTcpForwarding no",
             "AllowAgentForwarding no", "X11Forwarding no", "AuthenticationMethods publickey"):
    assert line in result["sshd"], line
assert "d /var/lib/agent-deploy 0700 root root -" in result["tmpfiles"]
assert result["baseProduction"] == result["enabledProduction"], "must not add or remove production units"

# Invalid configuration must fail its assertions, not evaluate silently.
bad = expression.replace(KEY, "ssh-ed25519 AAAA x,command=sh").replace('"172.18.0.0/16"', '"0.0.0.0/0"')
failed = evaluate(bad)["failed"]
assert len(failed) == 2, failed
print("agent-deploy module contract OK")
