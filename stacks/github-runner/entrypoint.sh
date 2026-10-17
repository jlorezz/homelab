#!/bin/bash
set -euo pipefail
# Keep fetched objects and refs durable across an unclean NAS shutdown.
git config --global core.fsync committed,reference
git config --global core.fsyncMethod fsync
cd /runner
# Registration is persistent per trusted-repository lane. Runner
# auto-updates remain enabled. Recreate the state directory to rebuild it cleanly.
if [[ ! -x ./run.sh ]]; then
  cp -a /opt/actions-runner/. /runner/
fi
if [[ ! -s .runner ]]; then
  if [[ ! -s /run/secrets/registration-token ]]; then
    echo 'No registration token: provision a fresh short-lived token on the NAS.' >&2
    exit 1
  fi
  export ACTIONS_RUNNER_INPUT_TOKEN
  ACTIONS_RUNNER_INPUT_TOKEN=$(cat /run/secrets/registration-token)
  ./config.sh --unattended --url https://github.com/dealpipe/dealpipe \
    --name "${RUNNER_NAME:-dealpipe-nas-ci}" --labels "${RUNNER_LABEL:-dealpipe-nas-ci}" \
    --work "${RUNNER_WORK_DIRECTORY:-/var/lib/dealpipe-ci/work}"
  unset ACTIONS_RUNNER_INPUT_TOKEN
fi
exec ./run.sh
