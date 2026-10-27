#!/bin/sh
# Install T3 from npm into the persistent home. Run inside the T3 worker as UID 1100.
set -eu
usage() {
  echo "usage: update-t3 [--restart] [nightly|latest|preview|VERSION]" >&2
  exit 2
}
restart=0
channel=nightly
for arg in "$@"; do
  case "$arg" in
    --restart) restart=1 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) channel=$arg ;;
  esac
done
case "$channel" in
  *[!A-Za-z0-9._-]*|"") usage ;;
esac
prefix="${T3_PREFIX:-$HOME/.local}"
workload="${AGENT_WORKLOAD:-agent-workload}"
"$workload" run --queue-timeout 1800 --timeout 600 -- \
  npm install --global --prefix "$prefix" "t3@$channel"
# Downloads the release now and verifies it against the release's SHA256SUMS.
version=$(timeout 300 "$prefix/bin/t3" --version)
echo "Installed $version in $prefix"
if [ "$restart" = 1 ]; then
  echo "Restarting T3; Docker starts it again and t3-start picks the new version." >&2
  exec kill -TERM 1
fi
echo "Restart T3 to use it: update-t3 --restart (drops open sessions), or docker restart homelab-agents-t3-1 on the NAS."
