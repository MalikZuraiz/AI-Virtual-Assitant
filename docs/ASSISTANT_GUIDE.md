# Nova — assistant guide

What is actually built and tested, as of this round. See "Not done yet" at the
end for the honest gaps.

This round rebuilt the assistant around the [project brief](assistant-project-brief.md):
a tray-resident PyQt6 HUD, a worker queue so the GUI never blocks, and a split
JSON config layer that turns "what the assistant can do" into editable data
rather than code.

---

## 1. The idea in one paragraph

Most of what this assistant does is **not written in Python** — it is written in
`config/*.json`. Report generators, websites, WordPress sites, personal links,
projects, media libraries, reminders and your own custom commands are all config
entries, and each one becomes a live command the moment the file is saved and
`refresh` runs. The Python side is a router, a worker pool and a set of *packs*
that know how to turn each kind of config entry into an action. Adding a website
is a one-line JSON edit; adding a whole new report generator is a folder on disk
plus one `scan for reports`.

---

## 2. What you can do with it

### Reports — the daily driver

Your generators under `D:/Python Reporting/…` were discovered automatically and
registered in [`config/reports.json`](../config/reports.json). Saying:

```
generate pending penalties report
```

runs the generator (its built `dist/*.exe` if there is one, otherwise the script
with the project's *own* `venv` interpreter), watches the filesystem for what it
produced, then **creates today's folder and files the output into it**:

```
D:/Reports/7 August/Agency Wise Pending Penalties Jul-26.xlsx
```

The day-folder naming (`7 August`) matches the archive you already had — no
leading zero, full month name. It is a format string in `config/core.json`, so
`{yyyy}-{mm}-{dd}` or anything else is a one-line change. Existing folders are
matched case- and whitespace-insensitively so `7 August` and `7 august` never
become two folders.

Output is found by **diffing the filesystem around the run**, not by knowing each
generator's naming scheme — those all differ and change whenever you edit a
script. Excel lock files (`~$Report.xlsx`), build plumbing and everything inside
`venv/`, `build/`, `logs/` and `.git/` are excluded, and a file you passed *in*
as input is never filed away.

Related: `list reports` · `open today's reports` · `what's in today's reports` ·
`recent reports` · `scan for reports` · `reports folder to <path>` ·
`generate <report> from D:/data/export.csv`

### Websites, with browser and window control

Exactly the flow you asked for: put a link in the JSON, and next time it opens.

```
open youtube                          → default browser, existing window
open my portfolio in a new window     → a fresh window
open client site admin in edge        → that browser specifically
open github in a new firefox window   → both at once
open gmail in incognito               → private window
open example.com                      → any bare address works with no setup
```

Three separate files on purpose — `websites.json`, `wordpress.json`,
`personal_links.json` — so a client's site can never be ambiguous with
`open my portfolio`. Add without leaving chat:

```
add website github.com as gh
add wordpress site clientco as https://clientco.com/wp-admin
add link portfolio as https://myportfolio.com
default browser to edge
```

### Teaching it a new command (the wizard)

Creating a command needs several things it cannot infer from one sentence, so
it is always a short conversation — never hand-written JSON:

```
you : add command
nova: Which folder is this command for?
      - say 'yes' to use the one you saved: D:/Office/Scripts
you : yes
nova: What should this command actually do?
      1. Run a Python script   2. Run a program (.exe/.bat)   3. Open a folder or file
      4. Open a project in VS Code   5. Open a link   6. Run a shell command
you : 1
nova: Which file should it run?
      1. sales.py    2. cleanup.py
you : 1
nova: What should I say to trigger this?  (commas for several)
you : run the sales report, sales report
nova: Give it a short name:
you : monthly sales report
nova: Anything after it runs — notify, speak, open a folder, file the output
      into today's report folder? ('no' is fine)
you : file the report
nova: Here's what I'll save: … Save it? (yes / no)
you : yes
nova: Saved to config/scripts.json. It's live right now — try "run the sales report"
```

`cancel`, `back` and `skip` work at any point. A trigger that would collide with
an existing command is refused before it is saved. Related: `list my commands`,
`remove command <name>`, `save this directory <path>`.

### Projects

```
create python project scraper [with venv]   scaffolds main.py/requirements/.gitignore, registers it
create flutter app myapp                    runs 'flutter create', registers it
open scraper                                opens it in VS Code
register python project x at D:/path        for projects it didn't create
list projects · flutter run myapp
```

Anything it creates itself is auto-registered — it already knows the name, type
and path, so there is nothing to ask.

### Entertainment

```
play movie interstellar     fuzzy match across your libraries, plays the file
play song <name> · play clip <name> · play series <name>
list movies · list music · open movies folder · open entertainment
add movies folder E:/Films
```

Matching strips release noise, so `interstellar` finds
`Interstellar (2014) (2014) [1080p]/`, and folders resolve to the largest video
inside them.

### Reminders

```
remind me to post on linkedin every monday at 9am
remind me to stretch every day at 15:30
remind me to renew the domain on 12 December at 11:00
remind me to call ali in 30 minutes
list reminders · remove reminder <text>
```

They fire as a chat line, a tray notification and a spoken line. One-offs that
already fired do not re-fire when you restart.

### Your PC

The previous build's 140-odd automation commands are all still here and now
grouped in `help`: Windows power/desktop/volume/settings pages, 23 Chrome
commands, YouTube playback, app launching, file and folder operations,
allowlisted shell, clipboard, system info, web lookups, virtual mouse.

### Housekeeping

`help` · `help reports` · `status` · `refresh` · `setup workspace` ·
`open config folder` · `edit websites` · `start with windows` · `ask <question>`

---

## 3. Architecture

```
assistant/
  store/                 config as data
    paths.py               where things live (config/ vs %APPDATA%)
    defaults.py            portable seed content for each file
    store.py               ConfigStore: load, refresh, atomic write
    bootstrap.py           one-time discovery of THIS machine's folders
  workspace/             folders, and what gets filed into them
    daybook.py             "today's folder" naming and creation
    scaffold.py            creates the Entertainment/Office hubs
    reports.py             run a generator, file its output
  core/
    assistant.py           Assistant.handle() — the single entry point
    router.py              regex > exact > substring > fuzzy matching
    jobs.py                worker pool (no Qt import anywhere)
    conversation.py        the wizard state machine
    runner.py              subprocess execution for config-described things
    reminders.py           APScheduler + SQLite
    voice.py               edge-tts, pyttsx3 fallback
    listening.py           faster-whisper push-to-talk
    context.py             everything a handler needs
    commands.py            the PC-automation pack (kept from the last build)
  commands/              one module per config-driven domain
    meta.py  links.py  reportpack.py  scriptpack.py  projects.py  media.py  reminders.py
  automation/            browser.py, autostart.py + the previous build's modules
  integrations/          llm.py (optional local Ollama), web.py, music.py
  ui/qt/                 app.py, hud.py, tray.py, bridge.py, theme.py
  ui/app.py              the previous CustomTkinter window (--legacy-ui)
config/                  the JSON files you edit
tests/                   108 tests
```

### Threading — why the GUI cannot freeze

```
GUI thread                      worker pool                  GUI thread
──────────                      ───────────                  ──────────
Assistant.handle(text)
  ├ wizard turn? answer inline (pure memory, instant)
  ├ instant command? run inline (clock, help, config reads)
  └ everything else:
      emit "Running X…"  ──▶  router.run(cmd, text, ctx)
                              ctx.progress("…")  ──▶ status bar
                              returns a Reply     ──▶ chat + TTS
```

`core/jobs.py` imports no Qt at all — it is plain threads, a `PriorityQueue` and
a callback, so the backbone is unit-testable without an application and a
different front-end could reuse it. `ui/qt/bridge.py` is the *only* place the two
worlds meet: workers call `publish()`, which emits a Qt signal that Qt queues
onto the GUI thread. No worker ever touches a widget.

Priority matters in practice: a four-minute report is submitted at priority 8
while a lookup runs at 1, so "what's the time" is never stuck behind it. Two
workers run by default.

The one awkward direction — a worker needing to *ask* the GUI something (a
destructive command's confirmation) — goes through `ConfirmBroker`, which emits
a signal and blocks the worker on an `Event` until the GUI answers, with a
two-minute timeout that defaults to "no".

### Command matching

Precedence is regex → exact phrase → longest substring → fuzzy, so specific
always beats generic and adding a site named "mail" can never shadow
`open my gmail`. Fuzzy (rapidfuzz, threshold in `core.json`) only runs when the
first three miss — that is what catches voice transcripts and typos like
`genrate pendng penalties reprt`. On a real miss you get "did you mean…"
suggestions rather than a dead end.

Config-driven commands come from **providers** the router re-asks on every
`refresh` — that is the mechanism behind "edit the JSON and it just works".

---

## 4. Config reference

Files live in [`config/`](../config/) — hand-editable, one per domain. Runtime
state (reminders SQLite, logs, the downloaded STT model) is kept separately in
`%APPDATA%/NovaAssistant/` so editing config can never clobber it.

| File | Holds |
|---|---|
| `core.json` | Shared defaults: workspace roots, project/script/report folders, day-folder format, browsers, behaviour toggles, `last_active_path`, optional LLM |
| `workspaces.json` | The Entertainment/Office folder scaffold |
| `projects.json` | Registered code projects |
| `scripts.json` | Commands you added through the wizard |
| `reports.json` | Report generators + where their output is filed |
| `websites.json` | General sites |
| `wordpress.json` | WordPress sites, admin panels, hosting dashboards |
| `personal_links.json` | Portfolio, LinkedIn, … |
| `media.json` | Movie/series/music/clip libraries |
| `reminders.json` | Reminders (human source of truth) |

A `reports.json` entry:

```json
{
  "name": "pending penalties report",
  "trigger": ["generate pending penalties report", "run pending penalties"],
  "working_directory": "D:/Python Reporting/Pending Penalties report",
  "run_as": "exe",
  "target": "dist/generate_pending_penalties_report.exe",
  "collect_from": ["dist", "output", "."],
  "route_output": "day_folder",
  "collect_mode": "move",
  "on_conflict": "version"
}
```

`run_as`: `python` (uses the project's own venv if it has one) · `venv_python` ·
`exe` · `shell` · `open` · `url` · `folder`.
`route_output`: `day_folder` · `none` · an explicit path.
`collect_mode`: `move` (default) or `copy` — switch to `copy` if you want output
to stay in the generator's own folder as well.
`on_conflict`: `version` (default — never overwrites, adds ` (2)`) · `overwrite` · `skip`.

### `refresh` is load-bearing

After any hand-edit, `refresh` rereads **every** file, rebuilds every
config-driven command and tells you exactly what changed. It is treated as a
reliability requirement, not a convenience:

- A file that fails to parse keeps its **last-good in-memory copy** and is
  reported — one stray comma in `websites.json` cannot stop reminders firing.
- Writes are atomic (temp file + `os.replace`), so a crash mid-write can never
  leave a truncated config.
- New defaults are deep-merged *underneath* your edits, so upgrading never
  overwrites something you customised, and lists you trimmed stay trimmed.

---

## 5. Voice

**Out (edge-tts):** free Microsoft neural voices, no key. Playback is Windows
MCI via `ctypes` rather than another audio dependency. `pyttsx3` is the offline
fallback, constructed as `Engine(...)` directly — `pyttsx3.init()` caches engines
module-level and returns the same instance, which is why reused engines go silent
after an utterance or two. Speech is queued on one thread so two commands
finishing together can't talk over each other, and long output is summarised
("…plus 12 more lines in the chat") instead of read out in full.

**In (faster-whisper):** local, CPU-only, `base.en` with int8 quantisation, model
cached in `%APPDATA%/NovaAssistant/models`. Push-to-talk (**Ctrl+Shift+Space** or
the MIC button) rather than a wake word — keeping a wake-word model resident
would burn CPU and battery all day on this hardware for a feature used a few
times an hour. Recording stops on ~1.2s of silence, so short commands transcribe
straight away. Voice and typed text both go through the same
`Assistant.handle()`; there is no second code path.

---

## 6. What was kept, replaced and dropped

**Kept** (worked, machine-independent, genuinely one domain — "drive this PC"):
`automation/` (windows_ctl, chrome_ctl, apps, files, shell, system_info,
clipboard), `integrations/` (web, music), `vision/virtual_mouse.py`,
`logging_setup.py`, and all ~140 commands in `core/commands.py` — now grouped
into categories for `help` instead of one flat list.

**Replaced:**
- `ui/app.py` (CustomTkinter) → `ui/qt/` — the brief needs frameless,
  translucent, always-on-top and a tray, which Tk does not do well. The old
  window still runs via `--legacy-ui`.
- `core/router.py` — the registration API is unchanged (so those 140 commands
  needed no edits) but it gained fuzzy matching, dynamic providers, categories,
  priorities and `Reply` objects.
- `core/context.py` — now carries the store, router, job queue and conversation
  state, and builds the virtual mouse lazily so mediapipe is not imported at
  startup.
- `core/speech.py` → `core/voice.py` + `core/listening.py`.
- `main.py` — argparse entry point with `--cli`, `--setup`, `--hidden`.

**Note on `config.py`:** the old `AppConfig` in `%APPDATA%` still holds app-level
toggles (theme, virtual-mouse tuning, app registry, shell allowlist) and is still
edited by the legacy Settings dialog. Workspace/domain data lives in `config/*.json`.
`assistant_name` is read from `core.json`.

---

## 7. Testing

```powershell
.\.venv\Scripts\python -m pytest tests/ -q     # 108 passed
```

Covers: config store (refresh after hand-edit, broken-JSON resilience, atomic
writes, deep-merge), day folders (naming, case-insensitive reuse, creation), the
report pipeline end-to-end with a throwaway generator (output filed into the day
folder, lock files excluded, pre-existing files untouched, conflicts versioned,
failures reported), the job queue (priority, progress, cancellation, failure
isolation), the wizard (happy path, retries, cancel, back, skip_if, save
failure), router matching including fuzzy and dynamic providers, browser modifier
parsing, reminder parsing, plus the previous build's files/shell/virtual-mouse
tests.

Integration tests in `test_command_packs.py` assert the promises the design rests
on: a hand-edited website becomes a command after `refresh`; the wizard's output
is live immediately; report entries get the right triggers and are never marked
instant.

**Verified by hand:** the HUD launches, renders and answers real commands
(screenshotted mid-session running `status`, `what's in today's reports` and
`list movies` against real data), config seeding found all 8 report generators on
this machine, and the workspace scaffold created 13 folders without touching
anything that already existed.

**Not automatically tested** (they take over hardware or change real work files):
the microphone, and running a real report generator end-to-end. The report
*pipeline* is tested with a synthetic generator; the real ones were deliberately
not run, since doing so would produce and move real work files.

---

## 8. Not done yet

- **Gestures (brief §7)** — MediaPipe discrete gesture classification is the last
  planned module and is not built. The existing continuous virtual mouse still
  works via `start virtual mouse`, but the brief asks for discrete classified
  actions instead, which is a separate module.
- **Local LLM** — the interface, config block and `ask <question>` command are all
  wired and tested, but it stays off until you install Ollama and flip
  `llm.enabled`. That is deliberate: 8GB of RAM is not enough headroom yet.
- **File-watching** — `refresh` is implemented and reliable; automatic pickup of
  config edits without saying `refresh` is not (watchdog is installed but unused).
- **`ctx.confirm` in the CLI** — `--cli` mode defaults destructive confirmations to
  "no". Use the HUD for those.
- **Report input files** — most generators are run with no arguments and find
  their own input. `generate <report> from <path>` passes one explicitly; per-report
  default input paths are not modelled yet.

## 9. Two things to know before daily use

1. **Report output is *moved*, not copied**, out of the generator's folder into
   `D:/Reports/<today>`. That is what "put it in today's folder" implies, but if
   you want a copy left behind, set `"collect_mode": "copy"` on that entry in
   `config/reports.json`.
2. **`config/` sits inside the git repo** and will hold your real links and paths.
   That was the brief's layout and it keeps the files easy to find and diff — but
   add `config/` to `.gitignore` before pushing anywhere public.
