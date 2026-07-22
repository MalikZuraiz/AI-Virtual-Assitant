# Nova - Personal Windows Automation Assistant

A from-scratch, working rebuild of this repo's old prototype. Everything
below reflects what's actually implemented and tested, not aspirational
features - see "Known limitations" for the honest gaps. This doc covers
both the original rebuild and the follow-up pass that substantially
deepened automation coverage (Chrome/Windows/apps/files/system) and
reworked the UI to be more visual.

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

## 2. Bug-fix pass (this round)

You reported three real problems from an actual run's log/terminal output.
All three were genuine bugs, not misconfiguration, and all three are fixed
and verified (not just "should work now" - each was measured/reproduced
before the fix and re-measured after):

1. **"All command responses should be spoken" - voice was silently
   unreliable, not just "off."** Root-caused by *timing* actual playback
   (not just checking for exceptions): `pyttsx3.init()` caches engines in
   a module-level dict keyed by driver name and returns the **same
   instance** on every call, so code that looked like it created a fresh
   engine per reply was silently reusing the first one - measured
   directly, a reused engine's 2nd/3rd utterances returned in ~0.2s with
   no audio, while a genuinely fresh `pyttsx3.engine.Engine` instance
   played every time at the correct duration for the text length (~6.3s
   for a ~19-word sentence, every single time across repeated test runs).
   `assistant/core/speech.py`'s `TextToSpeech` now constructs `Engine`
   directly per utterance, bypassing that cache entirely. Separately, the
   startup greeting, the "settings saved" message, and virtual-mouse
   start/stop confirmations were only ever logged, never spoken - they
   now go through the same `_say`/`_emit_response` path as every other
   command reply, so voice output is consistent everywhere instead of
   command-dispatch-only.
2. **Virtual mouse crashed with `AttributeError: module 'mediapipe' has no
   attribute 'solutions'`.** mediapipe 0.10.x **removed** the legacy
   `mediapipe.solutions` API entirely (not deprecated - the submodule
   doesn't exist in the installed package anymore). `vision/virtual_mouse.py`
   is rewritten against the replacement Tasks API
   (`mediapipe.tasks.python.vision.HandLandmarker`), which needs a small
   model file (`hand_landmarker.task`, ~8MB) that isn't bundled with the
   package anymore either - it's downloaded once from Google's official
   MediaPipe model storage on first use and cached at
   `%APPDATA%\NovaAssistant\models\hand_landmarker.task` (subsequent starts
   skip the download and start in under a second). Verified end-to-end:
   model downloads and caches correctly, camera opens, hand detection runs,
   start/stop both report cleanly with no errors.
3. **`open youtube` / `open pictures` claimed success but actually failed**
   (visible in the console as `'youtube' is not recognized as an internal
   or external command...`). `AppLauncher.open()` used to fall back to
   `subprocess.Popen(name, shell=True)` for any unrecognized name - that
   call "succeeds" from Python's point of view (cmd.exe itself starts
   fine) even when the name means nothing, so the method kept reporting
   "Opening youtube." while cmd printed an error and exited. It now
   resolves unrecognized names properly via `shutil.which()` and raises a
   clear `AppNotFoundError` when nothing matches - and since "open `<word>`"
   very often means a *website* rather than a local app, the `open app`
   command now falls back to opening it as a site (`open youtube` opens
   youtube.com) instead of failing. Bare folder names (`open pictures`,
   `open desktop`, `open documents`, `open videos`, `open music`) were
   also added as aliases alongside the existing `open <x> folder` phrasing,
   since that's the more natural way people actually type it.

## 3. Virtual mouse overhaul

You reported the virtual mouse was slow, couldn't reach the bottom of the
screen, wasn't smooth, and that pinch-to-click wasn't reliable - all four
were real, and all four are fixed in `assistant/vision/virtual_mouse.py`:

