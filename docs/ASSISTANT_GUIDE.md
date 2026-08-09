# Nova — architecture and design notes

How it's built and why. For **what to type**, see
**[COMMANDS.md](COMMANDS.md)** — every command, hotkeys and gestures.
Original spec: [assistant-project-brief.md](assistant-project-brief.md).

---

## 1. The idea in one paragraph

Most of what this assistant does is **not written in Python** — it is written
in `config/*.json`. Report generators, websites, projects, media libraries,
reminders, gesture bindings, chat personas and your own custom commands are
all config entries, and each becomes a live command the moment the file is
saved and `refresh` runs. The Python side is a router, a worker pool and a set
of *packs* that turn each kind of config entry into an action. Adding a
website is a one-line JSON edit; adding a whole new report generator is a
folder on disk plus one `scan for reports`.

---

## 2. Architecture

```
assistant/
  store/                 config as data
    paths.py               where things live (config/ vs %APPDATA%)
    defaults.py            portable seed content for each file
    store.py               ConfigStore: load, refresh, atomic write
    bootstrap.py           one-time discovery of THIS machine's folders
    personas.py            chat persona seeds
  workspace/             folders, and what gets filed into them
    daybook.py             "today's folder" naming and creation
    scaffold.py            creates the Entertainment/Office hubs
    reports.py             run a generator, file its output
    inputs.py              find/stage the raw data file a generator needs
  core/
    assistant.py           Assistant.handle() — the single entry point
    router.py              regex > exact > substring > fuzzy matching
    jobs.py                worker pool (no Qt import anywhere)
    selection.py           numbered lists — "say 3"
    conversation.py        the wizard state machine
    pathfinder.py          find a folder/file by name, not by path
    runner.py              subprocess execution for config-described things
    reminders.py           APScheduler + SQLite
    voice.py               edge-tts, pyttsx3 fallback
    listening.py           faster-whisper: tap, hold, or live
    context.py             everything a handler needs
    commands.py            the PC-automation pack
  commands/              one module per config-driven domain
    meta  links  reportpack  scriptpack  projects  media
    reminders  excel  tools  chat  vision
  automation/            browser, autostart, spreadsheets + Windows control
  integrations/          llm (Ollama), netspeed, web, music
  vision/                handtracking, gestures, actions
  ui/qt/                 app, hud, tray, bridge, theme
  ui/app.py              fallback CustomTkinter window (--legacy-ui)
config/                  the JSON files you edit
tests/                   296 tests
```

### Threading — why the GUI cannot freeze

```
GUI thread                      worker pool                  GUI thread
──────────                      ───────────                  ──────────
Assistant.handle(text)
  ├ wizard turn? answer inline (pure memory, instant)
  ├ a numbered pick? → queue it (priority 2) + "Working on that..."
  ├ instant command? run inline (clock, help, config reads)
  └ everything else:
      emit "Running X…"  ──▶  router.run(cmd, text, ctx)
                              ctx.progress("…")  ──▶ status bar
                              returns a Reply     ──▶ chat + TTS
```

`core/jobs.py` imports no Qt at all — plain threads, a `PriorityQueue` and a
callback, so the backbone is unit-testable without an application.
`ui/qt/bridge.py` is the *only* place the two worlds meet: workers call
`publish()`, which emits a Qt signal that Qt queues onto the GUI thread. No
worker ever touches a widget.

Priority matters: a four-minute report is submitted at priority 8 while a
lookup runs at 1, so "what's the time" is never stuck behind it.

### Command matching

Precedence is regex → exact phrase → longest substring → fuzzy, so specific
always beats generic. Fuzzy (rapidfuzz, threshold in `core.json`) only runs
when the first three miss — that is what catches voice transcripts and typos.
On a real miss you get "did you mean…" suggestions.

Config-driven commands come from **providers** the router re-asks on every
`refresh` — that is the mechanism behind "edit the JSON and it just works".

---

## 3. This round: what changed

### Merging is 30× faster and outputs CSV

The first version wrote `.xlsx` through openpyxl, which builds every cell as a
Python object. Merging two real exports (539,604 + 279,637 rows) took minutes.

