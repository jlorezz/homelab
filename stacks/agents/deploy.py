#!/usr/bin/env python3
"""Load a Nix-built image and deploy/update its stack through Portainer only."""

import argparse
import http.client
import json
import re
from pathlib import Path
import stat
import socket
import ssl
import sys
import tarfile
import time
from urllib.parse import urlencode, urlsplit

import yaml


class Portainer:
    def __init__(self, url, token_file, connect_address=None, timeout=1800):
        self.url = urlsplit(url)
        if self.url.scheme != "https" or self.url.username or self.url.password:
            raise ValueError("Use an HTTPS Portainer URL without embedded credentials")
        path = Path(token_file)
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("Token file must be private (chmod 600)")
        self.headers = {"X-API-Key": path.read_text().strip()}
        self.connect_address = connect_address
        self.timeout = timeout
        if not self.headers["X-API-Key"]:
            raise ValueError("Token file is empty")

    def request(self, method, route, data=None, archive=None, raw=False, raw_body=None, content_type=None):
        connection = http.client.HTTPSConnection(self.url.hostname, self.url.port, timeout=self.timeout)
        if self.connect_address:
            # Preserve hostname verification, SNI, and Host when MagicDNS is disabled.
            raw_socket = socket.create_connection((self.connect_address, connection.port), timeout=self.timeout)
            try:
                connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=self.url.hostname)
            except Exception:
                raw_socket.close()
                raise
        headers = dict(self.headers)
        body = raw_body if raw_body is not None else (json.dumps(data).encode() if data is not None else None)
        if archive:
            headers["Content-Type"] = "application/x-tar"
            headers["Content-Length"] = str(Path(archive).stat().st_size)
        elif body is not None:
            headers["Content-Type"] = content_type or "application/json"
        try:
            target = self.url.path.rstrip("/") + "/api" + route
            if archive:
                connection.putrequest(method, target)
                for key, value in headers.items():
                    connection.putheader(key, value)
                connection.endheaders()
                with open(archive, "rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        connection.send(chunk)
            else:
                connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            if response.status >= 400:
                # Do not echo API responses: stack errors may contain environment secrets.
                raise RuntimeError(f"Portainer {method} {route}: HTTP {response.status}; inspect Portainer logs")
            if raw:
                return payload
            if not payload:
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload.decode()
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://nas.tailb80645.ts.net:9443")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--connect-address", help="Connect to this IP while verifying the URL hostname's TLS certificate")
    parser.add_argument("--endpoint", type=int, help="Required when Portainer has multiple environments")
    parser.add_argument("--image-archive", required=True)
    parser.add_argument("--stack-directory", type=Path, default=Path(__file__).parent)
    parser.add_argument("--name", default="homelab-agents")
    parser.add_argument("--wait-seconds", type=int, default=600, help="Maximum time to wait for asynchronous Portainer deployment")
    parser.add_argument("--with-opencode", action="store_true")
    parser.add_argument("--opencode-password-file")
    parser.add_argument("--hermes-image", help="Enables Hermes; use a verified image@sha256:digest after setup")
    parser.add_argument("--hermes-password-file", default="/var/lib/agent-platform/secrets/hermes-dashboard-password")
    parser.add_argument("--hermes-session-secret-file", default="/var/lib/agent-platform/secrets/hermes-dashboard-secret")
    args = parser.parse_args()

    if not Path("/var/lib/agent-platform/policy/current/manifest.json").is_file():
        raise ValueError("Run configure-agents --apply on the NAS before deploying the policy-mounted stack")
    lock_file = Path("/var/lib/agent-workloads/heavy.lock")
    if not lock_file.is_file() or lock_file.is_symlink():
        raise ValueError("The host shared workload lock must exist before deploying agents")

    with tarfile.open(args.image_archive, "r:*") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
    tags = manifest[0].get("RepoTags", [])
    if len(tags) != 1 or not tags[0].startswith("localhost/homelab-agents:"):
        raise ValueError("Expected the single homelab agent image built by this flake")
    environment = [{"name": "AGENT_IMAGE", "value": tags[0]}]
    compose = yaml.safe_load((args.stack_directory / "compose.yaml").read_text())
    if args.with_opencode:
        if not args.opencode_password_file:
            parser.error("--with-opencode requires --opencode-password-file")
        password_file = Path(args.opencode_password_file)
        if password_file.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("OpenCode password file must be private (chmod 600)")
        password = password_file.read_text().strip()
        if len(password) < 24:
            raise ValueError("Use an OpenCode password of at least 24 characters")
        environment.append({"name": "OPENCODE_SERVER_PASSWORD", "value": password})
        compose["services"].update(yaml.safe_load((args.stack_directory / "compose.opencode.yaml").read_text())["services"])
    if args.hermes_image:
        if not (re.fullmatch(r"docker.io/nousresearch/hermes-agent@sha256:[0-9a-f]{64}", args.hermes_image)
                or re.fullmatch(r"localhost/homelab-hermes:[0-9a-f]{64}", args.hermes_image)):
            raise ValueError("Use a pinned Hermes digest or the content-tagged image from build-hermes.py")
        environment.append({"name": "HERMES_IMAGE", "value": args.hermes_image})
        for name, filename in [
            ("HERMES_DASHBOARD_PASSWORD", args.hermes_password_file),
            ("HERMES_DASHBOARD_SECRET", args.hermes_session_secret_file),
        ]:
            credential = Path(filename)
            if credential.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise ValueError("Hermes dashboard credential files must be private (chmod 600)")
            secret = credential.read_text().strip()
            if len(secret) < 24:
                raise ValueError("Hermes dashboard credentials must contain at least 24 characters")
            environment.append({"name": name, "value": secret})
        compose["services"].update(yaml.safe_load((args.stack_directory / "compose.hermes.yaml").read_text())["services"])

    api = Portainer(args.url, args.token_file, args.connect_address)
    endpoints = api.request("GET", "/endpoints")
    if args.endpoint is None:
        if len(endpoints) != 1:
            parser.error("Select an environment with --endpoint; IDs: " + ", ".join(str(e["Id"]) for e in endpoints))
        args.endpoint = endpoints[0]["Id"]
    if not any(e["Id"] == args.endpoint for e in endpoints):
        raise ValueError("Selected Portainer environment does not exist")
    stacks = api.request("GET", "/stacks")
    existing = [s for s in stacks if s["Name"] == args.name and s["EndpointId"] == args.endpoint]
    if len(existing) > 1:
        raise ValueError("Multiple matching stacks; resolve in Portainer first")
    images = api.request("GET", f"/endpoints/{args.endpoint}/docker/images/json")
    image_present = any(tags[0] in (image.get("RepoTags") or []) for image in images)
    result = None
    if not image_present:
        print(f"Loading {tags[0]} through Portainer environment {args.endpoint}...", flush=True)
        result = api.request("POST", f"/endpoints/{args.endpoint}/docker/images/load?quiet=false", archive=args.image_archive)
    else:
        print(f"Image already imported: {tags[0]}", flush=True)
    # Docker's image-load API can return errors inside an HTTP 200 NDJSON stream.
    records = result.splitlines() if isinstance(result, str) else [json.dumps(result)]
    for record in records:
        try:
            event = json.loads(record)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and (event.get("error") or event.get("errorDetail")):
            raise RuntimeError("Portainer image import failed; inspect the environment logs")
    content = yaml.safe_dump(compose, sort_keys=False)
    query = urlencode({"endpointId": args.endpoint})
    if existing:
        stack = api.request("PUT", f"/stacks/{existing[0]['Id']}?{query}", {
            "StackFileContent": content, "Env": environment, "Prune": False, "PullImage": False,
        })
    else:
        stack = api.request("POST", f"/stacks/create/standalone/string?{query}", {
            "Name": args.name, "StackFileContent": content, "Env": environment,
        })
    deadline = time.monotonic() + args.wait_seconds
    if stack.get("Status") == 3:
        print(f"Portainer accepted deployment of {args.name}; waiting for completion...", flush=True)
    while stack.get("Status") == 3:
        if time.monotonic() >= deadline:
            raise RuntimeError("Portainer is still deploying; inspect the existing stack before retrying")
        time.sleep(5)
        stack = api.request("GET", f"/stacks/{stack['Id']}")
    if stack.get("Status") != 1:
        raise RuntimeError("Portainer deployment did not become active; inspect the stack's deployment details")
    print(f"Portainer stack active: {args.name} (ID {stack['Id']}); verify container health before use.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, tarfile.TarError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        sys.exit(1)
