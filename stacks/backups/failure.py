#!/usr/bin/env python3
"""Record failures outside the workload guard, including queue and systemd timeouts."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile


def record_failure(path, action, result):
    if result == "success":
        return
    path = Path(path)
    state = json.loads(path.read_text()) if path.exists() else {}
    state["failure"] = {"at": datetime.now(timezone.utc).isoformat(),
                        "action": action, "service_result": result}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(state, stream, indent=2)
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    temporary.replace(path)


if __name__ == "__main__":
    record_failure(sys.argv[1], sys.argv[2], os.environ.get("SERVICE_RESULT", "unknown"))
