#!/bin/sh
# Start the user-managed npm T3 from the persistent home when it works, so
# updates need no image rebuild. Fall back to the image's pinned build.
local_t3="${T3_LOCAL_BIN:-$HOME/.local/bin/t3}"
if [ -x "$local_t3" ]; then
  # The npm launcher downloads and checksum-verifies its release on first use.
  if version=$(timeout "${T3_CHECK_TIMEOUT:-300}" "$local_t3" --version 2>/dev/null); then
    echo "t3-start: using user-managed $version" >&2
    exec "$local_t3" "$@"
  fi
  echo "t3-start: user-managed T3 failed its version check; using the image pin" >&2
fi
exec "${T3_PINNED:?T3_PINNED is set by the image}" "$@"