- **"Can't reach the bottom" / limited range.** The whole camera frame used
  to map 1:1 to the whole screen, but your hand realistically never
  reaches the frame's true edges (especially the bottom) without an
  awkward stretch. Movement now maps from a smaller, **configurable
  "active region"** of the frame to the full screen - a
  `virtual_mouse_sensitivity` slider now in **Settings** (default 1.7x)
  controls how much smaller; higher means less hand movement covers the
  whole screen. There's also a vertical center bias (`virtual_mouse_center_y`,
  default 0.42, config-file only) because a hand held up to a laptop webcam
  naturally sits a bit low in the frame, not dead center - this alone
  makes the bottom edge meaningfully easier to reach.
- **Not smooth / laggy.** The old code used a single fixed-factor
  exponential smoothing formula, which is either laggy or jittery at any
  one setting. Replaced with a **One Euro Filter** (a well-established
  adaptive filter designed exactly for this: hand/pointer tracking) - it
  smooths out jitter when your hand is nearly still and automatically
  cuts lag once it starts moving quickly, so motion feels both cleaner
  and more responsive at the same time, not a tradeoff between the two.
- **Pinch-click "not that good."** Rebuilt around **hysteresis** instead
  of a single distance threshold: a pinch has to clearly close past one
  threshold to register as a press, and clearly re-open past a *looser*
  second threshold to register as a release, so it can't flicker
  press/release right at one boundary the way a single threshold does.
  Real press/release mouse events replace the old single "synthetic
  click," which is also what makes drag possible now.
- **Genuinely slow frame rate on modest hardware**, measured directly on
  this machine: mediapipe's hand-tracking inference alone costs
  ~40-47ms/frame in the old `IMAGE` mode (~21-25 fps ceiling before any of
  our own overhead), meaning the 30fps cap was never actually the limit -
  inference was. Switched to `VIDEO` running mode (~17% faster, since it
  can reuse tracking between frames instead of fully re-detecting every
  time) and reduced the capture resolution to 640x480 (less pixel-pushing
  overhead in the color-conversion/drawing steps around the model). This
  narrows the gap but doesn't erase it - on a low-power CPU, lower
  `virtual_mouse_fps_limit` in Settings if it still feels behind, since
  a lower target frame rate gives the One Euro Filter more consistent
  timing to work with than fighting an inconsistent one.

**New gestures**, so the module does more than just point-and-click - all
built from the same 21 hand landmarks, checked in this priority order each
frame:

| Gesture | Action |
|---|---|
| Open palm (all 4 fingers extended) | **Pause** - freezes the cursor and releases any held button, so you can reposition your hand without dragging the cursor (like lifting a real mouse) |
| Index + middle fingers up, held together | **Scroll** - move them up/down to scroll the wheel |
| Pinch thumb + index | **Left click** - a quick pinch is a click; holding it while moving is a **drag** |
| Pinch thumb + middle | **Right click** (and right-drag, the same way) |
| Point with index finger | **Move** the cursor |

If your hand leaves the frame entirely while a button is held, it's
force-released defensively (same on Stop) so a lost hand can never leave
the mouse stuck down. The live preview panel now also overlays the
detected mode (`move`/`scroll`/`paused (open palm)`/`left-drag`/etc.) so
it's easy to see which gesture is being recognized while you're getting
used to it.

## 4. Architecture

```
assistant/
  config.py            AppConfig - all user settings, persisted to
                        %APPDATA%\NovaAssistant\config.json
  logging_setup.py       Console + rotating file log
                        (%APPDATA%\NovaAssistant\assistant.log)
  core/
    router.py            CommandRouter - keyword/regex -> handler registry
    commands.py            All 111 built-in commands, registered here
    context.py              Bundles every subsystem (Context) passed to handlers
    speech.py                TextToSpeech (pyttsx3) + SpeechListener (SpeechRecognition)
  automation/
    windows_ctl.py      Window/desktop management, power, volume (pycaw),
                        theme toggle, settings pages, screenshots, Recycle Bin
    chrome_ctl.py         Chrome tab/page/window hotkeys + YouTubeController
    apps.py                 Launch apps by name + process list/is-running/close
    files.py                 Directory/file ops incl. copy/zip/unzip/info
    shell.py                  Allowlisted cmd/PowerShell execution
    system_info.py       CPU/RAM/disk/battery/uptime/network/GPU via psutil
    clipboard.py            Clipboard read/write/clear (pyperclip)
  vision/
    virtual_mouse.py    Hand-tracking cursor control (mediapipe)
  integrations/
    web.py                    Wikipedia, Google/YouTube search, weather, IP/geo, jokes
    music.py                Local audio playback
  ui/
    app.py                    CustomTkinter main window (banner, avatar, chat log)
    gif_player.py            AnimatedGifLabel - plays a GIF's frames in a CTkLabel
    settings_dialog.py Settings editor
  main.py                       Entry point
tests/                              19 unit tests (router, files, shell logic)
requirements.txt / requirements-dev.txt
legacy_old/                       The original prototype, kept for reference only
```

