"""Idempotent local CouchDB provisioning; never prints credentials."""
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

admin_password = Path(sys.argv[1]).read_text().strip()
sync_password = Path(sys.argv[2]).read_text().strip()
base = "http://127.0.0.1:5984"
auth = "Basic " + base64.b64encode(f"nas-admin:{admin_password}".encode()).decode()


def request(path, method="GET", data=None, allowed=(200, 201, 202)):
    headers = {"Authorization": auth, "Content-Type": "application/json"}
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        if error.code in allowed:
            return None
        raise RuntimeError(f"CouchDB {method} {path}: HTTP {error.code}") from None


for attempt in range(30):
    try:
        request("/")
        break
    except (OSError, RuntimeError):
        if attempt == 29:
            raise RuntimeError("CouchDB did not become ready") from None
        time.sleep(2)

for database in ["_users", "_replicator", "_global_changes", "obsidian"]:
    request("/" + database, "PUT", allowed=(201, 202, 412))
user_path = "/_users/org.couchdb.user:notes-sync"
user = request(user_path, allowed=(200, 404))
if user is None:
    request(user_path, "PUT", {
        "_id": "org.couchdb.user:notes-sync", "name": "notes-sync",
        "type": "user", "roles": [], "password": sync_password,
    })
request("/obsidian/_security", "PUT", {
    "admins": {"names": ["nas-admin"], "roles": []},
    "members": {"names": ["notes-sync"], "roles": []},
})
milestone_path = "/obsidian/_local/obsydian_livesync_milestone"
if request(milestone_path, allowed=(200, 404)) is None:
    request(milestone_path, "PUT", {
        "_id": "_local/obsydian_livesync_milestone",
        "type": "milestoneinfo", "created": int(time.time() * 1000),
        "locked": False, "accepted_nodes": [], "node_chunk_info": {}, "node_info": {},
    })
print("CouchDB provisioned: private obsidian database and restricted sync account")
