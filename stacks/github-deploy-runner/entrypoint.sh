#!/usr/bin/env bash
set -euo pipefail
case "${DEPLOY_ENVIRONMENT:?}" in staging|production) ;; *) exit 2 ;; esac
cp -R /opt/actions-runner/. /runner/
# Only a one-hour registration token crosses the container boundary, over stdin.
# The long-lived GitHub credential stays in the host controller's systemd credential.
IFS= read -r ACTIONS_RUNNER_INPUT_TOKEN
export ACTIONS_RUNNER_INPUT_TOKEN
./config.sh --unattended --ephemeral --disableupdate --no-default-labels \
  --url https://github.com/dealpipe/dealpipe --name "${RUNNER_NAME:?}" \
  --labels "dealpipe-deploy-${DEPLOY_ENVIRONMENT}" --work /work
unset ACTIONS_RUNNER_INPUT_TOKEN
exec ./run.sh