Two changes: **output is CSV**, and when every input is a CSV with the same
columns, the files are **concatenated as raw bytes without being parsed**.
Measured on the actual exports:

| Path | Time |
|---|---|
| Old — parse + write .xlsx | minutes |
| Column-aligning path (parsed) | 16.2 s |
| Streaming path | **0.5 s** |

The streaming path is chosen automatically. It compares *parsed* column names
rather than raw header bytes, because two exports of the same dashboard often
quote differently (`"Penalty ID",District` vs `Penalty ID,District`) while
being structurally identical — a byte comparison rejected the fast path for no
reason. Quoted fields containing commas or embedded newlines survive
untouched, and a file whose last line lacks a newline no longer glues itself
onto the next file's first row.

Anything else — Excel inputs, genuinely different columns, de-duplication,
a source column — falls back to the pandas path, which aligns rows by column
*name* so nothing lands in the wrong column.

### The virtual mouse is gone

Removed entirely: `vision/virtual_mouse.py`, its commands, its config knobs,
its Settings slider, its webcam preview in the legacy UI, and its tests.

It was the wrong shape for a webcam. Cursor position lagged the hand, clicks
landed where your hand had been rather than where you aimed, and it fought the
physical mouse. Hand tracking now does only what it is good at: **classifying
discrete poses**. See the gesture section in [COMMANDS.md](COMMANDS.md).

### Ollama is usable now

The model was reloaded from disk on every single question (3–6 s of
`load_duration` before a single token), replies were capped at 500 tokens, and
nothing appeared until the whole reply was finished. On a CPU that generates
~6.5 tokens/second that reads as "not responding".

- `keep_alive: 30m` — the model stays resident between questions.
- **Streaming into the chat bubble** — the answer is rewritten in place as it
  generates, a few times a second, so you read along instead of waiting.
- Per-persona token caps (80 for meme, 260 for portfolio) plus a system-prompt
  brevity rule. Typical answers went from 150+ words to ~50, 15–20 s.
- `num_ctx: 2048` — plenty for chat, much faster to process on CPU.
- Warm-up on `enable chat`, off the GUI thread.

**Persona quality was a prompt-layering bug.** The system prompt stacked
persona + full biography + style note. A 1.5B model handed a biography recites
it back — which is exactly why ragebait mode started interviewing you about
your degree instead of arguing. Personas now declare `use_profile`, and the
playful modes (fun, meme, ragebait, personality, podcast, motivation) get the
persona *only*. Two modes added: **motivation** and **portfolio**.

Mode names are fuzzy-matched, so `memo mode` reaches `meme mode`.

### `open folder <name>` works

`open folder office` used to route to the legacy literal-path command and
answer `'office' does not exist`. Two fixes: a pattern that catches the
folder-word-first phrasing, and the legacy command now falls back to the
pathfinder when its argument isn't a real path. Searching D: takes 20–60 ms.

### `run <n>` runs things

On a project listing, `run 3` used to open a terminal in that folder. It now
*runs* it: the full report pipeline if it's a registered generator, otherwise
its newest `dist/*.exe`, otherwise its main script under the project's own
venv. `terminal 3` is the new verb for the old behaviour.

### exe over .py

Frozen builds are preferred wherever they exist — they carry their own
dependencies, so a report behaves the same whether or not its venv is intact.
Projects with no `dist/` (Fleet Analysis, Ranking Report) run as `.py` through
their own venv, and switch automatically once built. Python Bot (PED) is
pinned to `.py` by name.

### Speech input was captured at the wrong sample rate

Recording was opened at 16 kHz because that is what Whisper consumes. The
Realtek mic runs at 44.1 kHz and, asked for 16 kHz, returned clipped garbage:

| Requested rate | Ambient RMS | Peak |
|---|---|---|
| 16000 | 0.01725 | **0.96** (clipping) |
| 44100 | 0.00481 | 0.271 |
| 48000 | 0.00443 | 0.256 |

Ambient noise above the 0.012 silence threshold meant every moment looked
like speech, so recording ran to its 15-second limit and Whisper only ever
saw noise. Audio is now captured at the device's own rate and resampled with
PyAV's libswresample (already present as a faster-whisper dependency), and
the silence threshold is measured from the room at the start of each
recording rather than being a constant.

