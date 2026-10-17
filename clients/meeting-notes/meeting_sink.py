#!/usr/bin/env python3
"""Turn finished Meetily meetings into summary-only Obsidian notes.

For every meeting Meetily has saved, this script asks the local Ollama for a
paraphrased summary, writes it to the vault's Private/ folder and then erases
the transcript (database rows with secure_delete, meeting folder, audio).
Nothing leaves the machine: Ollama must be a loopback URL.

Run it every few minutes (Task Scheduler / launchd). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import sys
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

APP_ID = "com.meetily.ai"
AUDIO_SUFFIXES = {".mp4", ".m4a", ".wav", ".mp3", ".ogg", ".opus", ".webm", ".flac", ".aac", ".pcm"}
CHUNK_CHARS = 40_000
TYPES = ("daily", "1on1", "client", "meeting")

log = logging.getLogger("meeting-sink")


# --- locations -------------------------------------------------------------

def app_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ["APPDATA"]) / APP_ID
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_ID


def default_recordings_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path.home() / "Music" / "meetily-recordings"
    if system == "Darwin":
        return Path.home() / "Movies" / "meetily-recordings"
    return Path.home() / "Documents" / "meetily-recordings"


def sink_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ["LOCALAPPDATA"]) / "meeting-sink"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "meeting-sink"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "meeting-sink"


# --- config ----------------------------------------------------------------

@dataclass
class Config:
    vault_path: Path
    notes_folder: str
    ollama_url: str
    model: str
    num_ctx: int
    nas_exclusion_confirmed: bool
    settle_minutes: int
    max_age_hours: int
    meetily_db: Path
    recordings_dir: Path
    owner: str

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        cfg = cls(
            vault_path=Path(raw["vault_path"]).expanduser(),
            notes_folder=raw.get("notes_folder", "Private/Meetings"),
            ollama_url=raw.get("ollama_url", "http://127.0.0.1:11434").rstrip("/"),
            model=raw["model"],
            num_ctx=int(raw.get("num_ctx", 32768)),
            nas_exclusion_confirmed=bool(raw.get("nas_exclusion_confirmed", False)),
            settle_minutes=int(raw.get("settle_minutes", 2)),
            max_age_hours=int(raw.get("max_age_hours", 24)),
            meetily_db=Path(raw.get("meetily_db") or app_data_dir() / "meeting_minutes.sqlite").expanduser(),
            recordings_dir=Path(raw.get("recordings_dir") or default_recordings_dir()).expanduser(),
            owner=raw.get("owner", "Me"),
        )
        host = urlparse(cfg.ollama_url).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit(f"ollama_url must be loopback, got {host!r}: transcripts never leave this device")
        if not cfg.notes_folder.lower().startswith("private/"):
            raise SystemExit("notes_folder must live under Private/ (excluded from the NAS disk and index)")
        return cfg


# --- Meetily database --------------------------------------------------------

@dataclass
class Meeting:
    id: str
    title: str
    created_at: datetime
    folder_path: str | None


def parse_time(value: str) -> datetime:
    value = value.strip().replace(" ", "T", 1)
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)  # chrono writes nanoseconds
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def finished_meetings(conn: sqlite3.Connection) -> list[Meeting]:
    # Meetily inserts the meeting row and all segments in one transaction when
    # recording stops, so every row here is a finished meeting.
    rows = conn.execute("SELECT id, title, created_at, folder_path FROM meetings ORDER BY created_at").fetchall()
    return [Meeting(r["id"], r["title"] or "Meeting", parse_time(r["created_at"]), r["folder_path"]) for r in rows]


def transcript_text(conn: sqlite3.Connection, meeting_id: str, owner: str) -> str:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transcripts)")}
    speaker = "speaker" if "speaker" in cols else "NULL"
    order = "COALESCE(audio_start_time, 0), timestamp" if "audio_start_time" in cols else "timestamp"
    lines = []
    for r in conn.execute(
        f"SELECT transcript, {speaker} AS speaker FROM transcripts WHERE meeting_id = ? ORDER BY {order}",
        (meeting_id,),
    ):
        who = owner if r["speaker"] == "mic" else "Others" if r["speaker"] == "system" else "Speaker"
        text = (r["transcript"] or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def own_notes(conn: sqlite3.Connection, meeting_id: str) -> str:
    try:
        row = conn.execute("SELECT notes_markdown FROM meeting_notes WHERE meeting_id = ?", (meeting_id,)).fetchone()
    except sqlite3.OperationalError:
        return ""
    return (row["notes_markdown"] or "").strip() if row else ""


def purge_meeting(conn: sqlite3.Connection, meeting: Meeting, recordings_dir: Path) -> None:
    """Erase every trace of a meeting: rows (zeroed on disk), WAL, folder."""
    conn.execute("PRAGMA secure_delete = ON")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    with conn:
        for table in ("transcripts", "transcript_chunks", "summary_processes", "meeting_notes"):
            if table in tables:
                conn.execute(f"DELETE FROM {table} WHERE meeting_id = ?", (meeting.id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting.id,))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if meeting.folder_path:
        folder = Path(meeting.folder_path).resolve()
        root = recordings_dir.resolve()
        if folder != root and root in folder.parents and folder.is_dir():
            shutil.rmtree(folder)
        elif folder.exists():
            log.warning("meeting %s folder outside %s left in place: %s", meeting.id, root, folder)


def sweep_audio(recordings_dir: Path, settle: timedelta) -> None:
    """Delete stray audio even if Meetily's auto-save gets switched back on."""
    if not recordings_dir.is_dir():
        return
    cutoff = time.time() - settle.total_seconds()
    for path in recordings_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES and path.stat().st_mtime < cutoff:
            path.unlink()
            log.warning("deleted stray audio file %s", path.name)