**Why a router instead of another if/elif chain:** every command is a
small function registered with `@router.register(name, keywords=..., help=...)`
in `assistant/core/commands.py`. Longer/more specific keyword phrases
automatically win over shorter generic ones (e.g. "open website" beats
"open"), and anything needing structured arguments (`rename X to Y`,
`chrome switch tab to 3`, `open display settings`) uses a regex `pattern=`
instead. Adding a new command means writing one function - nothing else in
the app changes. At 111 commands, this still holds up because the registry
does the disambiguation work instead of a human maintaining `if/elif` order.

**Threading model (why the UI won't freeze or lag):** Tkinter/CustomTkinter
is not thread-safe, so background work (command dispatch, mic capture, the
webcam loop) never touches a widget directly. Background threads push
events onto a `queue.Queue`; a single `after()`-scheduled poller on the
main thread drains it and is the only code that updates the window. The
virtual mouse's camera loop runs on its own thread, throttled to a target
FPS via delta timing (default 30 FPS, configurable), independent of the UI
thread and independent of how fast the webcam/CPU could otherwise run it.
The GIF avatar animations use the same principle in miniature: every frame
is decoded to a `CTkImage` once at load time, so playback is just cheap
image-swapping on a timer, not per-frame decoding.

## 5. What the UI looks like now

The window now has:

- **A banner** across the top using `GUI/bg.jpg` (darkened and cropped to
  fit) behind the assistant's name.
- **A living avatar** in the sidebar - `GUI/sophie body.gif` loops
  continuously as the assistant's idle state. When you click **Mic** and
  it's listening, the avatar swaps to `GUI/voice.gif` and swaps back
  automatically once your phrase is transcribed.
- **A startup splash** using `GUI/yy3.gif`, shown immediately (before the
  slower TTS/mic/router initialization) so you see something moving right
  away instead of a frozen window for a few seconds.
- The rest is unchanged in spirit: a scrolling chat/log panel, a text entry
  with Send, a live webcam preview for the virtual mouse, and a status bar
  with clock/CPU/RAM/battery.

An earlier version of the splash logic had a real bug: it hid the main
window (`withdraw()`) at startup and only un-hid it (`deiconify()`) *after*
destroying the splash - for a brief moment zero top-level windows were
mapped, and on this Tk/Windows combination that's enough for Tk to decide
the whole application is done and tear the GUI down (the process itself
kept running in the background with no window, which is why it looked
like the app "closed on its own" right after appearing). Confirmed via a
live window-handle check that the old code's window vanished within
seconds while the process stayed alive, and that the fix - keeping the
root window mapped continuously and layering the splash on top of it
instead of hiding it underneath - keeps the window open indefinitely.

These all come from `assistant/ui/gif_player.py`'s `AnimatedGifLabel`,
which decodes every frame of a GIF up front (via Pillow) into pre-built
`CTkImage`s and then just cycles through them on a timer using each
frame's own duration - so it holds a steady rate without re-decoding
anything during playback. If `GUI/` or a specific file is missing, the
affected widget just falls back to plain text instead of erroring.

The remaining files in `GUI/` (`Loader.gif`, `location.gif`, `yy3.gif`,
`WMDx.gif`, `XDZT.gif`, `Nt6v.gif`, `gif1.gif`, `1.jpg`/`2.jpg`/`3.jpg`)
aren't wired up yet - see the suggestions section for ideas (e.g. a
location-lookup animation, a "thinking" state between command and reply).

