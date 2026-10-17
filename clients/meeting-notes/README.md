# Private meeting notes

Summary-only meeting notes in Obsidian. Speech is turned into text on the device
you are using, summarised by a local model, and only the summary is kept. No
audio, no transcript, nothing to the cloud. Works the same on the desktop and on
a laptop away from home: the NAS is only reached over Tailscale for sync.

## Flow

1. Join a call. Press the Meetily hotkey/tray button and name the meeting with a
   prefix: `daily`, `1on1 Alex`, `client Acme`, or anything else (`meeting`).
2. Meetily transcribes mic + system audio live on this device (Parakeet/Whisper).
   Audio saving is off; `meeting-sink` also deletes any audio it finds.
3. Stop Meetily. Within ~2–4 minutes `meeting-sink` (Task Scheduler, every 2 min):
   - sends the transcript to Ollama on `127.0.0.1` (loopback is enforced) with a
     paraphrase-only prompt and a per-type template;
   - writes `Private/Meetings/YYYY/MM/YYYY-MM-DD HHMM <Title>.md` (summary,
     decisions, `- [ ]` action items, open questions; no transcript);
   - erases the meeting from Meetily: rows deleted with SQLite `secure_delete`,
     WAL truncated, meeting folder removed. Unsummarised transcripts are erased
     after 24 h regardless, leaving a stub note.
4. LiveSync syncs the note end-to-end encrypted over Tailscale. The NAS bridge
   drops `Private/` in both directions (`excludePrefixes`), so the NAS holds it
   only as encrypted CouchDB documents: not on disk, not indexed for agents,
   encrypted twice in offsite backups.
5. Copy anything worth keeping long-term into `Brain/` yourself.

Dictation: Handy hotkey types into whatever is focused, including Obsidian.

For people who have not agreed to transcription, skip Meetily and dictate a
short debrief with Handy afterwards (only your own voice is processed).

## Install on a Windows device

```powershell
.\install-windows.ps1 -VaultPath 'G:\obsidian-notes\NAS' -Owner 'Your Name'
```

Installs Python, Ollama, Handy and Meetily (pinned, SHA-256 checked) for the
current user; sets `OLLAMA_NO_CLOUD=1` and a loopback `OLLAMA_HOST`; turns off
Meetily audio saving; picks `qwen3:14b`/`8b`/`4b` by GPU memory; installs
`%LOCALAPPDATA%\meeting-sink` (owner-only ACL) and the scheduled task.

Then, once per device:

1. Meetily settings: analytics **off**, local transcription model, confirm audio
   saving **off**, do not configure a summary provider.
2. Handy: local model + hotkey, start with Windows.
3. Elevated PowerShell: `.\install-windows.ps1 -Firewall` (blocks Meetily and
   Handy outbound after their models are downloaded).
4. After the NAS runs the `Private/` exclusion: `.\install-windows.ps1 -EnableSummaries`.
   Until then no notes are written and transcripts expire after 24 h.
5. Device hygiene: BitLocker (FileVault on macOS) on, Obsidian vault on the
   encrypted disk, and the Claude Code deny rule for the vault's `Private/**`.

### Laptop

Same script and steps; the vault path is that laptop's LiveSync vault. Without a
discrete GPU the installer picks `qwen3:4b`, and Parakeet is the faster choice
for transcription. Away from home everything works offline; notes sync when
Tailscale reaches the NAS. Each device erases its own Meetily data, and note
names carry the time, so two devices never collide.

macOS: `meeting_sink.py` already knows Meetily's macOS paths; schedule it with
launchd (`StartInterval` 120) instead of the Windows installer.

## Checks

- `python3 -m unittest test_meeting_sink` — fake Meetily DB (v0.4.1 schema) and a
  fake loopback Ollama: note written, transcript absent from the note, rows
  zeroed on disk, folder and audio removed, gate and settle time respected.
- Bridge exclusion tests live in `machines/nas/config/obsidian-bridge/compat.patch`
  (`exclude.test.ts`).
- Log: `%LOCALAPPDATA%\meeting-sink\sink.log` (ids and titles only).

When upgrading Meetily, re-check its schema and `recording_preferences.json`
format, then update the pinned version and hash in `install-windows.ps1`.
