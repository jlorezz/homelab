#!/usr/bin/env python3
"""A fail-closed GitHub authorization queue for the T3 coordinator.

This program never starts agents, merges PRs, closes issues, or interprets issue
prose as authorization. All GitHub operations use gh without a shell.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote


DEFAULT_CONFIG = Path(__file__).with_name("coordinator.json")
DEFAULT_STATE = Path.home() / ".local/state/t3-coordinator/claims.json"
DEFAULT_BOARD = Path.home() / ".local/state/t3-coordinator/board.json"
PHASES = ("claimed", "running", "review", "blocked", "delivered", "done", "cancelled")
STATUS = {"claimed": "In Progress", "running": "In Progress", "review": "In Review",
          "blocked": "On Hold", "delivered": "In Review", "done": "Done", "cancelled": "Canceled"}
MARKER = re.compile(r"^<!-- t3-coordinator:v1 (\{[^\n]+\}) -->$", re.MULTILINE)
ISSUE_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/issues/([1-9][0-9]*)\Z")


class QueueError(Exception):
    pass


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise QueueError("timestamps must include a timezone")
    return parsed


def gh(*args, payload=None):
    command = ["gh", *map(str, args)]
    if payload is not None:
        command += ["--input", "-"]
    try:
        result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, check=True, timeout=120)
        value = json.loads(result.stdout) if result.stdout.strip() else {}
        if isinstance(value, dict) and value.get("errors"):
            raise QueueError("GitHub API failed: " + json.dumps(value["errors"]))
        return value
    except subprocess.CalledProcessError as exc:
        raise QueueError(f"gh {' '.join(map(str, args[:3]))} failed: {exc.stderr.strip()}") from exc
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise QueueError(f"gh failed or returned invalid JSON: {exc}") from exc


def read_config(path):
    config = json.loads(Path(path).read_text())
    if config.get("schema_version") != 1 or not config.get("coordinator_thread_id"):
        raise QueueError("config requires schema_version 1 and coordinator_thread_id")
    timestamp(config["enabled_after"])
    if not config.get("approvers") or not config.get("repositories"):
        raise QueueError("config requires explicit approvers and repositories")
    for settings in config["repositories"].values():
        if "coordinator_thread_id" in settings and (not isinstance(settings["coordinator_thread_id"], str)
                                                    or not settings["coordinator_thread_id"].strip()):
            raise QueueError("repository coordinator_thread_id must be a nonempty string when specified")
    return config


def model_route(config, role, risk="uncertain", paths=(), escalations_used=0):
    """Choose an explicit T3 target; never estimate account billing or dispatch."""
    routing = config.get("model_routing")
    if not routing or role not in routing["profiles"]:
        raise QueueError("configured model profile required for this role")
    if escalations_used < 0:
        raise QueueError("escalations-used cannot be negative")
    normalized = []
    for path in paths:
        path = path.removeprefix("./")
        if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise QueueError("owned paths must be repository-relative without traversal")
        normalized.append(path)
    sensitive = [path for path in normalized if any(
        fnmatchcase(path, pattern) for pattern in routing["sensitive_paths"])]
    profile = role
    reason = "bounded role default"
    if role == "escalation" or (role in {"implementation", "review", "research"}
                                and (risk != "routine" or sensitive
                                     or (role in {"implementation", "review"} and not normalized))):
        profile = "escalation"
        reason = ("sensitive owned paths: " + ", ".join(sensitive) if sensitive else
                  "high/uncertain risk or missing implementation/review ownership")
    if profile == "escalation" and escalations_used >= routing["escalation_limit"]:
        raise QueueError("model escalation budget exhausted; report unresolved findings")
    target = routing["profiles"][profile]
    if not target.get("providerInstanceId") or not target.get("model"):
        raise QueueError("model routing target must explicitly name provider and model")
    return {"role": role, "risk": risk, "profile": profile, "target": target,
            "reason": reason, "escalations_used": escalations_used,
            "escalation_limit": routing["escalation_limit"], "actual_cost": "unknown"}


def comments(repository, number):
    # --slurp preserves page boundaries, including pages beyond the first 100.
    pages = gh("api", "--paginate", "--slurp",
               f"repos/{repository}/issues/{number}/comments?per_page=100")
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise QueueError("unexpected paginated comments response")
    return [comment for page in pages for comment in page]


def latest_command(config, entries):
    valid = []
    for entry in entries:
        user = entry.get("user") or {}
        body = entry.get("body", "")
        if user.get("type") != "User" or user.get("login") not in config["approvers"]:
            continue
        if body not in ("/t3 deliver", "/t3 cancel"):
            continue
        if entry.get("created_at") != entry.get("updated_at"):
            continue
        try:
            created = timestamp(entry["created_at"])
            comment_id = int(entry["id"])
        except (KeyError, ValueError, TypeError):
            continue
        if created <= timestamp(config["enabled_after"]):
            continue
        valid.append((created, comment_id, entry))
    return max(valid, key=lambda item: item[:2])[2] if valid else None


def receipts(config, entries, login, issue):
    found = []
    for entry in sorted(entries, key=lambda e: int(e.get("id", 0))):
        if (entry.get("user") or {}).get("login") != login:
            continue
        # Only our final machine-generated line is a receipt. Human-readable
        # summaries may quote issue text containing marker-shaped strings.
        lines = entry.get("body", "").splitlines()
        match = MARKER.fullmatch(lines[-1]) if lines else None
        for match in ([match] if match else []):
            try:
                receipt = json.loads(match.group(1))
                if (receipt.get("schema_version") != 1 or receipt.get("issue") != issue
                        or receipt.get("coordinator_thread_id") != config["coordinator_thread_id"]
                        or receipt.get("phase") not in PHASES
                        or not (isinstance(receipt.get("authorization_comment_id"), int)
                                or isinstance(receipt.get("authorization_id"), str))):
                    continue
                found.append(receipt)
            except (json.JSONDecodeError, AttributeError):
                continue
    return found


def project_id(settings):
    # gh project's owner inference can hide API failures as "unknown owner type".
    query = '''query($owner: String!, $number: Int!) {
      repositoryOwner(login:$owner) {
        ... on Organization { projectV2(number:$number) { id } }
        ... on User { projectV2(number:$number) { id } }
      }
    }'''
    result = gh("api", "graphql", "-f", f"query={query}", "-f",
                f"owner={settings['project_owner']}", "-F", f"number={settings['project_number']}")
    owner = result["data"]["repositoryOwner"]
    project = (owner or {}).get("projectV2")
    if not project or not project.get("id"):
        raise QueueError("configured GitHub project is unavailable; check owner, number and project access")
    return project["id"]


def project_item_nodes(settings, selection):
    project = project_id(settings)
    # 100 is GitHub's maximum connection page size. Fetch only queue fields:
    # gh project item-list also expands unrelated field values and linked data.
    query = '''query($project: ID!, $cursor: String) {
      node(id:$project) { ... on ProjectV2 { items(first:100, after:$cursor) {
        nodes { SELECTION }
        pageInfo { hasNextPage endCursor }
      } } }
    }'''.replace("SELECTION", selection)
    cursor = None
    seen_cursors = set()
    while True:
        arguments = ["api", "graphql", "-f", f"query={query}", "-f", f"project={project}"]
        if cursor:
            arguments += ["-f", f"cursor={cursor}"]
        result = gh(*arguments)
        if result.get("errors"):
            raise QueueError("GitHub project snapshot failed: " + json.dumps(result["errors"]))
        node = result["data"]["node"]
        if not node or "items" not in node:
            raise QueueError("configured GitHub project is unavailable; cannot read queue")
        page = node["items"]
        yield from page["nodes"]
        info = page["pageInfo"]
        if not info["hasNextPage"]:
            break
        cursor = info["endCursor"]
        if not cursor or cursor in seen_cursors:
            raise QueueError("project snapshot pagination did not advance")
        seen_cursors.add(cursor)


def project_items(settings):
    return [{"id": item["id"], "content": {"type": "Issue", "url": item["content"]["url"]}}
            for item in project_item_nodes(settings, "id content { __typename ... on Issue { url } }")
            if (item.get("content") or {}).get("__typename") == "Issue"]


def issue_identity(config, url):
    match = ISSUE_URL.fullmatch(url)
    if not match or match[1] not in config["repositories"]:
        raise QueueError("issue must be an exact github.com issue URL in a configured repository")
    return match[1], int(match[2])


def key_for(issue, authorization_id):
    return f"{issue}#{authorization_id}"


def authorization_id(record):
    return record.get("authorization_id") or record.get("authorization_comment_id")


def authorization_args(args):
    return getattr(args, "authorization_id", None) or getattr(args, "authorization_comment_id", None)


def board_snapshot(config, repository):
    settings = config["repositories"][repository]
    selection = '''id content { ... on Issue { url } }
      fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt } }'''
    snapshots = {}
    for item in project_item_nodes(settings, selection):
        url = (item.get("content") or {}).get("url", "")
        if not ISSUE_URL.fullmatch(url) or ISSUE_URL.fullmatch(url)[1] != repository:
            continue
        field = item.get("fieldValueByName") or {}
        if field.get("name") and not field.get("updatedAt"):
            raise QueueError("Status field timestamp unavailable; cannot authorize Ready transitions")
        snapshots[item["id"]] = {"issue": url, "status": field.get("name"),
                                  "updated_at": field.get("updatedAt")}
    return snapshots


def board_update(config, board_path, repository, initialize=False, *, snapshot=None):
    if repository not in config["repositories"]:
        raise QueueError("repository must be configured")
    if not initialize and config.get("authorization_mode") != "project_ready":
        raise QueueError("project_ready mode must be explicitly configured")
    if snapshot is None:
        snapshot = board_snapshot(config, repository)
    with locked_state(board_path) as state:
        repositories = state.setdefault("repositories", {})
        previous = repositories.get(repository)
        if initialize:
            if previous:
                raise QueueError("board already initialized; never discard authorization history by rebaselining")
            repositories[repository] = {"initialized_at": datetime.now(timezone.utc).isoformat(),
                                        "items": snapshot, "events": {}}
        else:
            if not previous:
                raise QueueError("board-init is required before Ready dispatch")
            for item_id, current in snapshot.items():
                old = previous["items"].get(item_id)
                if current["status"] == "Ready" and (not old or old != current):
                    auth_id = f"ready:{item_id}:{current['updated_at']}"
                    previous["events"].setdefault(auth_id, {
                        "authorization_id": auth_id, "authorization_source": "project_ready",
                        "issue": current["issue"], "project_item_id": item_id,
                        "status_updated_at": current["updated_at"],
                        "observed_at": datetime.now(timezone.utc).isoformat()})
            previous["items"] = snapshot
        atomic_state(board_path, state)
        return {"repository": repository, "initialized": True, "items": len(snapshot),
                "events": len(repositories[repository]["events"])}


def board_repository(config, board_path, repository):
    if config.get("authorization_mode") != "project_ready":
        return None
    board = read_state(board_path).get("repositories", {}).get(repository)
    if not board:
        raise QueueError("board-init is required before Ready dispatch")
    return board


def board_valid(event, snapshot, record=None):
    current = snapshot.get(event["project_item_id"])
    if not current or current["issue"] != event["issue"]:
        return False
    if not record:
        return current["status"] == "Ready" and current["updated_at"] == event["status_updated_at"]
    return progress_status_valid(current, record)


def progress_status_valid(current, record):
    # Continue only from our most recently recorded Status. On Hold pauses;
    # human Done/Review changes require reconciliation rather than overwriting.
    return bool(current and current.get("status") in {STATUS[record["phase"]], "On Hold"})


def repository_config(config, repository):
    thread = config["repositories"][repository].get("coordinator_thread_id", config["coordinator_thread_id"])
    if not isinstance(thread, str) or not thread.strip():
        raise QueueError("repository coordinator_thread_id must be a nonempty string")
    return {**config, "coordinator_thread_id": thread}


def read_state(path):
    if not path.exists():
        return {}
    state = json.loads(path.read_text())
    if not isinstance(state, dict):
        raise QueueError("invalid claims state; recover manually")
    return state


def atomic_state(path, state):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            os.chmod(temporary, 0o600)
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


@contextmanager
def locked_state(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state(path)
        yield state


def scan(config, state_path, repository_filter=None, board_path=DEFAULT_BOARD, *, snapshots=None):
    if repository_filter and repository_filter not in config["repositories"]:
        raise QueueError("repository filter must name a configured repository")
    login = gh("api", "user")["login"]
    state = read_state(state_path)  # scan deliberately creates no directories or locks
    output = {"coordinator_thread_id": config["coordinator_thread_id"], "candidates": [],
              "active": [], "interrupted_claims": []}
    for repository, settings in config["repositories"].items():
        if repository_filter and repository != repository_filter:
            continue
        scoped = repository_config(config, repository)
        board = board_repository(config, board_path, repository)
        snapshot = ((snapshots[repository] if snapshots is not None and repository in snapshots
                     else board_snapshot(config, repository)) if board else {})
        if repository_filter:
            output["coordinator_thread_id"] = scoped["coordinator_thread_id"]
        local = {r["issue"]: {} for r in state.values()
                 if ISSUE_URL.fullmatch(r.get("issue", ""))
                 and ISSUE_URL.fullmatch(r["issue"])[1] == repository}
        for r in state.values():
            if r.get("issue") in local:
                local[r["issue"]][authorization_id(r)] = r
                if r.get("pending"):
                    output["interrupted_claims"].append(r)
        project_entries = ([{"id": item_id, "content": {"type": "Issue", "url": item["issue"]}}
                            for item_id, item in snapshot.items()] if board else project_items(settings))
        items = {i["content"]["url"]: i for i in project_entries
                 if (i.get("content") or {}).get("type") == "Issue"
                 and ISSUE_URL.fullmatch(i["content"].get("url", ""))
                 and ISSUE_URL.fullmatch(i["content"]["url"])[1] == repository}
        open_pages = gh("api", "--paginate", "--slurp",
                        f"repos/{repository}/issues?state=open&per_page=100")
        if not isinstance(open_pages, list) or any(not isinstance(p, list) for p in open_pages):
            raise QueueError("unexpected paginated issues response")
        open_issues = {i["html_url"]: i for page in open_pages for i in page if "pull_request" not in i}
        # The repository comments endpoint avoids one request per issue. Every
        # eligible authorization/receipt was created after enabled_after. Paginate
        # all matching comments; no search-index freshness dependency.
        pages = gh("api", "--paginate", "--slurp",
                   f"repos/{repository}/issues/comments?since={quote(config['enabled_after'], safe='')}&per_page=100")
        if not isinstance(pages, list) or any(not isinstance(p, list) for p in pages):
            raise QueueError("unexpected paginated repository comments response")
        grouped = {}
        for page in pages:
            for entry in page:
                number = entry.get("issue_url", "").rsplit("/", 1)[-1]
                if number.isdigit():
                    grouped.setdefault(f"https://github.com/{repository}/issues/{number}", []).append(entry)
        # Include local active work even when someone closes/removes the issue or
        # deletes/edits the authorization. It must be reconciled, never forgotten.
        urls = set(open_issues).intersection(items) | set(local) | set(grouped)
        for url in sorted(urls):
            issue = open_issues.get(url)
            item = items.get(url)
            entries = grouped.get(url, [])
            command = latest_command(config, entries)
            known = receipts(scoped, entries, login, url)
            latest = {authorization_id(r): r for r in known}
            for auth_id, record in local.get(url, {}).items():
                # Local pending writes always remain visible. Otherwise GitHub's
                # append-only receipt chronology is the durable recovery source.
                if record.get("pending") or auth_id not in latest:
                    latest[auth_id] = record
            common = {"issue": url, "repository": repository,
                      "title": issue["title"] if issue else "Locally tracked issue (closed or unavailable)",
                      "project_item_id": item["id"] if item else None,
                      "base": settings["base"], "checkout": settings["checkout"]}
            issue_events = [event for event in board["events"].values() if event["issue"] == url] if board else []
            newest_event = max(issue_events, key=lambda event: timestamp(event["status_updated_at"])) if issue_events else None
            command_newer = bool(command and (not newest_event or
                                 timestamp(command["created_at"]) >= timestamp(newest_event["status_updated_at"])))
            for auth_id, record in latest.items():
                if record["phase"] in ("done", "cancelled") and not record.get("pending"):
                    continue
                event = board["events"].get(auth_id) if board else None
                current = snapshot.get(event["project_item_id"] if event else common["project_item_id"], {}) if board else {}
                valid = (board_valid(event, snapshot, record) and event is newest_event and not command_newer if event else
                         bool(command_newer and command["body"] == "/t3 deliver" and command["id"] == auth_id))
                cancel = bool(command and command["body"] == "/t3 cancel" and
                              (not event or timestamp(command["created_at"]) >= timestamp(event["status_updated_at"])))
                if board:
                    cancel = cancel or current.get("status") in ("Backlog", "Canceled") or not current
                    valid = valid and progress_status_valid(current, record)
                identity = {"authorization_id": auth_id, "authorization_source": "project_ready"} if event else {"authorization_comment_id": auth_id}
                output["active"].append({**common, **identity,
                    "receipt": record, "cancel_requested": cancel,
                    "paused": bool(board and (current.get("status") == "On Hold" or not progress_status_valid(current, record))),
                    "authorization_valid": valid and not cancel, "issue_open": bool(issue), "project_member": bool(item)})
            if (issue and item and command and command["body"] == "/t3 deliver"
                    and command_newer and int(command["id"]) not in latest):
                output["candidates"].append({**common, "authorization_comment_id": int(command["id"])})
            if board and issue and item:
                for auth_id, event in board["events"].items():
                    if event is not newest_event or command_newer or auth_id in latest or not board_valid(event, snapshot):
                        continue
                    if command and command["body"] == "/t3 cancel" and timestamp(command["created_at"]) >= timestamp(event["status_updated_at"]):
                        continue
                    output["candidates"].append({**common, **event})
    return output


def status_target(settings, phase):
    project = project_id(settings)
    query = '''query($project: ID!) {
      node(id:$project) { ... on ProjectV2 { fields(first:100) {
        totalCount nodes { ... on ProjectV2SingleSelectField { id name options { id name } } }
      } } }
    }'''
    result = gh("api", "graphql", "-f", f"query={query}", "-f", f"project={project}")
    node = result["data"]["node"]
    if not node or "fields" not in node:
        raise QueueError("configured GitHub project is unavailable; cannot resolve Status")
    fields = node["fields"]
    if fields["totalCount"] > len(fields["nodes"]):
        raise QueueError("project fields response is incomplete")
    status = next((f for f in fields["nodes"] if f and f.get("name") == "Status"), None)
    option = next((o for o in (status or {}).get("options", []) if o["name"] == STATUS[phase]), None)
    if not status or not option:
        raise QueueError(f"existing project Status option {STATUS[phase]!r} is required")
    return project, status["id"], option["id"]


def receipt(config, state_path, args):
    repository, number = issue_identity(config, args.issue)
    config = repository_config(config, repository)
    settings = config["repositories"][repository]
    login = gh("api", "user")["login"]
    if login not in config["approvers"]:
        raise QueueError("authenticated GitHub account must be an allowlisted coordinator operator")
    item = next((i for i in project_items(settings)
                 if (i.get("content") or {}).get("url") == args.issue
                 and (i.get("content") or {}).get("type") == "Issue"), None)
    if not item:
        raise QueueError("issue must already belong to the configured project")
    target = status_target(settings, args.phase)  # resolve before reserving or writing
    auth_id = authorization_args(args)
    if auth_id is None:
        raise QueueError("authorization identity is required")
    board = board_repository(config, getattr(args, "board_file", DEFAULT_BOARD), repository)
    event = board["events"].get(auth_id) if board else None
    if isinstance(auth_id, str) and not event:
        raise QueueError("unknown board authorization; board-tick must persist the Ready event first")
    snapshot = board_snapshot(config, repository) if board else {}
    key = key_for(args.issue, auth_id)
    with locked_state(state_path) as state:
        entries = comments(repository, number)
        known = [r for r in receipts(config, entries, login, args.issue)
                 if authorization_id(r) == auth_id]
        previous = state.get(key)
        prior_record = known[-1] if known else previous
        issue = gh("api", f"repos/{repository}/issues/{number}")
        if args.phase not in ("done", "cancelled") and issue.get("state") != "open":
            raise QueueError("issue is closed; do not dispatch or continue implementation")
        command = latest_command(config, entries)
        issue_events = [e for e in board["events"].values() if e["issue"] == args.issue] if board else []
        newest_event = max(issue_events, key=lambda e: timestamp(e["status_updated_at"])) if issue_events else None
        auth = next((e for e in entries if e.get("id") == auth_id), None)
        if event and event["issue"] != args.issue:
            raise QueueError("board authorization belongs to a different issue")
        # GitHub's project workflow can set Done when a PR merges, before our
        # wrap-up receipt. This permits only the done operation through the
        # status guard; confirmed mergedAt verification below is still required.
        external_done = bool(args.phase == "done" and prior_record and
                             snapshot.get(item["id"], {}).get("status") == "Done")
        if event and args.phase != "cancelled":
            if (event is not newest_event or
                    (command and timestamp(command["created_at"]) >= timestamp(event["status_updated_at"]))):
                raise QueueError("board authorization was superseded or cancelled; stop the worker")
            if not external_done and not board_valid(event, snapshot, (known[-1] if known else previous)):
                raise QueueError("board authorization revoked: Ready transition or active status no longer valid")
            current = snapshot[event["project_item_id"]]
            if current["status"] == "On Hold" and args.phase not in ("blocked", "done"):
                raise QueueError("board task is paused On Hold; record blocked or cancel before continuing")
            if command and command["body"] == "/t3 cancel" and timestamp(command["created_at"]) >= timestamp(event["status_updated_at"]):
                raise QueueError("authorization was cancelled; stop the worker")
        if board and prior_record and args.phase != "cancelled":
            current = snapshot.get(item["id"])
            if not external_done and not progress_status_valid(current, prior_record):
                raise QueueError("project Status differs from latest receipt; pause and reconcile before continuing")
            if current["status"] == "On Hold" and args.phase not in ("blocked", "done"):
                raise QueueError("board task is paused On Hold; record blocked or cancel before continuing")
        if not event and args.phase != "cancelled" and (not auth or latest_command(config, [auth]) != auth or auth["body"] != "/t3 deliver"):
            raise QueueError("authorization comment is absent, edited, or not an eligible /t3 deliver")
        if not event and args.phase != "cancelled" and (not command or command["id"] != auth_id
                                          or command["body"] != "/t3 deliver"):
            raise QueueError("authorization was superseded or cancelled; stop the worker")
        if not event and args.phase != "cancelled" and newest_event and timestamp(newest_event["status_updated_at"]) > timestamp(auth["created_at"]):
            raise QueueError("comment authorization was superseded by a newer Ready transition")
        if previous and previous.get("pending"):
            raise QueueError("interrupted receipt reservation; inspect GitHub/task state and use recover explicitly")
        if args.phase == "claimed" and (known or previous):
            raise QueueError("authorization already consumed; a new /t3 deliver is required")
        if args.phase == "claimed":
            prior = {authorization_id(r): r for r in receipts(config, entries, login, args.issue)}
            for r in state.values():
                if r.get("issue") == args.issue and authorization_id(r) not in prior:
                    prior[authorization_id(r)] = r
            if any(r["phase"] not in ("done", "cancelled") or r.get("pending") for r in prior.values()):
                raise QueueError("previous authorization still has active work; reconcile it before a new claim")
        prior_phase = (known[-1] if known else previous or {}).get("phase")
        if prior_phase in ("done", "cancelled"):
            raise QueueError("authorization is terminal; a new /t3 deliver is required")
        if args.phase != "claimed" and not (known or previous):
            raise QueueError("claim this authorization before recording progress")
        history = [*known, *([previous] if previous else [])]
        original_time = (event["status_updated_at"] if event else
                         auth["created_at"] if auth else
                         (known[0] if known else previous or {}).get("recorded_at"))
        preserve_ready = bool(args.phase == "cancelled" and newest_event and original_time
            and timestamp(newest_event["status_updated_at"]) > timestamp(original_time)
            and board_valid(newest_event, snapshot)
            and not (command and command["body"] == "/t3 cancel"
                     and timestamp(command["created_at"]) >= timestamp(newest_event["status_updated_at"])))
        task_id = args.task_id or next((r["task_id"] for r in reversed(history) if r.get("task_id")), None)
        pr_url = args.pr or next((r["pr"] for r in reversed(history) if r.get("pr")), None)
        if args.phase in ("delivered", "done") and not pr_url:
            raise QueueError("delivered/done receipts require --pr")
        if pr_url:
            if not re.fullmatch(r"https://github\.com/" + re.escape(repository) + r"/pull/[1-9][0-9]*", pr_url):
                raise QueueError("PR URL must belong to the issue repository")
            if args.phase == "done":
                pr = gh("pr", "view", pr_url, "--json", "state,mergedAt")
                if pr.get("state") != "MERGED" or not pr.get("mergedAt"):
                    raise QueueError("done requires a confirmed merged PR")
        record = {"schema_version": 1, "coordinator_thread_id": config["coordinator_thread_id"],
                  "issue": args.issue, "authorization_comment_id": auth_id if isinstance(auth_id, int) else None,
                  "authorization_id": auth_id if isinstance(auth_id, str) else None,
                  "authorization_source": "project_ready" if event else "issue_comment",
                  "phase": args.phase, "task_id": task_id, "pr": pr_url,
                  "project_status_preserved": "Ready" if preserve_ready else None,
                  "summary": args.summary, "recorded_at": datetime.now(timezone.utc).isoformat()}
        state[key] = {**record, "pending": True}
        atomic_state(state_path, state)  # reserve before any remote mutation; never expire automatically
        body = (f"T3 coordinator: **{args.phase}**\n\n{args.summary}\n\n"
                + (f"PR: {pr_url}\n\n" if pr_url else "")
                + "<!-- t3-coordinator:v1 " + json.dumps(record, separators=(",", ":")) + " -->")
        posted = gh("api", "--method", "POST", f"repos/{repository}/issues/{number}/comments",
                    payload={"body": body})
        if not preserve_ready:
            gh("project", "item-edit", "--id", item["id"], "--project-id", target[0],
               "--field-id", target[1], "--single-select-option-id", target[2], "--format", "json")
        state[key] = {**record, "pending": False, "comment_url": posted["html_url"]}
        atomic_state(state_path, state)
        return state[key]


def recover(config, state_path, args):
    issue_identity(config, args.issue)
    key = key_for(args.issue, authorization_args(args))
    with locked_state(state_path) as state:
        record = state.get(key)
        if not record or not record.get("pending"):
            raise QueueError("only an interrupted local reservation can be recovered")
        # Keep the claim consumed even if the post never reached GitHub. Recovery
        # unlocks explicit progress recording; it never silently requeues work.
        record.update(pending=False, recovery_reason=args.reason,
                      recovered_at=datetime.now(timezone.utc).isoformat())
        atomic_state(state_path, state)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--board-file", type=Path, default=DEFAULT_BOARD)
    commands = parser.add_subparsers(dest="command", required=True)
    route_parser = commands.add_parser("route", help="select an explicit worker model without dispatch")
    route_parser.add_argument("--role", required=True, choices=("coordinator", "implementation", "review", "research", "verification", "escalation"))
    route_parser.add_argument("--risk", choices=("routine", "high", "uncertain"), default="uncertain")
    route_parser.add_argument("--path", action="append", default=[])
    route_parser.add_argument("--escalations-used", type=int, default=0)
    scan_parser = commands.add_parser("scan", help="read-only authorized queue and interrupted claims")
    scan_parser.add_argument("--repository", help="scan only this configured repository")
    for name in ("board-init", "board-tick"):
        board_parser = commands.add_parser(name, help="baseline board or persist Ready transitions")
        board_parser.add_argument("--repository", required=True)
    progress = commands.add_parser("receipt", help="reserve authorization, post receipt, update project Status")
    progress.add_argument("--issue", required=True)
    progress_auth = progress.add_mutually_exclusive_group(required=True)
    progress_auth.add_argument("--authorization-comment-id", type=int)
    progress_auth.add_argument("--authorization-id")
    progress.add_argument("--phase", choices=PHASES, required=True)
    progress.add_argument("--task-id")
    progress.add_argument("--pr")
    progress.add_argument("--summary", required=True)
    recovery = commands.add_parser("recover", help="explicitly unlock interrupted progress without requeuing")
    recovery.add_argument("--issue", required=True)
    recovery_auth = recovery.add_mutually_exclusive_group(required=True)
    recovery_auth.add_argument("--authorization-comment-id", type=int)
    recovery_auth.add_argument("--authorization-id")
    recovery.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        config = read_config(args.config)
        if args.command == "route":
            result = model_route(config, args.role, args.risk, args.path, args.escalations_used)
        elif args.command in ("board-init", "board-tick"):
            snapshot = board_snapshot(config, args.repository)
            result = board_update(config, args.board_file, args.repository, args.command == "board-init",
                                  snapshot=snapshot)
            if args.command == "board-tick":
                result = scan(config, args.state_file, args.repository, args.board_file,
                              snapshots={args.repository: snapshot})
        elif args.command == "scan":
            result = scan(config, args.state_file, args.repository, args.board_file)
        elif args.command == "receipt":
            result = receipt(config, args.state_file, args)
        else:
            result = recover(config, args.state_file, args)
        print(json.dumps(result, indent=2))
    except (QueueError, OSError, KeyError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
