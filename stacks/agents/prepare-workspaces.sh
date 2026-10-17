#!/usr/bin/env bash
# Run on the NAS after activating the Nix module. Does not deploy containers.
set -euo pipefail

if [[ $(id -u) != 0 ]]; then
  echo "Run with sudo on the NAS." >&2
  exit 1
fi

agent_state=/var/lib/agent-platform
seed=${1:-https://github.com/jlorezz/homelab.git}
hermes_config=${HERMES_CONFIG_TEMPLATE:-/etc/nixos/homelab/stacks/agents/hermes-config.yaml}
opencode_config=${OPENCODE_CONFIG_TEMPLATE:-/etc/nixos/homelab/stacks/agents/opencode.json}
policy_source=${AGENT_POLICY_SOURCE:-/etc/nixos/homelab/stacks/agents}
for worker in t3 opencode; do
  install -d -m 700 -o agent-platform -g agent-platform "$agent_state/$worker/.config/opencode"
  if [[ ! -e "$agent_state/$worker/.config/opencode/opencode.jsonc" && ! -e "$agent_state/$worker/.config/opencode/opencode.json" ]]; then
    install -m 600 -o agent-platform -g agent-platform "$opencode_config" \
      "$agent_state/$worker/.config/opencode/opencode.jsonc"
  fi
  destination="$agent_state/projects/$worker/homelab"
  if [[ ! -e "$destination" ]]; then
    runuser -u agent-platform -- env HOME="$agent_state/$worker" \
      git clone "$seed" "$destination"
    runuser -u agent-platform -- git -C "$destination" remote set-url origin \
      https://github.com/jlorezz/homelab.git
    runuser -u agent-platform -- git -C "$destination" switch -c "agents/$worker"
  fi
done
if [[ ! -e "$agent_state/projects/hermes/homelab" ]]; then
  runuser -u hermes-agent -- git clone "$seed" "$agent_state/projects/hermes/homelab"
  runuser -u hermes-agent -- git -C "$agent_state/projects/hermes/homelab" remote set-url origin \
    https://github.com/jlorezz/homelab.git
  runuser -u hermes-agent -- git -C "$agent_state/projects/hermes/homelab" switch -c agents/hermes
fi

if [[ ! -e "$agent_state/secrets/opencode-password" ]]; then
  umask 077
  openssl rand -hex 32 > "$agent_state/secrets/opencode-password"
fi
for credential in hermes-dashboard-password hermes-dashboard-secret; do
  if [[ ! -e "$agent_state/secrets/$credential" ]]; then
    umask 077
    openssl rand -hex 32 > "$agent_state/secrets/$credential"
  fi
done

if [[ ! -e "$agent_state/hermes/config.yaml" ]]; then
  install -m 600 -o hermes-agent -g hermes-agent "$hermes_config" "$agent_state/hermes/config.yaml"
fi
python3 "$policy_source/configure.py" --source "$policy_source" --apply
echo "Prepared separate homelab workspaces; no existing checkout was reset."
