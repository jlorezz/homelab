#!/usr/bin/env python3
"""Build and deploy the runner exclusively through its rootless Portainer environment."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlencode

import yaml

from capacity import render

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
from deploy import Portainer


def require_idle(api, endpoint_id):
    filters = json.dumps({"label": ["com.docker.compose.project=dealpipe-ci"]})
    containers = api.request("GET", f"/endpoints/{endpoint_id}/docker/containers/json?" + urlencode({"filters": filters}))
    for container in containers:
        top = api.request("GET", f"/endpoints/{endpoint_id}/docker/containers/{container['Id']}/top")
        if any("Runner.Worker" in " ".join(row) for row in top.get("Processes", [])):
            raise RuntimeError("A CI lane is executing a job; wait for an idle maintenance window")


def multipart(fields):
    boundary = "homelab-ci-endpoint-20261015"
    body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                   for k, v in fields.items()) + f"--{boundary}--\r\n"
    return body.encode(), f"multipart/form-data; boundary={boundary}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--url", default="https://nas.tailb80645.ts.net:9443")
    parser.add_argument("--connect-address", default="100.125.180.36")
    parser.add_argument("--prepare-only", action="store_true", help="Create environment and build image without registering a runner")
    parser.add_argument("--capacity-file", default="/etc/dealpipe-runner-capacity.json")
    args = parser.parse_args()
    # Fail before API calls or builds if measured host configuration is missing.
    capacity = json.loads(Path(args.capacity_file).read_text())
    rendered = render(yaml.safe_load((Path(__file__).resolve().parent / "compose.yaml").read_text()), capacity)
    directory = Path(__file__).resolve().parent
    api = Portainer(args.url, args.token_file, args.connect_address)
    endpoints = api.request("GET", "/endpoints")
    matches = [e for e in endpoints if e["Name"] == "Dealpipe CI"]
    if len(matches) > 1:
        raise RuntimeError("Multiple CI environments; resolve in Portainer")
    if not matches:
        body, content_type = multipart({"Name": "Dealpipe CI", "EndpointCreationType": 2,
                                       "URL": "tcp://portainer-ci-agent:9001", "ContainerEngine": "docker",
                                       "TLS": "true", "TLSSkipVerify": "true", "TLSSkipClientVerify": "true"})
        endpoint = api.request("POST", "/endpoints", raw_body=body, content_type=content_type)
    else:
        endpoint = matches[0]
    if endpoint["URL"] != "tcp://portainer-ci-agent:9001" or endpoint["Type"] != 2:
        raise RuntimeError("Refusing CI stack deployment without the dedicated agent environment")
    endpoint_id = endpoint["Id"]
    info = api.request("GET", f"/endpoints/{endpoint_id}/docker/info")
    if "name=rootless" not in info.get("SecurityOptions", []):
        raise RuntimeError("Refusing runner deployment: selected engine is not rootless")
    lock_file = Path("/var/lib/agent-workloads/heavy.lock")
    if not lock_file.is_file() or lock_file.is_symlink():
        raise RuntimeError("Provision the host shared workload lock before deploying CI hooks")
    require_idle(api, endpoint_id)
    digest = hashlib.sha256()
    context_files = {name: directory / name for name in (
        "Dockerfile", "entrypoint.sh", "job-hook.py", "custody.py", "worker-custody.c")}
    context_files["workload.py"] = directory.parent / "agents/workload.py"
    for name, path in context_files.items():
        digest.update(name.encode() + b"\0" + path.read_bytes())
    image = "localhost/dealpipe-ci:" + digest.hexdigest()[:16]
    images = api.request("GET", f"/endpoints/{endpoint_id}/docker/images/json")
    if not any(image in (i.get("RepoTags") or []) for i in images):
        print(f"Building runner image through Portainer environment {endpoint_id}...", flush=True)
        with tempfile.TemporaryDirectory(prefix="ci-image-") as tmp:
            archive = Path(tmp) / "context.tar"
            with tarfile.open(archive, "w") as stream:
                for name, path in context_files.items():
                    stream.add(path, arcname=name)
            query = urlencode({"t": image, "dockerfile": "Dockerfile", "rm": "1", "forcerm": "1"})
            result = api.request("POST", f"/endpoints/{endpoint_id}/docker/build?{query}", archive=archive)
        for line in (result.splitlines() if isinstance(result, str) else [json.dumps(result)]):
            record = json.loads(line)
            if record.get("error") or record.get("errorDetail"):
                # No credentials are supplied to the image build.
                raise RuntimeError(f"Runner image build failed: {record.get('error')}")
    print(f"CI environment {endpoint_id}; image {image}", flush=True)
    if args.prepare_only:
        return
    require_idle(api, endpoint_id)
    compose = yaml.safe_dump(rendered, sort_keys=False)
    # Validate before transmitting; the original YAML remains reviewable in Portainer.
    yaml.safe_load(compose)
    environment = [{"name": "RUNNER_IMAGE", "value": image}]
    require_idle(api, endpoint_id)
    stacks = api.request("GET", "/stacks")
    matches = [s for s in stacks if s["EndpointId"] == endpoint_id and s["Name"] == "dealpipe-ci"]
    if len(matches) > 1:
        raise RuntimeError("Multiple CI stacks; resolve in Portainer")
    if matches:
        stack = api.request("PUT", f"/stacks/{matches[0]['Id']}?endpointId={endpoint_id}",
                            {"StackFileContent": compose, "Env": environment, "Prune": False, "PullImage": False})
    else:
        stack = api.request("POST", f"/stacks/create/standalone/string?endpointId={endpoint_id}",
                            {"Name": "dealpipe-ci", "StackFileContent": compose, "Env": environment})
    deadline = time.monotonic() + 180
    while stack["Status"] == 3 and time.monotonic() < deadline:
        time.sleep(5)
        stack = api.request("GET", f"/stacks/{stack['Id']}")
    if stack["Status"] != 1:
        raise RuntimeError("CI stack is not active; inspect it before retrying")
    print(f"Portainer CI stack active (ID {stack['Id']}). Check runner registration and health.")


if __name__ == "__main__":
    main()
