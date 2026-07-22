# Nova - Personal Windows Automation Assistant

A from-scratch, working rebuild of this repo's old prototype. Everything
below reflects what's actually implemented and tested, not aspirational
features - see the "Known limitations" section for the honest gaps.

## 1. What changed and why

The original code (now in `legacy_old/`) never actually ran:

- `main.py`/`chatbot.py` imported `from Zoemain import sophie as s` - that
  module doesn't exist anywhere in the repo.
- `chatbot.py` called `openai.Completion.create(engine="davinci-codex", ...)`
  - an API OpenAI retired years ago.
- `functions.py` used `chatterbot`, a library that's been unmaintained since
  2021 and doesn't install cleanly on modern Python.
- Paths were hardcoded to a *different machine*: `E:\...JARVIS SERIES...`,
  `C:\Users\hp\AppData\...`, `D://Projects//project AI`, `D://SnapTube Audio`.
- `VM.py` and `virtual mouse.py` were duplicates of the same broken script;
  same for `automation.py` / `automation Protocols.py`.
- The virtual mouse's "pinch to click" math was
  `sqrt((index_x - thumb_x)**2 + (index_x - thumb_x)**2)` - both terms use
  the *x* coordinate for both axes, and it's compared against a fixed pixel
  threshold that means something different depending on how far your hand
  is from the camera. It didn't reliably detect a pinch.
- The `.venv` had nothing installed but `pip`/`setuptools`/`wheel` - the
  project had never been run end-to-end.

The rebuild lives entirely under `assistant/` and is a normal installable
Python package with tests. Nothing hardcodes a path that's specific to one
machine - everything configurable lives in `%APPDATA%\NovaAssistant\config.json`,
editable from the in-app Settings dialog.

Per your call on scope: **no cloud LLM** (all commands are handled by a
local keyword/pattern router - no API key needed), and the old **face-login
and text-emotion-classifier features were dropped** rather than rebuilt.

## 2. Architecture

```
assistant/
  config.py            AppConfig - all user settings, persisted to
                        %APPDATA%\NovaAssistant\config.json
  logging_setup.py       Console + rotating file log
                        (%APPDATA%\NovaAssistant\assistant.log)
  core/
    router.py            CommandRouter - keyword/regex -> handler registry
    commands.py            All ~48 built-in commands, registered here
    context.py              Bundles every subsystem (Context) passed to handlers
    speech.py                TextToSpeech (pyttsx3) + SpeechListener (SpeechRecognition)
  automation/
    windows_ctl.py      Window management, lock, shutdown/restart, screenshot
    chrome_ctl.py         Chrome tab/window hotkeys + YouTubeController
    apps.py                 Launch apps by name (config-driven registry)
    files.py                 Directory/file operations
    shell.py                  Allowlisted cmd/PowerShell execution
    system_info.py       CPU/RAM/disk/battery via psutil
  vision/
    virtual_mouse.py    Hand-tracking cursor control (mediapipe)
  integrations/
    web.py                    Wikipedia, Google/YouTube search, weather, IP/geo, jokes
    music.py                Local audio playback
  ui/
    app.py                    CustomTkinter main window
    settings_dialog.py Settings editor
  main.py                       Entry point
tests/                              15 unit tests (router, files, shell logic)
requirements.txt / requirements-dev.txt
legacy_old/                       The original prototype, kept for reference only
```

**Why a router instead of another if/elif chain:** every command is a
small function registered with `@router.register(name, keywords=..., help=...)`
in `assistant/core/commands.py`. Longer/more specific keyword phrases
automatically win over shorter generic ones (e.g. "open website" beats
"open"), and anything needing structured arguments (`rename X to Y`,
`chrome switch tab to 3`) uses a regex `pattern=` instead. Adding a new
command means writing one function - nothing else in the app changes.