def enforce_no_audio_preference() -> None:
    prefs_file = app_data_dir() / "recording_preferences.json"
    try:
        data = json.loads(prefs_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    prefs = data.get("preferences")
    if isinstance(prefs, dict) and prefs.get("auto_save") is not False:
        prefs["auto_save"] = False
        prefs_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.warning("Meetily audio saving was on; switched off (takes effect after Meetily restarts)")


# --- summarising -------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "language": {"type": "string"},
        "participants": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "array", "items": {"type": "string"}},
        "yesterday": {"type": "array", "items": {"type": "string"}},
        "today": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "task": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["owner", "task", "due"],
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "language", "participants", "summary", "yesterday", "today",
                 "blockers", "decisions", "action_items", "open_questions"],
}

RULES = """You write private meeting notes for {owner}. Lines marked "{owner}:" are {owner}; \
"Others:" are the other participants. Rules:
- Paraphrase. Never quote anyone verbatim and never reproduce the transcript.
- Keep only what matters later: main points, decisions, commitments, open questions.
- Use the meeting's language (German or English) for all text; set "language" to "de" or "en".
- Participants: only names that are clearly stated; otherwise leave the list empty.
- Action items: owner as a name or "{owner}"; due as YYYY-MM-DD only if a date was agreed, else "".
- Leave any list empty when nothing fits. Do not invent anything."""

GUIDE = {
    "daily": "This is a daily stand-up. Fill yesterday/today/blockers per person "
             "(prefix items with the person's name when known); keep summary to 1-3 points.",
    "1on1": "This is a one-on-one. Focus on feedback, agreements, follow-ups. Leave yesterday/today/blockers empty.",
    "client": "This is a client call. Focus on the client's needs, commitments made by each side, "
              "pricing/scope/timeline facts and next steps. Leave yesterday/today/blockers empty.",
    "meeting": "This is a general meeting. Leave yesterday/today/blockers empty.",
}


def meeting_type(title: str) -> tuple[str, str]:
    """`daily`, `1on1 Alex`, `client Acme`, anything else -> meeting."""
    first, _, rest = title.strip().partition(" ")
    kind = first.lower().rstrip(":")
    if kind in TYPES:
        return kind, rest.strip() or kind.capitalize()
    return "meeting", title.strip() or "Meeting"


