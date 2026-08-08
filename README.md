# Nova — personal desktop assistant

A always-on Windows assistant that lives in the system tray with a small
frameless HUD: typed or spoken commands drive your report generators, code
projects, websites, media, files, PC settings and reminders.

Everything actionable is **data, not code** — the JSON files in [config/](config/)
decide what it can do. Add a link, a script or a report there (or teach it one
through chat), say `refresh`, and it works. No restart, no code change.

```powershell
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m assistant.main --setup   # scan this PC, seed config (once)
.\.venv\Scripts\python -m assistant.main           # tray + HUD
```

| Command | What it does |
|---|---|
| `python -m assistant.main` | Tray icon + floating HUD (normal use) |
| `python -m assistant.main --hidden` | Start minimised to tray (used by autostart) |
| `python -m assistant.main --cli` | Same router, in the terminal |
| `python -m assistant.main --setup` | Rescan folders, seed config, exit |
| `python -m assistant.main --legacy-ui` | The previous CustomTkinter window |

**Try first:** `help` · `list reports` · `generate pending penalties report` ·
`open youtube in a new window` · `add command` · `remind me to X every monday at 9am` ·
`setup workspace` · `start with windows` · `refresh`

Global hotkeys: **Ctrl+Alt+Space** summons the window, **Ctrl+Shift+Space** is
push-to-talk.

**[docs/COMMANDS.md](docs/COMMANDS.md)** — every command, hotkeys and gestures.
**[docs/ASSISTANT_GUIDE.md](docs/ASSISTANT_GUIDE.md)** — architecture and design notes.
Original spec: [docs/assistant-project-brief.md](docs/assistant-project-brief.md).

No paid APIs anywhere — TTS is edge-tts (free, keyless), STT is faster-whisper
running locally on CPU, and the optional open-chat fallback is a local Ollama
model that stays off until you switch it on.
