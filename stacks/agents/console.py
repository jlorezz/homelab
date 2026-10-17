#!/usr/bin/env python3
"""Execute a command through Portainer; keep sensitive output in a private file."""

import argparse
import os
import struct

from deploy import Portainer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--url", default="https://nas.tailb80645.ts.net:9443")
    parser.add_argument("--connect-address")
    parser.add_argument("--endpoint", type=int, default=2)
    parser.add_argument("--container", default="homelab-agents-t3-1")
    parser.add_argument("--output-file", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("Supply a command after --")
    api = Portainer(args.url, args.token_file, args.connect_address)
    execution = api.request("POST", f"/endpoints/{args.endpoint}/docker/containers/{args.container}/exec", {
        "AttachStdout": True, "AttachStderr": True, "Cmd": command,
    })
    raw = api.request("POST", f"/endpoints/{args.endpoint}/docker/exec/{execution['Id']}/start", {
        "Detach": False, "Tty": False,
    }, raw=True)
    chunks = []
    offset = 0
    while offset < len(raw):
        header = raw[offset:offset + 8]
        if len(header) != 8 or header[0] not in (0, 1, 2) or header[1:4] != b"\x00\x00\x00":
            raise RuntimeError("Unexpected Portainer exec stream framing")
        size = struct.unpack(">I", header[4:])[0]
        if offset + 8 + size > len(raw):
            raise RuntimeError("Incomplete Portainer exec stream")
        chunks.append(raw[offset + 8:offset + 8 + size])
        offset += 8 + size
    # O_EXCL prevents overwriting another file or following a symlink.
    fd = os.open(args.output_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(b"".join(chunks))
    status = api.request("GET", f"/endpoints/{args.endpoint}/docker/exec/{execution['Id']}/json")
    print(f"Command finished with exit code {status['ExitCode']}; private output saved.")
    return status["ExitCode"] or 0


if __name__ == "__main__":
    raise SystemExit(main())