## 6. Running it

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

All dependencies (including the newly added `pycaw`, for volume control)
resolve and install cleanly for this machine's Python 3.13.14 - no
dependency dead-ends needed a workaround. All 19 unit tests pass. The GUI
(banner/avatar/splash), the Settings dialog, and a wide sample of commands
across every category (`open notepad`, `gpu info`, `uptime`, `network
info`, `current volume`/`set volume to N`, `is <app> running`, `list
running apps`, `copy to clipboard` / `read clipboard`, `file info`, `open
display settings`) were smoke-tested end to end against this real machine
and returned real data (actual GPU, actual uptime, actual Wi-Fi IP, actual
volume level, etc.) - not mocked output.

**Not automatically tested (needs you, live):** the microphone button, the
virtual mouse's cursor takeover, and `toggle theme`/`empty recycle bin` -
these either hijack real input devices or change visible system state, so
rather than have an agent do that on your machine, they were verified by
code review plus testing their underlying read half (e.g. reading the
current theme registry value, reading the current volume before/after a
round-trip set) instead of actually flipping them. Try them yourself:

- **Mic**: click **Mic**, speak, watch the avatar switch to the
  listening animation and back.
- **Virtual mouse**: click **Start Virtual Mouse**, show the webcam your
  hand, move your index finger to move the cursor, pinch thumb+index to
  click.
- **Theme toggle**: say `toggle theme` - flips Windows light/dark mode.

## 7. Configuration

Everything is editable from the **Settings** button in the sidebar, backed
by `%APPDATA%\NovaAssistant\config.json`:

| Setting | Purpose |
|---|---|
| Assistant name | Shown in the title bar/banner and replies (default "Nova") |
| Music folder | Where `play <song>` looks for audio files |
| Default weather city | Used when you just say "weather" with no city |
| Theme | dark / light / system |
| Speak replies aloud | Toggle TTS |
| Enable microphone input | Toggle STT |
| Confirm before shutdown/restart/delete | Safety gate on destructive commands |
| Virtual mouse sensitivity | How much a hand movement is amplified to cover the screen (default 1.7x) |
| Custom apps | `name=path` registry used by `open <name>` (26 apps registered by default) |
| Allowed shell commands | Allowlist for `run command ...` |

A few more virtual-mouse knobs are config-file-only (not in the Settings
UI, to keep it from getting cluttered) - edit `config.json` directly:
`virtual_mouse_center_x`/`_center_y` (where the "active region" is
centered in the frame), `virtual_mouse_min_cutoff`/`_beta` (One Euro
Filter smoothing - lower `min_cutoff` for less jitter at rest, higher
`beta` for less lag when moving fast).

Configs saved by an older version of the assistant automatically pick up
newly-added default apps/allowlist entries on load (merged in, without
discarding anything you customized yourself).

## 8. Full command list (111 commands)

Say/type **help** in the app at any time to see this live from the router
- it's generated from the same registry, so it can never drift out of
sync with what's actually implemented.

- **Time/date/system:** time, date, system specs, storage, uptime, network
  info, top processes, gpu info
- **Power/window/desktop control:** shutdown pc, restart pc, cancel
  shutdown, lock pc, minimize windows, minimize active window, maximize
  window, restore windows, snap left/right, switch window, task view, new/
  close/next/previous virtual desktop, open run dialog, open action
  center, open clipboard history, open emoji panel, screenshot (snipping
  tool), capture screenshot (saved to disk), empty recycle bin, toggle
  theme, active window, close active window
- **Volume:** volume up/down, mute volume, current volume, set volume to
  `<0-100>`
- **Settings pages:** open `<display|sound|wifi|network|airplane mode|
  bluetooth|night light|power|battery|apps|personalization|update|
  storage|privacy|accounts|notifications>` settings
