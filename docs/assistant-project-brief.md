# Personal Desktop Assistant — Project Brief for Claude Code

## 1. What this is and why

I'm building a **personal desktop assistant** that lives on my laptop from startup to
shutdown, helps me run my daily dev/work tasks (Python, Flutter, WordPress, Excel
report scripts), takes voice or typed commands, replies in a chat window and via
text-to-speech, manages reminders/scheduled posts, and (later) responds to hand
gestures for OS-level shortcuts. It should feel like a lightweight "buddy" sitting in
the corner of my screen, not a heavyweight app.

I already have a rough skeleton of this in Python. Your job is to **restructure /
extend it** into the architecture below — reuse what's good in the skeleton, replace
what isn't, and keep everything consistent with the constraints in this doc.

**Non-goals (explicitly do NOT do these):**
- No paid APIs anywhere (no OpenAI/Claude API calls, no paid TTS/STT). Everything must
  run free/local or use free-tier-no-key services.
- Do not integrate with Rainmeter programmatically. Rainmeter is purely a desktop skin
  running independently for visual aesthetic — the assistant and Rainmeter never talk
  to each other, no shared files, no plugins.
- Do not build a continuous virtual-mouse/pointer gesture system. Gestures are
  **discrete, classified actions** (e.g. "this is a swipe-left"), not cursor control.

## 2. Hardware constraints (design around this)

- Lenovo ThinkPad T470s, Intel Core i7 7th gen, Intel HD 620 integrated graphics (no
  dGPU), 8GB RAM currently → upgrading to 16GB RAM + 512GB SSD soon.
- No GPU acceleration available, ever. All ML models must run CPU-only.
- STT: `faster-whisper`, **tiny or base model, int8 quantized**. Never medium/large.
- Local LLM (only after RAM upgrade): small quantized models only (e.g.
  Qwen2.5-1.5B-Instruct or Phi-3-mini, Q4_K_M, via Ollama). Expect multi-second
  latency and design the UX (loading indicator in chat) around that.
- Given these constraints, the **rule-based command router is the primary brain**.
  The local LLM is only a fallback for open-ended chat, not for anything actionable.

## 3. Core architecture

### 3.1 Process model
- Runs as a **background process with a system tray icon** (pystray or
  PyQt6/PySide6 `QSystemTrayIcon`), registered to launch on Windows startup
  (Startup folder shortcut or Task Scheduler).
- Left-click tray icon → show/restore the floating widget. Right-click → quick menu
  (quit, mute mic, open settings/JSON).
- The floating widget: **PyQt6/PySide6**, frameless, always-on-top, semi-transparent,
  rounded corners, "tech HUD" styling via QSS. Shows:
  - Live-updating date/time at the top
  - A small animation (Lottie or sprite-based) that stays idle/alive-looking
  - A minimize button that hides to tray (process keeps running)
  - Below that: a scrollable chat log + a text input box (typed commands go through
    the exact same pipeline as voice commands)

### 3.2 Threading — GUI must never freeze
This is a hard requirement. Pattern to implement:

```
Main thread (PyQt GUI)
  - never calls subprocess directly
  - only pushes jobs onto a queue.Queue()
  - immediately echoes "⏳ running <task>..." to chat when a job is enqueued

Worker (QThread / ThreadPoolExecutor, pulling from the queue)
  - runs subprocess.Popen(...) for scripts, flutter, VS Code, browser opens, etc.
  - captures stdout/stderr asynchronously
  - emits a Qt signal (pyqtSignal) back to the main thread on progress/completion

GUI slot receives the signal
  - appends result to chat log
  - triggers TTS output if applicable
```
Never touch widgets from a worker thread directly — always via signals/slots. Two
commands arriving close together should queue, not block the UI.

### 3.3 Command routing
1. Typed or transcribed text arrives at a single entry point: `route_command(text)`.
2. First, match against the rule-based registry (see §4) — trigger phrases,
   fuzzy-matched (simple substring / difflib ratio is enough, no ML needed here).
3. If no match, and a local LLM is configured, fall back to it for open chat.
4. If no LLM configured yet, respond with a friendly "I don't have a command for that
   yet" and optionally suggest adding one to the JSON.

## 4. Configuration — split by intent hierarchy, editable two ways

**Everything actionable is defined in JSON config files, not hardcoded.** As this
grows it should NOT be one giant JSON file — split it by intent, top level (broad,
shared defaults) down to specific (per-domain data). This keeps each file small,
readable, and safe to hand-edit.

The assistant must support editing this config in two equivalent ways, but they are
**not symmetric** — see §4.3:
- **Through chat**: for anything that *creates a new command/trigger*, chat is the
  only path (interactive, multi-turn — see §4.3). For simple value updates (adding a
  reminder with a known format, registering a project after creation, changing a
  default path), a single chat instruction can write directly to the right file.
