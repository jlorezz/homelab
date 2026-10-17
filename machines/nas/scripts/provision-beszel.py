"""Idempotent Beszel 0.20 provisioning via its local authenticated API.

Bootstrap passwords live only in a root-readable, backed-up state directory.
The ordinary dashboard password can be changed independently of the API account.
"""

import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATE = Path("/var/lib/beszel-secrets")
URL = "http://127.0.0.1:8090"


def prepare(config):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    credentials = STATE / "credentials.json"
    if not credentials.exists():
        # Exclusive creation prevents replacing an established bootstrap identity.
        with open(credentials, "x", opener=lambda p, f: os.open(p, f, 0o600)) as out:
            json.dump({"email": config["email"], "password": secrets.token_urlsafe(36)}, out)
    identity = json.loads(credentials.read_text())
    env = STATE / "hub.env"
    env.write_text(f'USER_EMAIL={identity["email"]}\nUSER_PASSWORD={identity["password"]}\n')
    env.chmod(0o600)


class API:
    token = ""

    def request(self, path, data=None, method=None):
        body = None if data is None else json.dumps(data).encode()
        request = Request(URL + path, body, method=method,
                          headers={"Content-Type": "application/json", "Authorization": self.token})
        try:
            with urlopen(request, timeout=15) as response:
                result = response.read()
                return json.loads(result) if result else None
        except HTTPError as error:
            # API errors may include submitted credentials: do not print their bodies.
            raise RuntimeError(f"Beszel API {request.method} {path}: HTTP {error.code}") from None

    def records(self, collection, filter=""):
        query = urlencode({"perPage": 500, "filter": filter})
        return self.request(f"/api/collections/{collection}/records?{query}")["items"]

    def save(self, collection, data, existing=None):
        path = f"/api/collections/{collection}/records"
        if existing:
            self.request(path + "/" + existing["id"], data, "PATCH")
            return existing
        return self.request(path, data)


def provision(config):
    api = API()
    for attempt in range(30):
        try:
            api.request("/api/health")
            break
        except (URLError, RuntimeError):
            if attempt == 29:
                raise
            time.sleep(2)
    identity = json.loads((STATE / "credentials.json").read_text())
    auth = api.request("/api/collections/_superusers/auth-with-password", {
        "identity": identity["email"], "password": identity["password"],
    })
    api.token = auth["token"]
    users = api.records("users", f'email="{identity["email"]}"')
    if len(users) != 1:
        raise RuntimeError("Expected the bootstrap dashboard user")
    user = users[0]
    api.save("users", {"role": "admin"}, user)

    settings = api.records("user_settings", f'user="{user["id"]}"')
    if settings:
        record = settings[0]
    else:
        record = api.save("user_settings", {"user": user["id"]})
    values = record.get("settings") or {}
    # User explicitly selected dashboard-only alerts. No external messages.
    values.update({"emails": [], "webhooks": [], "chartTime": "1h"})
    api.save("user_settings", {"settings": values}, record)

    key = api.request("/api/beszel/info")["key"]
    key_file = Path("/run/beszel-agent-key/hub.pub")
    key_file.write_text(key + "\n")
    key_file.chmod(0o644)  # Public authentication key, not the hub private key.

    systems = api.records("systems")
    all_alerts = api.records("alerts")
    monitors = api.records("network_monitors")
    managed_file = STATE / "provisioned.json"
    previously_managed = json.loads(managed_file.read_text()) if managed_file.exists() else {}
    managed_monitors = []
    for desired in config["systems"]:
        system = next((s for s in systems if s["host"] == desired["host"]
                       and str(s["port"]) == desired["port"]), None)
        system = api.save("systems", {
            "name": desired["name"], "host": desired["host"],
            "port": desired["port"], "users": [user["id"]],
        }, system)
        for alert in config["alerts"]:
            if not desired["primary"] and alert["name"] not in ("Status", "ContainerHealth"):
                continue
            previous = next((a for a in all_alerts if a["system"] == system["id"]
                             and a["user"] == user["id"] and a["name"] == alert["name"]), None)
            api.save("alerts", dict(alert, system=system["id"], user=user["id"]), previous)
        if desired["primary"]:
            for monitor in config["monitors"]:
                previous = next((m for m in monitors if m["system"] == system["id"]
                                 and m["target"] == monitor["target"]
                                 and m["protocol"] == monitor["protocol"]
                                 and m["port"] == monitor["port"]), None)
                saved = api.save("network_monitors", dict(monitor, system=system["id"],
                                                           interval=60, enabled=True), previous)
                managed_monitors.append(saved["id"])
    for obsolete in set(previously_managed.get("monitors", [])) - set(managed_monitors):
        if any(m["id"] == obsolete for m in monitors):
            api.request(f"/api/collections/network_monitors/records/{obsolete}", method="DELETE")
    managed_file.write_text(json.dumps({"monitors": managed_monitors}))
    managed_file.chmod(0o600)
    print(f'Beszel provisioned: {len(config["systems"])} systems, '
          f'{len(config["monitors"])} application/network probes; dashboard-only alerts')


if __name__ == "__main__":
    configuration = json.loads(Path(sys.argv[-1]).read_text())
    if "--prepare" in sys.argv:
        prepare(configuration)
    else:
        provision(configuration)