- **Chrome (23 commands):** new/close/reopen/next/previous tab, new/close
  window, incognito, switch tab to N, reload/hard reload, zoom in/out/
  reset, print, find, address bar, view source, dev tools, full screen,
  history, downloads, bookmark, bookmarks manager, clear browsing data,
  browser task manager, open website `<name>` (25+ built-in site
  shortcuts: youtube, github, gmail, netflix, spotify, reddit, drive,
  docs, maps, discord, notion, amazon, chatgpt, claude, ...)
- **YouTube:** play/pause/resume, fullscreen, theater, forward/rewind,
  mute/unmute, next/previous, volume up/down, captions, speed up/down,
  miniplayer, restart
- **Apps (26 default registrations):** open `<name>` - chrome, edge,
  firefox, notepad, wordpad, calculator, explorer, word, excel,
  powerpoint, outlook, paint, snipping tool, task manager, control panel,
  vs code, terminal, powershell, cmd, camera, photos, magnifier,
  on-screen keyboard, character map, remote desktop, media player, device
  manager, services, event viewer, registry editor, disk management, task
  scheduler; register app `<name>` at `<path>`; list running apps; is
  `<name>` running; close app `<name>`
- **Files & folders:** list folder, create folder/file, open folder/path,
  rename/move/copy `<path>` to `<path>`, delete (confirmed), find files
  `<pattern>` in `<path>`, compress/zip, extract/unzip, file info, open
  downloads/desktop/documents/pictures/videos/music
- **Shell:** run command `<cmd>` (allowlisted: dir, cd, echo, ipconfig,
  ping, tasklist, systeminfo, tree, git, python, pip, node, npm, code,
  powershell, netstat, nslookup, hostname, tracert, type, findstr, ...),
  force run `<cmd>` (bypasses it)
- **Clipboard:** read clipboard, copy to clipboard `<text>`, clear clipboard
- **Web/info:** search google for `<q>`, search youtube for `<q>`,
  wikipedia `<q>`, weather in `<city>`, my ip, my location, tell me a joke
  / programming joke / chuck joke
- **Music:** play `<song>`
- **Virtual mouse:** start virtual mouse, stop virtual mouse

## 9. Known limitations (be aware of these)

- **Command matching is keyword/regex, not real NLU.** Longer/more
  specific phrases win over shorter ones, and structured commands use
  regex patterns, but this is still substring matching, not language
  understanding. At 111 commands the collision surface is bigger, so
  phrasing matters more than it would with a real intent classifier (see
  suggestions below) - e.g. `open <page> settings` requires literally
  three words in that shape, or it won't route there.