- **Directly in the JSON file(s)**: I will hand-edit these sometimes, especially for
  bulk changes or fixing something. The assistant should pick up changes without
  requiring an app restart — either by watching the files for changes, or by a
  **manual "refresh" command** (see §4.4) that forces an immediate reread of every
  config file.

### 4.1 File layout — intent hierarchy

```
config/
  core.json          # top-level, broadest: shared defaults used across all domains
  projects.json       # registered flutter/python/other project paths
  scripts.json        # custom runnable commands (scripts, exe's, files) — built via chat wizard
  wordpress.json       # wordpress site links
  personal_links.json  # portfolio, linkedin, etc.
  reminders.json        # scheduled reminders
```

Broadest/most shared settings live in `core.json` at the top of the hierarchy (e.g.
default folders that other files' entries get created *inside*). Everything
domain-specific lives in its own file below that. When adding a new domain later
(e.g. a "wordpress-deploy" domain), add a new file rather than growing an existing
one indefinitely.

`config/core.json`:
```json
{
  "defaults": {
    "flutter_projects_dir": "D:/projects/flutter",
    "python_projects_dir": "D:/projects/python",
    "scripts_dir": "D:/scripts"
  },
  "last_active_path": null
}
```

`config/projects.json`:
```json
{
  "projects": [
    { "name": "myapp", "type": "flutter", "path": "D:/projects/flutter/myapp" },
    { "name": "reportgen", "type": "python", "path": "D:/scripts/reportgen" }
  ]
}
```

`config/scripts.json` (this is where chat-built custom commands live — see §4.3):
```json
{
  "commands": [
    {
      "name": "monthly sales report",
      "trigger": ["generate sales report", "run monthly report"],
      "working_directory": "D:/scripts/reports",
      "target": "sales_report.py",
      "run_as": "python",
      "description": "Runs the sales report script against this month's raw data export"
    }
  ]
}
```

`config/wordpress.json`:
```json
{
  "sites": [
    { "name": "client site admin", "url": "https://clientsite.com/wp-admin" },
    { "name": "client site hosting panel", "url": "https://hosting.example.com" }
  ]
}
```

`config/personal_links.json`:
```json
{
  "portfolio": "https://myportfolio.com",
  "linkedin": "https://linkedin.com/in/myprofile"
}
```

`config/reminders.json`:
```json
{
  "reminders": [
    { "text": "Post on LinkedIn", "schedule": "weekly", "day": "monday", "time": "09:00" },
    { "text": "Update portfolio project list", "schedule": "monthly", "day_of_month": 1, "time": "10:00" }
  ]
}
```

### 4.2 Simple command behaviors (single-turn, not the wizard)
- `"create flutter app <name>"` → `flutter create <name>` run inside
  `core.defaults.flutter_projects_dir` via the worker queue → on success,
  **auto-register** the new project into `projects.json` (no need to ask, since it
  was just created).
- `"create python project <name>"` → same pattern using `python_projects_dir`.
- `"open <name>"` → look up in `projects.json` by name/type → `code <path>` (VS Code)
  or, for flutter, optionally offer `flutter run` too.
- `"save this directory"` (said while I'm mid-conversation about a folder, or after
  a command just ran against one) → stores that path into `core.json →
  last_active_path`, and confirms in chat what it saved. This is the anchor the
  wizard in §4.3 reuses so I don't have to retype paths.
- WordPress: `"open <site name>"` → opens `wordpress.json` entry in default browser
  (`webbrowser.open`). `"open <site name> in <browser>"` → open in that specific
  browser. Nothing more — no login automation, no scraping.
- Personal links: `"open my portfolio"` / `"open my linkedin"` reads from
  `personal_links.json`.
- Reminders: `"remind me to X every <day> at <time>"` or `"remind me to X on
  <date>"` → parsed and appended to `reminders.json`, then registered with
  APScheduler. On fire: push a system notification + chat message + optional TTS
  line.

### 4.3 Adding a NEW command — always an interactive chat wizard

Creating a brand-new trigger/command is **never** a single sentence and never a
direct JSON edit on my end — it always goes through a short back-and-forth in chat,
because the assistant needs several pieces of info it can't infer from one line.
This is the realistic flow to build:

1. I say something like `"add command"` (or `"save this directory"` followed later
   by `"add command"` — both should work, using whatever is currently in
   `last_active_path` as the default directory if one exists).
2. Assistant asks, one question at a time, filling out a `scripts.json` entry:
   - "Which directory/file is this for?" (skip if `last_active_path` already set —
     confirm it instead: "Use D:/scripts/reportgen — the one I saved earlier?")
   - "What should running this command actually do?" — options like: run a script
     file, run an exe, open a project, open a link, or something else I describe
     freely
   - If "run a script/exe": "Which file inside that directory?" (offer to list files
     in the directory so I can just say a number/name instead of typing a path)
   - "What should I say (or type) to trigger this?" — accepts one or more trigger
     phrases
   - Optional: "Anything else it should do after running — notify you, speak a
     result, open something?"
3. Assistant shows a summary ("Here's what I'll save: ...") and asks for
   confirmation before writing to `scripts.json`.
4. On confirmation, it writes the entry and confirms it's live immediately (no
   restart needed).