### Gestures: three separate bugs

Documented in [COMMANDS.md](COMMANDS.md). In short: an open palm was
classified as "four fingers" (bound to minimise-everything) because the
distinguishing signal was `thumb_out`, which is unreliable; static poses
accumulated their hold time *during* a swipe; and thumbs up/down were
measured against the wrist so they only worked with the hand held high.
"Four" no longer exists as a pose, movement resets the hold streak, and the
thumb is compared to the other fingertips.

### Replies are spoken in full

`TextToSpeech` used to speak the first line and append "plus N more lines in
the chat", so most answers were never actually heard. It now reads the whole
reply, converting bullets to pauses, with a 1200-character safety valve that
cuts at a sentence boundary. `help` is no longer silent.

### Scripts run in a visible terminal

Captured runs pass `stdin=DEVNULL`, which turns any `input()` call into an
instant `EOFError` — reproduced in a test, and the reason the PED bot failed
here while working by hand. `Runnable.console` runs under
`CREATE_NEW_CONSOLE` instead: a real terminal with real stdin, still waited
on so the output diff still works. On by default via
`core.behaviour.show_terminals`.

### A pick now acknowledges itself

Choosing from a numbered list could kick off minutes of work with no feedback
at all — the chat just sat there. Picks now emit "Working on that…" the same
way commands do.

---

## 3a. Next round: `start gestures` crash + speech recognition root cause

The finger-count gesture rewrite (§3, "Gestures: three separate bugs") landed
its logic correctly but shipped two integration bugs, and the STT sample-rate
fix from the same round turned out not to be the whole story.

**`start gestures` raised `TypeError` immediately.** `assistant/commands/
vision.py` still called `GestureController(min_travel=...)` — a constructor
argument that belonged to the swipe-based recogniser the rewrite had already
removed. Fixed by dropping the stale argument; caught going forward by
`tests/test_gesture_wiring.py::test_vision_command_pack_only_passes_
arguments_the_controller_accepts`, which inspects the call site against the
real signature instead of requiring a webcam to catch it.

**Deleted gesture bindings kept coming back.** `assistant/store/defaults.py`'s
seed content for `gestures.json` still listed every pre-rewrite binding
(swipe_left, thumbs_up, rock, ok, call, …). `ConfigStore` merges that seed
*underneath* the real file so new settings appear in old files automatically
— but the merge was recursive for every nested dict, including `bindings`
itself. Deleting a binding from the JSON never stuck: the seed's copy merged
back in on the very next load. This is why gestures kept "colliding" even
after a rewrite that had already deleted the offending bindings from disk.

Fixed at the right layer: `deep_merge` gained a `wholesale` parameter, and
`ConfigStore._read` passes `WHOLESALE_DICT_KEYS` (`gestures.bindings`,
`personas.modes`) so those two keys are replaced wholesale from the user's
file when present, the same treatment lists already got and for the same
reason — a user who deletes an entry means it.
`tests/test_wholesale_merge.py` and `test_gesture_wiring.py::test_a_deleted_
binding_does_not_come_back_from_defaults` pin this so it can't regress
silently again.

**Speech recognition's actual remaining bug: noise-floor calibration, not
sample rate.** The 44.1kHz-vs-16kHz capture fix (§3) was real and necessary,
but calibrating the silence threshold from **3 samples taken the instant
recording opens** was the bug that actually mattered. That window almost
always contains the physical click of pressing the mic button or hotkey,
right next to the microphone. Measured live on this machine: that click
alone produced a threshold of **0.168**, while genuine ambient room noise
over the following three seconds peaked at only **0.09** — meaning normal
speech could never clear the bar, and every recording silently produced
nothing. No exception, no log line a user would see; `_record_until_silence`
just ran its full 15-second timeout and returned `None`.

Fixed with three changes to `SpeechInput._noise_floor`:
- an 80ms settle period is discarded before calibration starts, so a
  button-press transient falls outside the sampled window entirely
- calibration takes far more, smaller samples (40ms chunks over 360ms, ~9
  samples instead of 3) and uses the **20th percentile** instead of the
  median, so one bad instant among many can't dominate