- **Voice input needs internet** (SpeechRecognition's default backend is
  Google's free Web Speech API) and a working microphone. Typed commands
  always work regardless.
- **`run command`/`force run`** executes via `subprocess.run(..., shell=True)`
  under the account running the app. There's an allowlist for the
  unprefixed form, but this is a single-user desktop tool, not a sandboxed
  service - don't expose it to anyone else or wire it to untrusted input.
- **The virtual mouse takes over your real cursor.** Open-palm now pauses
  it without stopping the module (see section 3), but if you want your
  physical mouse back entirely, use the Stop button/voice command.
- **Achievable frame rate is inference-bound on modest CPUs.** On this
  machine's 2-core CPU, hand-tracking inference alone costs ~40ms/frame -
  a hard floor no amount of app-level optimization removes. If it still
  feels behind after the VIDEO-mode/resolution changes, lower
  `virtual_mouse_fps_limit` in Settings.
- **First-ever "start virtual mouse" needs internet**, to download the
  ~8MB hand-tracking model (one time only - it's cached afterward at
  `%APPDATA%\NovaAssistant\models\hand_landmarker.task` and every
  subsequent start is instant and fully offline).
- **Chrome/YouTube controls are OS-level hotkeys**, so they act on
  whatever window currently has focus - they don't verify Chrome is the
  focused app.
- **Volume control depends on pycaw**, which wraps a Windows COM API that
  has changed shape across versions before (this rebuild already hit and
  fixed one such break - `AudioUtilities.GetSpeakers()` returning a
  wrapper object instead of a raw COM device). If a future pycaw release
  changes its API again, `volume up`/`volume down` (plain media-key
  presses) will keep working even if `set volume to N`/`current volume`
  (pycaw-based) don't.
- **`toggle theme`** writes `HKCU\...\Themes\Personalize` directly; some
  running apps only pick up the new theme after restarting.
- Weather/IP/Wikipedia/GPU-via-PowerShell calls depend on free
  third-party endpoints or OS tooling with no guaranteed SLA.

## 10. How to extend it

Add a new command in `assistant/core/commands.py`:

```python
@router.register(
    "my new command", keywords=("do the thing",),
    help="Describe what it does.",
)
def cmd_my_new_command(text, ctx):
    return "Done!"
```

If it needs a new subsystem (another API, another local integration), add
a small class/module under `automation/` or `integrations/` - simple
stateless helpers (like `clipboard.py`) can just be plain functions
imported directly into `commands.py`; anything that needs shared state
(like `WindowsController` or `AppLauncher`) goes on `Context` in
`assistant/core/context.py`.

## 11. Suggestions to get the most out of this (prioritized)

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
4. **Put the remaining `GUI/` GIFs to work**: `location.gif` while running
   `my location`/`weather`, a short "thinking" loop (`gif1.gif` or one of
   the small icon GIFs) between sending a command and getting a reply,
   `Loader.gif` as an alternate splash. The `AnimatedGifLabel` class
   already supports swapping any GIF in; it's the same pattern used for
   the mic/idle swap.

**Medium effort, real productivity gains:**
5. **A small set of custom "macros"**: one phrase that runs several
   commands in sequence (e.g. "start my day" -> open Chrome, open VS Code,
   show weather, show system specs). Straightforward to add as one more
   router command that just calls `router.dispatch(...)` several times.
6. **Even more virtual-mouse gestures** on top of move/click-drag/
   right-click/scroll/pause (section 3): a pinky-pinch for
   forward/backward browser navigation, a fist for "grab and pan," or a
   per-hand calibration step (hold still for 2s to set the active
   region's center to wherever your hand actually is, instead of a fixed
   default) - `_run` in `vision/virtual_mouse.py` already computes every
   finger's extended/curled state and all the pinch ratios each frame, so
   most of the raw material for new gestures is already there.
7. **A proper "is Chrome focused?" check** before Chrome/YouTube hotkey
   commands, using `win32gui`/`active_window()` (already implemented) to
   warn instead of silently sending Ctrl+T to the wrong app.

**Bigger, optional:**
8. **Pluggable LLM backend** - you explicitly opted out of this for now,
   but the `Context`/router design would make it a clean add later: a new
   `integrations/llm.py` with a `chat(prompt) -> str` function, one new
   router command ("ask ... "), and an API key read from an environment
   variable. No changes needed anywhere else.
9. **Offline wake-word** (e.g. `openWakeWord` or `Porcupine`) so you can
   say "Hey Nova" instead of clicking the mic button - turns this from a
   "click to talk" tool into a true ambient assistant.
10. **Face-based unlock/greeting**, done properly this time (a dedicated
    enrollment flow, confidence thresholds, opt-in) - you dropped the old
    half-finished version, but the `images/` folder of labeled photos is
    still sitting there if you want to revisit this later.
11. **A simple intent classifier** (even a small local embedding-similarity
    matcher over the 111 commands' help text) instead of keyword/regex
    matching, if the substring-matching heuristics ever start feeling
    limiting in daily use.

**Housekeeping:**
- `cv-corpus-13.0-delta-2023-03-09/` (a Common Voice Urdu dataset download,
  a few hundred MB) doesn't appear to be referenced by any code, old or
  new - worth deleting manually if you don't need it, to shrink the repo.
- `legacy_old/` is safe to delete once you've confirmed you don't need
  anything from it for reference; it's not imported by the new app.