This wizard pattern is the template — reuse the same "ask → confirm → write" shape
for any future command type that needs more than one piece of info, not just
scripts (e.g. adding a new WordPress site with a name + url is simple enough for a
single line, but adding a script/project trigger always goes through this flow).

### 4.4 Refresh command

Add an explicit command — `"refresh"` / `"reread config"` / `"reload settings"` —
that forces the assistant to immediately reread every file in `config/` from disk
and rebuild its in-memory command table, regardless of whether file-watching is
also implemented. This is the fallback I use after hand-editing JSON directly, so I
never have to restart the app just to pick up a change.

## 5. Reminders/scheduling implementation

- `APScheduler` for scheduling, `SQLite` for persistence across restarts (reminders
  should reload into the scheduler on app start, not just live in the JSON — treat
  JSON as the human-editable source and SQLite as the runtime store, syncing on
  load/edit).
- Recurring (weekly/monthly) and one-off reminders both supported.
- Firing a reminder pushes a job into the same worker queue pattern as everything
  else, so it never blocks the GUI.

## 6. Voice I/O

- **STT**: `faster-whisper` (tiny/base, int8, CPU). Triggered by a global hotkey
  (via the `keyboard` library) — push-to-talk style, not always-on wake word, to keep
  CPU usage low on this hardware.
- **TTS**: `edge-tts` (free, no API key, natural-sounding) for spoken replies, with
  `pyttsx3` as an offline fallback if there's no internet.
- Every voice transcript and every typed command both go through the same
  `route_command()` entry point — no separate code paths.

## 7. Gestures (build last, fully independent module)

- `MediaPipe Hands` (free, offline, CPU-capable) for hand landmark detection.
- Design as **discrete gesture classification**, not continuous pointer tracking —
  this avoids the "click doesn't work" problem from the earlier virtual-mouse attempt.
- Map fixed gestures to fixed OS actions via `pyautogui`/`keyboard`, e.g.:
  - Open palm hold → screenshot
  - Swipe left/right → switch virtual desktop (`Ctrl+Win+Left/Right`)
  - Fist → close active window (`Alt+F4`)
  - (list is extensible — add more mappings in config later, same JSON pattern)
- Runs in its own thread/process so a laggy camera frame never touches the GUI thread.

## 8. Build order (please follow this sequence)

1. Tray icon + floating widget shell (date/time, animation, minimize/restore) — no
   logic yet.
2. Worker thread + job queue backbone with signal-based GUI updates — get this right
   before anything else, everything depends on it.
3. Config loader/writer for the split `config/*.json` files (§4.1) + the "refresh"
   command (§4.4) + project registry commands (create/open flutter/python projects,
   VS Code launch) — highest daily value, build first among features. The
   interactive "add command" wizard (§4.3) can come right after this, since it
   depends on the same loader/writer.
4. Script runner (Excel report scripts) wired through the same worker queue.
5. WordPress site links + personal links (portfolio/LinkedIn) opening in browser.
6. Reminders: APScheduler + SQLite, chat-based and JSON-based creation.
7. TTS output (edge-tts).
8. STT input (faster-whisper, hotkey-triggered).
9. Local LLM fallback for open chat — only wire this in after the RAM upgrade to
   16GB; keep the hook/interface ready earlier but don't require it running.
10. Gestures (MediaPipe) — last, independent module.

## 9. Working style for this project

- Reuse and refactor my existing skeleton where it's sound; don't throw it away
  wholesale — tell me what you're keeping vs. replacing and why.
- Keep each config file small and scoped to its domain (§4.1); don't let one file
  absorb everything. Files should stay human-editable and comment-friendly, since I
  will hand-edit them directly sometimes.
- Simple value changes (registering a project, adding a WordPress link, adding a
  reminder in a known format) can be done via a single chat line or a direct JSON
  edit — keep these two paths in sync.
- **Creating a brand-new trigger/command is chat-only**, via the interactive wizard
  in §4.3 — never a one-line chat instruction and never something I'm expected to
  hand-write into `scripts.json` myself, since it needs multiple pieces of
  information gathered step by step.
- After any hand-edit to the JSON files, `"refresh"` (§4.4) must pick it up
  immediately — treat this as a core reliability requirement, not a nice-to-have.
- No paid services, no cloud API keys, no Rainmeter coupling. Flag it explicitly if a
  library I ask about would violate this.
