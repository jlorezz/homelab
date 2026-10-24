#!/usr/bin/env bash
# Offline evaluation only. Does not build images, activate NixOS, or start workers.
set -euo pipefail
base=${1:?Usage: production-equivalence.sh BASE_COMMIT OUTPUT_DIRECTORY}
out=${2:?}
root=$(git rev-parse --show-toplevel)
mkdir -p "$out"
out=$(cd "$out" && pwd)
command -v nix >/dev/null || { echo 'Nix unavailable; evaluated production equivalence UNVERIFIED' >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git archive "$base" | tar -x -C "$tmp"
# Equal synthetic image/configuration inputs expose the otherwise disabled production
# unit. These numbers are TEST FIXTURES, never measured host capacity or approval.
for side in before after; do
  source=$root
  if [ "$side" = before ]; then source=$tmp; fi
  nix eval --offline --impure --raw --expr "
    let f = builtins.getFlake \"path:$source\";
        baseSystem = f.nixosConfigurations.nas;
        hasCapacity = baseSystem.options.qnoxslab ? githubRunnerCapacity;
        system = baseSystem.extendModules { modules = [
          ({ lib, ... }: {
            qnoxslab.githubDeploymentRunners = {
              enable = lib.mkForce true;
              environments = lib.mkForce [ \"production\" ];
              image = lib.mkForce \"sha256:$(printf '%064d' 0)\";
            };
          } // lib.optionalAttrs hasCapacity {
            qnoxslab.githubRunnerCapacity = {
              enable = true;
              measuredHeadroomMiB = 131072;
              measuredTotalMiB = 131072;
              reserveMiB = 32768;
              measuredCpuPercent = 3200;
            };
          }) ]; };
    in system.config.systemd.units.\"dealpipe-deploy-production.service\".text
  " > "$out/production-$side.service"
done
cmp "$out/production-before.service" "$out/production-after.service"
echo 'Evaluated production unit: byte-identical (synthetic equal inputs). Live drift still requires operator verification.'