- a hard ceiling (`MAX_SILENCE_THRESHOLD = 0.05`) means a bad calibration can
  never lock real speech out for the rest of that recording, no matter what
  it measured

Also added: `logger.info` on every recording's outcome (heard nothing above
threshold X / captured N seconds / transcript "…") and on anything the noise
filter drops, so a future "it's not working" is diagnosable from the console
log alone rather than requiring someone to have been watching the chat
window at the time. `tests/test_noise_floor.py` reproduces the click-at-
start scenario against a fake audio stream and asserts the calibrated
threshold stays low enough for real speech to clear it.

---

## 3b. Next round again: voice commands did nothing, gestures went nowhere

Three more bugs, found by chasing the previous round's fixes through to
where they actually connect to the UI.

**Voice commands - including exact matches like "open youtube" - produced no
chat message and no console log line at all.** Not a matching bug: the Qt UI
hopped from the microphone's background thread to the GUI thread with
`QTimer.singleShot(0, callback)`. That primitive only fires if the *calling*
thread has an active Qt event loop of its own pumping it; `SpeechInput`
records and transcribes on a plain `threading.Thread` - never a `QThread`,
`exec()` never called on it - so the timer was created successfully every
time and then simply never fired. No exception, no log, the callback (which
calls `Assistant.handle()`) just never ran. Proven directly: emitting from
the same kind of thread, a real `pyqtSignal` delivers every time and
`QTimer.singleShot` delivers nothing (`tests/test_listener_bridge.py`
contains both halves of that comparison, run against a real
`QCoreApplication` event loop, not mocked). Fixed with `ListenerBridge`, a
proper signal-based bridge mirroring `AssistantBridge`'s existing pattern for
job results and `HotkeyBridge`'s for global hotkeys - the mic path was the
one place in the app that had never gotten that treatment.

**Gesture actions fired the hotkey but never told you.** `ctx.progress()`
only works on a thread the job runner explicitly bound with `bind_job()`;
the gesture camera loop is a bare `threading.Thread` that is never job-bound,
so every notification hit a debug-level log line and stopped. Fixed with
`Context.announce(text, speak=True)`, a channel that works from any thread
regardless of binding state - wired to `AssistantEvent("reply", ...)` the
same way a normal command reply is, so a fired gesture now writes a chat line
*and* speaks it: `Gesture (2 fingers): new desktop`.

**Unmatched text was silently handed to the chat model.** `Assistant.
_fallback` piped any unmatched text to the LLM whenever one was configured
and reachable, no `nova` prefix required - directly contradicting
`assistant/commands/chat.py`'s own documented "opt-in by prefix" design. A
garbled voice transcript that failed to match a real command (`"generate any
penalties before."`) silently became a 15-20 second conversation with
whatever persona happened to be active instead of a fast "I don't have a
command for that yet." Fixed: `_fallback` always returns the plain message
now. `nova <message>` still reaches chat exactly as before, since it matches
the `chat` command directly in the router and never reaches `_fallback` at
all. Every dispatch outcome is now logged at INFO level
(`Dispatching '...' -> '<command>'` or `No command matched: '...'`), so this
class of "why did nothing happen" is diagnosable from the terminal without
needing to have been watching the chat window.

Also added: `fist` is no longer a second neutral pose alongside open palm -
it is bound to **previous desktop**, pairing with the existing three-fingers
→ next desktop. Open palm is now the *only* neutral/reset pose.

---

## 3c. Speech was reading things nobody would say out loud

Two complaints, same underlying mistake in opposite directions: the previous
round's "speak everything, not just line one" fix was correct as far as it
went, but it made no distinction between a short answer (which *should* be
read in full) and a numbered listing (which should not be read as a list at
all). `"Opened https://www.youtube.com"` was read with the full URL, and a
13-file picker was read filename by filename, size and date included -
technically "everything", but not what a person would actually say.

`TextToSpeech._for_speech` (the one chokepoint every spoken reply passes
through) now does two things before the existing length-cap logic:

- **URL shortening.** `https://www.youtube.com` → `youtube.com` in speech
  only; the chat text is untouched, so the full address is still there to
  read or click.
