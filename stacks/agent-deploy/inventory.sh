#!/usr/bin/env bash
# Read-only NAS inventory (Dealpipe pipeline handover NAS-HOST-BATCH §0).
# Prints names, image IDs, limits and selected non-secret env keys only; it
# never dumps full container environments. Run as root by agent-deploy.
set -euo pipefail

for sock in \
  /run/user/1102/docker.sock \
  /run/user/1102/dealpipe-browser/docker.sock \
  /run/user/1102/dealpipe-ci2/docker.sock
do
  d() { runuser -u dealpipe-ci -- docker --host "unix://$sock" "$@"; }
  printf '\nENGINE: %s\nNETWORK | CONTAINERS | CREATED\n' "$sock"
  # A stopped engine (e.g. the hand-started CI2 canary after a reboot) must not
  # abort the rest of the read-only inventory.
  if ! ids=$(d network ls -q 2>/dev/null); then echo "engine unavailable"; continue; fi
  for id in $ids; do
    d network inspect --format \
      '{{.Name}} | {{len .Containers}} | {{.Created}}' "$id"
  done

  printf '\nCONTAINERS\n'
  d ps -a --format '{{.Names}} | {{.Image}} | {{.Status}}'
  names=$(d ps -a --format '{{.Names}}')
  for name in $names; do
    case "$name" in
      dealpipe-ci-*|dealpipe-nas-ci-2-canary*) ;;
      *) continue ;;
    esac
    d inspect --format \
      '{{.Name}} image={{.Image}} memory={{.HostConfig.Memory}} memorySwap={{.HostConfig.MemorySwap}} nanoCPUs={{.HostConfig.NanoCpus}} project={{index .Config.Labels "com.docker.compose.project"}} service={{index .Config.Labels "com.docker.compose.service"}}' "$name"

    for key in RUNNER_NAME RUNNER_LABEL AGENT_CI_EXCLUSIVE \
      AGENT_CI_PROFILE AGENT_CI_ENGINE_LOCK AGENT_CI_SOCKET_REQUIRED \
      AGENT_CI_EXPIRED_POLICY_FALLBACK AGENT_CI_NETWORK_HYGIENE
    do
      d inspect --format \
        '{{range .Config.Env}}{{$k := index (split . "=") 0}}{{if eq $k "'"$key"'"}}{{println .}}{{end}}{{end}}' \
        "$name"
    done
    d inspect --format \
      '{{range .Mounts}}{{if or (eq .Destination "/var/run/docker.sock") (eq .Destination "/run/agent-workload.lock")}}{{.Source}} -> {{.Destination}}{{println}}{{end}}{{end}}' \
      "$name"
  done
done

printf '\nPARENT LIMITS\n'
for field in memory.max memory.swap.max cpu.max memory.events; do
  printf '%s: ' "$field"
  cat "/sys/fs/cgroup/user.slice/user-1102.slice/$field"
done
awk '/^(MemTotal|MemAvailable):/' /proc/meminfo

printf '\nDEPLOY/PREVIEW CONTROLLERS\n'
systemctl list-units --all --no-legend \
  'dealpipe-deploy-*' 'dealpipe-preview*' |
  awk '{print $1, $3, $4}'

printf '\nPRODUCTION FINGERPRINTS\n'
for file in \
  /etc/systemd/system/dealpipe-deploy-production.service \
  /etc/systemd/system.control/dealpipe-deploy-production.service.d/40-chromium.conf
do
  if [ -f "$file" ]; then sha256sum "$file"; else
    printf 'MISSING: %s\n' "$file"
  fi
done

printf '\nSOURCE/SYSTEM\n'
nixos-version
readlink -f /run/current-system
for checkout in /etc/nixos/homelab /var/tmp/homelab-ci2-review-ac1e2d0; do
  printf '%s: ' "$checkout"
  if [ -e "$checkout/.git" ]; then
    git -C "$checkout" rev-parse HEAD
  else
    printf 'No .git entry; checkout identity UNVERIFIED\n'
  fi
done