**Threading model (why the UI won't freeze or lag):** Tkinter/CustomTkinter
is not thread-safe, so background work (command dispatch, mic capture, the
webcam loop) never touches a widget directly. Background threads push
events onto a `queue.Queue`; a single `after()`-scheduled poller on the
main thread drains it and is the only code that updates the window. The
virtual mouse's camera loop runs on its own thread, throttled to a target
FPS via delta timing (default 30 FPS, configurable), independent of the UI
thread and independent of how fast the webcam/CPU could otherwise run it.

## 3. Running it

```powershell
# from the repo root, using the existing venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m assistant.main
```

For development (adds pytest):

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest tests/
```

Both of these were run during this rebuild: all 52 packages resolved and
installed cleanly for this machine's Python 3.13.14 (no dependency
dead-ends needed a workaround), all 15 unit tests pass, and the GUI, the
Settings dialog, and a real `open notepad` command were smoke-tested and
confirmed working.

**Not automatically tested (needs you, live):** the microphone button and
the virtual mouse's cursor takeover - both were left for you to try
in-person rather than have an agent hijack your real mouse/mic. Click
**Start Virtual Mouse** in the sidebar, show your webcam your hand, move
your index finger to move the cursor, and pinch your thumb+index finger
together to click. Click **Mic** and speak to try voice input.

## 4. Configuration

Everything is editable from the **Settings** button in the sidebar, backed
by `%APPDATA%\NovaAssistant\config.json`:

| Setting | Purpose |
|---|---|
| Assistant name | Shown in the title bar and replies (default "Nova") |
| Music folder | Where `play <song>` looks for audio files |
| Default weather city | Used when you just say "weather" with no city |
| Theme | dark / light / system |
| Speak replies aloud | Toggle TTS |
| Enable microphone input | Toggle STT |
| Confirm before shutdown/restart/delete | Safety gate on destructive commands |
| Custom apps | `name=path` registry used by `open <name>` |
| Allowed shell commands | Allowlist for `run command ...` |

## 5. Full command list

Say/type **help** in the app at any time to see this live from the router.
As of this build:

- **Time/date/system:** time, date, system specs, storage
- **Power/window control:** shutdown pc, restart pc, cancel shutdown, lock
  pc, minimize windows, restore windows, screenshot, open settings, open
  search, start menu
- **Chrome:** chrome new tab / close tab / new window / incognito /
  history / downloads / bookmark / switch tab to N, open website `<name>`
- **YouTube:** youtube play/pause/fullscreen/theater/forward/rewind/mute/
  next/previous/volume up/volume down
- **Apps:** open `<name>` (notepad, chrome, calculator, explorer, word,
  excel, paint, task manager, control panel, vs code out of the box),
  register app `<name>` at `<path>`
- **Files & folders:** list folder `<path>`, create folder/file `<path>`,
  open folder/path `<path>`, rename `<path>` to `<name>`, move `<src>` to
  `<dst>`, delete `<path>` (asks to confirm), find files `<pattern>` in `<path>`
- **Shell:** run command `<cmd>` (allowlisted), force run `<cmd>` (bypasses it)
- **Web/info:** search google for `<q>`, search youtube for `<q>`, wikipedia
  `<q>`, weather in `<city>`, my ip, my location, tell me a joke /
  programming joke / chuck joke
- **Music:** play `<song>`
- **Virtual mouse:** start virtual mouse, stop virtual mouse

## 6. Known limitations (be aware of these)

- **Command matching is keyword/regex, not real NLU.** It's the same
  substring-matching approach the original had, just centralized and
  disambiguated by phrase length. "open" as a generic app-launch fallback
  will technically match inside words like "reopen." Good enough for a
  personal tool where you know the phrasing; a real intent classifier
  would be the next step if this becomes annoying (see suggestions below).
- **Voice input needs internet** (SpeechRecognition's default backend is
  Google's free Web Speech API) and a working microphone. Typed commands
  always work regardless.
- **`run command`/`force run`** executes via `subprocess.run(..., shell=True)`
  under the account running the app. There's an allowlist for the
  unprefixed form, but this is a single-user desktop tool, not a sandboxed
  service - don't expose it to anyone else or wire it to untrusted input.
- **The virtual mouse takes over your real cursor.** There's no
  "safe zone" or pause gesture yet beyond the Stop button/voice command -
  if you want to use your physical mouse while it's on, stop it first.
- **Chrome/YouTube controls are OS-level hotkeys**, so they act on
  whatever window currently has focus - they don't verify Chrome is the
  focused app.
- Weather/IP/Wikipedia calls depend on free third-party endpoints
  (wttr.in, ipapi.co, ipify.org) with no API key and no SLA.

## 7. How to extend it

Add a new command in `assistant/core/commands.py`:

```python
@router.register(
    "my new command", keywords=("do the thing",),
    help="Describe what it does.",
)
def cmd_my_new_command(text, ctx):
    return "Done!"
```

If it needs a new subsystem (another API, another local integration),
add a small class/module under `automation/` or `integrations/`, then wire
an instance of it into `assistant/core/context.py`'s `Context` dataclass
and `Context.build()`.

## 8. Suggestions to get the most out of this (prioritized)

**High value, low effort:**
1. **Global hotkey to summon the window** (e.g. `Ctrl+Alt+Space`) using the
   already-installed `keyboard` library, so you never have to alt-tab to
   find it - register a hotkey in `main.py` that calls `app.deiconify()`/`lift()`.
2. **System tray icon** (via `pystray`, one more small dependency) so
   closing the window minimizes to tray instead of fully quitting - keeps
   the assistant "always on" without a taskbar window in the way.
3. **Startup shortcut**: drop a `.lnk` to `python -m assistant.main` (via
   the venv's `pythonw.exe` to avoid a console window) into
   `shell:startup` so it's running whenever you log in.

**Medium effort, real productivity gains:**
4. **A small set of custom "macros"**: one phrase that runs several
   commands in sequence (e.g. "start my day" -> open Chrome, open VS Code,
   show weather, show system specs). Straightforward to add as one more
   router command that just calls `router.dispatch(...)` several times.
5. **Clipboard-aware commands**: "summarize clipboard" / "search clipboard
   on google" using `pyperclip` (already installed transitively via
   pyautogui) - handy for a fast triage workflow.
6. **Richer virtual-mouse gestures**: right-click via a second gesture
   (e.g. middle-finger pinch), scroll via a two-finger swipe, drag via
   holding the pinch. The `_run` loop in `vision/virtual_mouse.py` already
   has all the landmarks available; it's mostly adding more distance
   checks alongside the existing pinch check.

**Bigger, optional:**
7. **Pluggable LLM backend** - you explicitly opted out of this for now,
   but the `Context`/router design would make it a clean add later: a new
   `integrations/llm.py` with a `chat(prompt) -> str` function, one new
   router command ("ask ... "), and an API key read from an environment
   variable. No changes needed anywhere else.
8. **Offline wake-word** (e.g. `openWakeWord` or `Porcupine`) so you can
   say "Hey Nova" instead of clicking the mic button - turns this from a
   "click to talk" tool into a true ambient assistant.
9. **Face-based unlock/greeting**, done properly this time (a dedicated
   enrollment flow, confidence thresholds, opt-in) - you dropped the old
   half-finished version, but the `images/` folder of labeled photos is
   still sitting there if you want to revisit this later.

**Housekeeping:**
- `cv-corpus-13.0-delta-2023-03-09/` (a Common Voice Urdu dataset download,
  a few hundred MB) doesn't appear to be referenced by any code, old or
  new - worth deleting manually if you don't need it, to shrink the repo.
- `legacy_old/` is safe to delete once you've confirmed you don't need
  anything from it for reference; it's not imported by the new app.
