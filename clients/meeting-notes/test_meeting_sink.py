"""End-to-end test: fake Meetily DB (v0.4.1 schema) + fake loopback Ollama."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import meeting_sink as ms

SECRET = "PROJEKT-NORDLICHT-geheime-Zahl-4711"
SUMMARY = {
    "title": "Acme", "language": "de", "participants": ["Alex", "Me", "Others"],
    "summary": ["Preismodell besprochen"], "yesterday": [], "today": [], "blockers": [],
    "decisions": ["Pilot startet im November"],
    "action_items": [{"owner": "Me", "task": "Angebot senden", "due": "2026-10-20"}],
    "open_questions": ["Budgetfreigabe?"],
}
SCHEMA = """
CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, folder_path TEXT);
CREATE TABLE transcripts (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, transcript TEXT NOT NULL,
  timestamp TEXT NOT NULL, summary TEXT, action_items TEXT, key_points TEXT, audio_start_time REAL,
  audio_end_time REAL, duration REAL, speaker TEXT);
CREATE TABLE summary_processes (meeting_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, error TEXT, result TEXT);
CREATE TABLE transcript_chunks (meeting_id TEXT PRIMARY KEY, transcript_text TEXT NOT NULL, model TEXT NOT NULL,
  model_name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE meeting_notes (meeting_id TEXT PRIMARY KEY NOT NULL, notes_markdown TEXT, notes_json TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


class FakeOllama(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        reply = json.dumps(SUMMARY) if body.get("format") else "notes"
        out = json.dumps({"message": {"content": reply}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class SinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = self.tmp / "vault"
        self.recordings = self.tmp / "meetily-recordings"
        self.appdata = self.tmp / "appdata"
        self.appdata.mkdir()
        self.db = self.appdata / "meeting_minutes.sqlite"
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.close()
        self.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        FakeOllama.requests = []
        patches = [
            mock.patch.object(ms, "app_data_dir", return_value=self.appdata),
            mock.patch.object(ms, "sink_dir", return_value=self.tmp / "state"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        (self.tmp / "state").mkdir()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def config(self, confirmed=True):
        return ms.Config(
            vault_path=self.vault, notes_folder="Private/Meetings",
            ollama_url=f"http://127.0.0.1:{self.server.server_port}", model="test", num_ctx=8192,
            nas_exclusion_confirmed=confirmed, settle_minutes=2, max_age_hours=24,
            meetily_db=self.db, recordings_dir=self.recordings, owner="Me",
        )

    def add_meeting(self, title, minutes_ago):
        created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        folder = self.recordings / f"{title}_{minutes_ago}"
        folder.mkdir(parents=True)
        (folder / "transcripts.json").write_text(SECRET)
        (folder / "audio.mp4").write_bytes(b"\0" * 64)
        mid = f"meeting-{title}-{minutes_ago}"
        conn = sqlite3.connect(self.db)
        stamp = created.isoformat().replace("+00:00", "Z")
        conn.execute("INSERT INTO meetings VALUES (?,?,?,?,?)", (mid, title, stamp, stamp, str(folder)))
        conn.execute("INSERT INTO transcripts (id, meeting_id, transcript, timestamp, audio_start_time, speaker) "
                     "VALUES (?,?,?,?,?,?)", (f"t-{mid}", mid, f"Wir reden über {SECRET}.", stamp, 1.0, "system"))
        conn.execute("INSERT INTO meeting_notes VALUES (?,?,?,?,?)", (mid, "eigene Notiz", None, stamp, stamp))
        conn.commit()
        conn.close()
        return mid, folder

    def raw_db_bytes(self):
        data = b""
        for f in self.appdata.glob("meeting_minutes.sqlite*"):
            data += f.read_bytes()
        return data

    def test_summarises_writes_note_and_erases_everything(self):
        _, folder = self.add_meeting("client Acme", minutes_ago=5)
        self.assertEqual(ms.process(self.config()), 1)

        notes = list((self.vault / "Private/Meetings").rglob("*.md"))
        self.assertEqual(len(notes), 1)
        self.assertIn("client Acme".split(" ", 1)[1], notes[0].name)
        text = notes[0].read_text(encoding="utf-8")
        self.assertIn("type: client", text)
        self.assertIn('participants: ["Alex"]', text)
        self.assertIn("- [ ] Me: Angebot senden (due 2026-10-20)", text)
        self.assertIn("## Entscheidungen", text)
        self.assertNotIn(SECRET, text)

        # transcript reached only the loopback model, with the user's own notes
        sent = json.dumps(FakeOllama.requests)
        self.assertIn(SECRET, sent)
        self.assertIn("eigene Notiz", sent)
        self.assertIs(FakeOllama.requests[-1]["think"], False)
        self.assertIn("Date: ", FakeOllama.requests[-1]["messages"][1]["content"])

        conn = sqlite3.connect(self.db)
        for table in ("meetings", "transcripts", "meeting_notes"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        conn.close()
        self.assertNotIn(SECRET.encode(), self.raw_db_bytes(), "secure_delete must zero the pages")
        self.assertFalse(folder.exists())

    def test_waits_for_settle_time(self):
        self.add_meeting("daily", minutes_ago=0)
        self.assertEqual(ms.process(self.config()), 0)
        self.assertEqual(FakeOllama.requests, [])

    def test_gate_blocks_notes_but_still_expires_transcripts(self):
        _, fresh = self.add_meeting("daily", minutes_ago=5)
        _, old = self.add_meeting("daily", minutes_ago=25 * 60)
        ms.process(self.config(confirmed=False))
        self.assertFalse((self.vault / "Private").exists())
        self.assertEqual(FakeOllama.requests, [])
        self.assertTrue(fresh.exists())
        self.assertFalse(old.exists())

    def test_rejects_non_loopback_ollama_and_non_private_folder(self):
        cfg = self.tmp / "c.toml"
        cfg.write_text(f'vault_path = "{self.vault.as_posix()}"\nmodel = "m"\nollama_url = "http://nas.qnoxs.lab:11434"\n')
        with self.assertRaises(SystemExit):
            ms.Config.load(cfg)
        cfg.write_text(f'vault_path = "{self.vault.as_posix()}"\nmodel = "m"\nnotes_folder = "Brain/Meetings"\n')
        with self.assertRaises(SystemExit):
            ms.Config.load(cfg)

    def test_forces_audio_saving_off_and_sweeps_stray_audio(self):
        prefs = self.appdata / "recording_preferences.json"
        prefs.write_text(json.dumps({"preferences": {"auto_save": True, "file_format": "mp4"}}))
        stray = self.recordings / "x" / "rec.wav"
        stray.parent.mkdir(parents=True)
        stray.write_bytes(b"\0")
        old = datetime.now().timestamp() - 3600
        os.utime(stray, (old, old))
        ms.process(self.config())
        self.assertIs(json.loads(prefs.read_text())["preferences"]["auto_save"], False)
        self.assertFalse(stray.exists())

    def test_meeting_type_prefixes(self):
        self.assertEqual(ms.meeting_type("daily"), ("daily", "Daily"))
        self.assertEqual(ms.meeting_type("1on1 Alex"), ("1on1", "Alex"))
        self.assertEqual(ms.meeting_type("Client: Acme"), ("client", "Acme"))
        self.assertEqual(ms.meeting_type("client Acme GmbH"), ("client", "Acme GmbH"))
        self.assertEqual(ms.meeting_type("Roadmap sync"), ("meeting", "Roadmap sync"))


if __name__ == "__main__":
    unittest.main()