def ollama_chat(cfg: Config, system: str, user: str, schema: dict | None) -> str:
    body = {
        "model": cfg.model,
        "stream": False,
        "think": False,
        "keep_alive": "2m",
        "options": {"temperature": 0.2, "num_ctx": cfg.num_ctx},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if schema:
        body["format"] = schema
    req = urllib.request.Request(
        f"{cfg.ollama_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return json.loads(resp.read())["message"]["content"]


def summarise(cfg: Config, kind: str, title: str, when: datetime, transcript: str, notes: str) -> dict:
    system = RULES.format(owner=cfg.owner) + "\n" + GUIDE[kind]
    material = transcript
    if len(material) > CHUNK_CHARS:
        # Long meetings: condense each part into notes first, then summarise those.
        parts = [material[i:i + CHUNK_CHARS] for i in range(0, len(material), CHUNK_CHARS)]
        condensed = [
            ollama_chat(cfg, system, f"Part {n} of {len(parts)} of '{title}'. Write concise paraphrased notes "
                                     f"(points, decisions, commitments, questions):\n\n{part}", None)
            for n, part in enumerate(parts, 1)
        ]
        material = "\n\n".join(condensed)
    user = f"Meeting: {title}\nDate: {when:%A, %Y-%m-%d} (resolve relative dates like 'Monday' from this)\n"
    if notes:
        user += f"\n{cfg.owner}'s own notes during the meeting:\n{notes}\n"
    user += f"\nMeeting content:\n{material}"
    result = json.loads(ollama_chat(cfg, system, user, SCHEMA))
    return {k: result.get(k, [] if SCHEMA["properties"][k]["type"] == "array" else "") for k in SCHEMA["required"]}


# --- notes -------------------------------------------------------------------

def safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")[:80] or "Meeting"


def yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render(kind: str, title: str, when: datetime, s: dict) -> str:
    de = s.get("language") == "de"
    h = {
        "summary": "Zusammenfassung" if de else "Summary",
        "yesterday": "Gestern" if de else "Yesterday",
        "today": "Heute" if de else "Today",
        "blockers": "Blocker" if de else "Blockers",
        "decisions": "Entscheidungen" if de else "Decisions",
        "action_items": "Aufgaben" if de else "Action items",
        "open_questions": "Offene Fragen" if de else "Open questions",
    }
    placeholders = {"me", "others", "speaker", "ich", "andere"}
    participants = [p for p in s["participants"] if p.strip() and p.strip().lower() not in placeholders]
    out = [
        "---",
        f"type: {kind}",
        f"date: {when:%Y-%m-%d}",
        f"time: \"{when:%H:%M}\"",
        "participants: [" + ", ".join(yaml_str(p) for p in participants) + "]",
        f"device: {yaml_str(platform.node())}",
        "source: meetily-summary",
        "tags: [meeting/" + kind + "]",
        "---",
        f"# {title}",
        "",
    ]

    def section(key: str) -> None:
        items = [i for i in s[key] if str(i).strip()]
        if items:
            out.extend([f"## {h[key]}", *[f"- {i}" for i in items], ""])

    order = ["summary", "yesterday", "today", "blockers"] if kind == "daily" else ["summary"]
    for key in order + ["decisions"]:
        section(key)
    tasks = [a for a in s["action_items"] if a.get("task", "").strip()]
    if tasks:
        out.append(f"## {h['action_items']}")
        for a in tasks:
            due = f" (due {a['due']})" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.get("due", "")) else ""
            owner = a.get("owner", "").strip()
            out.append(f"- [ ] {owner + ': ' if owner else ''}{a['task'].strip()}{due}")
        out.append("")
    section("open_questions")
    return "\n".join(out).rstrip() + "\n"


def write_note(cfg: Config, when: datetime, title: str, body: str) -> Path:
    folder = cfg.vault_path / cfg.notes_folder / f"{when:%Y}" / f"{when:%m}"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{when:%Y-%m-%d %H%M} {safe_name(title)}"
    path = folder / f"{stem}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}).md"
        n += 1
    with open(path, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    if path.read_text(encoding="utf-8") != body:
        raise OSError(f"note verification failed: {path}")
    return path


# --- main loop -----------------------------------------------------------------

def process(cfg: Config) -> int:
    enforce_no_audio_preference()
    sweep_audio(cfg.recordings_dir, timedelta(minutes=10))
    if not cfg.meetily_db.exists():
        return 0
    now = datetime.now(timezone.utc)
    done = 0
    written_file = sink_dir() / "written.json"  # ids whose note exists but purge did not finish
    written = set(json.loads(written_file.read_text())) if written_file.exists() else set()
    conn = connect(cfg.meetily_db)
    try:
        for meeting in finished_meetings(conn):
            age = now - meeting.created_at
            if age < timedelta(minutes=cfg.settle_minutes):
                continue
            expired = age > timedelta(hours=cfg.max_age_hours)
            if not cfg.nas_exclusion_confirmed:
                if expired:
                    log.warning("summaries disabled; purging expired meeting %s unsummarised", meeting.id)
                    purge_meeting(conn, meeting, cfg.recordings_dir)
                continue
            if meeting.id in written:
                purge_meeting(conn, meeting, cfg.recordings_dir)
                written.discard(meeting.id)
                written_file.write_text(json.dumps(sorted(written)))
                continue
            kind, title = meeting_type(meeting.title)
            local = meeting.created_at.astimezone()
            try:
                transcript = transcript_text(conn, meeting.id, cfg.owner)
                if not transcript and not own_notes(conn, meeting.id):
                    log.info("meeting %s is empty; purging", meeting.id)
                    purge_meeting(conn, meeting, cfg.recordings_dir)
                    continue
                summary = summarise(cfg, kind, title, local, transcript, own_notes(conn, meeting.id))
                note = write_note(cfg, local, title, render(kind, title, local, summary))
            except Exception as exc:  # retried next run until max_age_hours
                if not expired:
                    log.warning("meeting %s not summarised yet (%s); will retry", meeting.id, type(exc).__name__)
                    continue
                body = (f"---\ntype: {kind}\ndate: {local:%Y-%m-%d}\nsource: meetily-summary\n---\n# {title}\n\n"
                        f"Summary failed for {cfg.max_age_hours} h ({type(exc).__name__}); "
                        "the transcript was erased as configured. Add notes from memory.\n")
                note = write_note(cfg, local, title, body)
            written.add(meeting.id)
            written_file.write_text(json.dumps(sorted(written)))
            purge_meeting(conn, meeting, cfg.recordings_dir)
            written.discard(meeting.id)
            written_file.write_text(json.dumps(sorted(written)))
            log.info("wrote %s and erased meeting %s", note.relative_to(cfg.vault_path), meeting.id)
            done += 1
    finally:
        conn.close()
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=sink_dir() / "config.toml")
    args = parser.parse_args()
    state = sink_dir()
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=state / "sink.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    lock = state / "sink.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 3 * 3600:
            return 0
        lock.unlink()  # stale lock from a crashed run
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        cfg = Config.load(args.config)
        if not cfg.nas_exclusion_confirmed:
            log.info("summaries disabled until nas_exclusion_confirmed = true")
        process(cfg)
        return 0
    except Exception:
        log.exception("run failed")
        return 1
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