- **List detection.** Three or more lines matching a bullet/number pattern
  (`- `, `* `, `1.`, `2)`, …) means this is a picker or listing, not a short
  reply. Only the first non-list line - the question or header - is spoken,
  followed by "Check the chat for the full list." Two list-shaped lines still
  read in full; the threshold exists so an ordinary two-item answer that
  happens to use dashes isn't over-triggered into a summary.

Both changes are content-shape heuristics rather than per-command flags, so
they apply uniformly to every existing "list X" command and any future one,
with no risk of a new command forgetting to opt in.

---

## 4. Config reference

Files live in [`config/`](../config/) — hand-editable, one per domain. Runtime
state (reminders SQLite, logs, the downloaded STT model) lives separately in
`%APPDATA%/NovaAssistant/` so editing config can never clobber it.

A `reports.json` entry:

```json
{
  "name": "pending penalties report",
  "trigger": ["generate pending penalties report", "run pending penalties"],
  "working_directory": "D:/Office/Scripts/Pending Penalties report",
  "run_as": "exe",
  "target": "dist/generate_pending_penalties_report.exe",
  "input": { "mode": "arg", "extensions": [".csv"], "stage_to": "files" },
  "collect_from": ["dist", "output", "."],
  "route_output": "day_folder",
  "collect_mode": "move",
  "on_conflict": "version"
}
```

`run_as`: `python` (uses the project's own venv if it has one) · `venv_python`
· `exe` · `shell` · `open` · `url` · `folder`.
`route_output`: `day_folder` · `none` · an explicit path.
`collect_mode`: `move` (default) or `copy`.
`on_conflict`: `version` (never overwrites, adds ` (2)`) · `overwrite` · `skip`.

### `refresh` is load-bearing

After any hand-edit, `refresh` rereads **every** file, rebuilds every
config-driven command and says what changed.

- A file that fails to parse keeps its **last-good in-memory copy** and is
  reported — one stray comma can't stop reminders firing.
- Writes are atomic (temp file + `os.replace`), so a crash mid-write can never
  leave a truncated config.
- New defaults are deep-merged *underneath* your edits, so upgrading never
  overwrites something you customised.

---

## 5. Testing

```powershell
.\.venv\Scripts\python -m pytest tests/ -q     # 296 passed
```

Covers: config store (refresh after hand-edit, broken-JSON resilience, atomic
writes, deep-merge), day folders, the report pipeline end-to-end with a
throwaway generator, input staging, the job queue (priority, progress,
cancellation, failure isolation), the wizard, numbered selections, router
matching including fuzzy and dynamic providers, browser modifier parsing,
reminder parsing, gesture classification, and the merge — both paths, plus the
byte-copy edge cases (missing trailing newline, quoted commas, embedded
newlines, differing quote styles, column-order changes).

**Measured on real data, not mocked:** the 0.5 s streaming merge and the 16.2 s
parsed merge were both run against the actual 126 MB + 71 MB exports and
verified to produce 819,241 rows that read back cleanly. Ollama latency
(6.5 tok/s, 3–6 s cold load) was measured against the running server.

**Not automatically tested** — they take over hardware or change real work
files: the microphone, the webcam gesture loop, and running a real report
generator end-to-end.

---

## 6. Known limits

- **Chat persona quality is capped by the model.** `qwen2.5:1.5b-instruct` on
  ~2.2 GB free RAM will drift out of character. A 3B model after the RAM
  upgrade fixes this; recommending one now would just make it swap.
- **Fleet Analysis and Ranking Report have no `dist/` build**, so they run as
  `.py`. Build them with PyInstaller and they switch automatically.
- **Gestures track one hand.** Two hands roughly doubles per-frame inference
  cost, which on this CPU trades smoothness for coverage — the wrong trade.
- **Weather comes from a free third-party endpoint** and can differ a few
  degrees from Google, which blends several sources.
- **`config/` sits inside the git repo** and holds your real links and paths.
  Add it to `.gitignore` before pushing anywhere public.
- The **legacy CustomTkinter UI** (`--legacy-ui`) still works but is a
  fallback; the Qt HUD is the real front-end.
