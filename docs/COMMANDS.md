# Nova — complete command guide

Every command, what it does, and how to phrase it. **208 commands** across 28
areas. Type `help` in the app for the same list generated live from the
registry, or `help reports` for one area.

Three things that apply everywhere:

**You don't need exact wording.** Matching goes regex → exact phrase → longest
matching phrase → fuzzy. `genrate pendng penalties reprt` still lands on the
right report. Filler like "please", "can you", "hey" is stripped.

**Numbered lists answer with a number — or several.** Any command that finds
several matches parks a list. Answer with one number, or with *all* the
numbers you want in a single message:

```
1 2 3 4      four items, in that order
1,2,3        commas work too
1-4          so do ranges
2 and 4
```

Order matters where it means something — merging stacks files in the order
you list them. Or a verb plus a number to do something else with one item:

| You say | What happens |
|---|---|
| `3` | the default action (usually open) |
| `run 3` | run it |
| `vs code 3` | open in VS Code |
| `folder 3` | open its folder in Explorer |
| `terminal 3` | a shell in that folder |
| `use 3` | remember it for the next step |

A list expires after 5 minutes, and any real command clears it — a stray `3`
typed later can't fire something unexpected.

**You never need to type a path.** Folder and file commands search your drives
by name. `open folder office` finds `D:\Office`.

**A reply is spoken the way you'd actually say it out loud.** A short answer
or confirmation is read in full — not the first line with "plus 6 more lines
in the chat", which is what it used to do. A **list** (a file picker, `help`,
any `list X`) is not read item by item — nobody reads "one: sheet three, two:
sheet two, three: sheet one..." aloud for a 13-file picker. Three or more
list-shaped lines and only the question gets spoken: *"Which file should the
report use? Check the chat for the full list."* The full list is always still
in the chat window either way. URLs are shortened to just the domain when
spoken — `"Opened https://www.youtube.com"` is said as *"Opened
youtube.com"* — the chat text keeps the full address. Mute all of this from
the tray menu.

---

## 1. Reports

The core daily workflow. Every generator under `D:\Office\Scripts` was found
automatically and lives in `config/reports.json`.

| Command | What it does |
|---|---|
| `list reports` | Numbered list of every generator — then say `3` to run it |
| `generate pending penalties report` | Runs it, then files the output into today's folder |
| `generate <report> from <path>` | Uses a specific input file |
| `what's in today's reports` | Lists what's already been filed today |
| `open today's reports` | Opens (creating if needed) `D:\Office\Reports\<today>` |
| `recent reports` | The last few day folders, with file counts |
| `scan for reports` | Re-scans for newly added generators |
| `reports folder to <path>` | Changes where output is filed |
| `where do reports go` | Explains the current setup |

**Generators available:** fleet analysis, fmo penalties non compliance,
pending complaint analysis, pending penalties, pending penalties under 2%,
ranking report (complains kpi), tmo scoring.

### How input files work

Every generator needs a raw data file. You don't type paths:

1. **Name it `data`.** Download the export, rename it to `data.csv` (or
   `data.xlsx`), and `generate pending penalties report` picks it up from
   Downloads with no further questions.
2. **Or pick from a list.** With no `data` file, you get a numbered list of
   recent matching downloads. Say `1` — or `1 2 3 4` if the generator takes
   several files (Pending Complaint Analysis wants four).
3. **Or be explicit**, one path or many:
   `generate pending penalties report from D:\exports\july.csv`
   `generate complaint analysis from D:.xlsx, D:.xlsx and D:\c.xlsx`

### The TMO report asks for its period

TMO scoring needs a date range, and a report silently covering the wrong dates
looks completely fine and is completely wrong - so it asks:

```
you : generate tmo scoring report
nova: Using data.csv. What period should 'tmo scoring report' cover?
        e.g. '1 august to 7 august', 'last 7 days', 'this month'
you : 1 august to 7 august
```

That becomes `--csv "data.csv" --from-date 2026-08-01 --to-date 2026-08-07`.
Say it up front to skip the question:
`generate tmo scoring report for last 7 days`.

Understood phrasings: `1 august to 7 august` · `2026-08-01 to 2026-08-07` ·
`01/08/2026 - 07/08/2026` · `between 1 aug and 7 aug` · `last 7 days` ·
`this month` · `last month` · `july` · `today` · `yesterday`.
No other report asks - the rest work out their own period from the data.

The chosen file is copied into the generator's own `files\` folder before the
run, so the script finds it exactly where it expects. Your download stays in
Downloads — it's copied, not moved.

### All seven run as an exe

Every generator now runs its frozen `dist\*.exe` build - they carry their own
dependencies, so a report behaves the same whether or not the project's venv is
intact. Each one's command line was checked against the exe's own `--help`:

| Report | How it's invoked |
|---|---|
| fleet analysis | one positional path |
| fmo penalties non compliance | one positional path |
| pending complaint analysis | **four** positional paths |
| pending penalties | one positional path |
| pending penalties under 2% | one positional path |
| ranking report (complains kpi) | **two** positionals - complaints, then attendance |
| tmo scoring | `--csv` plus `--from-date` / `--to-date` |

**Python Bot (PED) is not a report.** `open python bot` (or `python bot`,
`run python bot`) opens it in VS Code and stops there — the assistant never
executes it. It's an interactive scraper you drive yourself, and `scan for
reports` will not re-register it.

### Watching scripts run

Scripts and reports run in **a visible terminal window** by default, so you
can see what's actually happening. The assistant still waits for the run to
finish and files the output as normal.

- `hide terminals` — run them silently, output to the status line instead
- `show terminals` — back to visible

Anything that prompts for input needs a terminal, so leave this on unless you
have a reason not to.

### Where output goes

Files produced by a run are moved into `D:\Office\Reports\<today>` — e.g.
`7 August`. The folder is created if it doesn't exist. Output is detected by
diffing the filesystem around the run, so it keeps working when you rename
things inside a script. Excel lock files (`~$…`), `venv\`, `build\` and the
input file you supplied are never collected.

To leave a copy behind in the generator's own folder, set
`"collect_mode": "copy"` on that entry in `config/reports.json`.

---

## 2. Excel and spreadsheets

| Command | What it does |
|---|---|
| `merge downloads` | Lists recent spreadsheets — answer `1 2 3 4` and all of them merge |
| `merge last 3 downloads` | Stacks the newest N with no picking |
| `merge july data and august data` | Stacks two named files |
| `combine sheets in <workbook>` | Stacks every sheet of one workbook into one |
| `excel info <file>` | Sheets, rows, columns — without opening Excel |
| `convert <file> to csv` | Spreadsheet → CSV |
| `convert <file> to excel` | CSV → real `.xlsx` |

**Any number of files, answered once.** `merge downloads` shows the list; you
reply `1 2 3 4` and they're all stacked, in that order. No repeated prompts,
no two-file limit.

**Merging is always CSV out, and it's fast.** Two exports of 539,604 and
279,637 rows merge in **0.5 seconds**. When the inputs are CSVs with the same
columns — the normal case for two exports of the same dashboard — the files
are concatenated as raw bytes without ever being parsed. Different quoting
styles between files are fine; only a genuine column difference falls back to
the slower column-aligning path (~16s for the same data), which keeps rows
aligned by column *name* so nothing lands in the wrong column.

The merged file is written next to the first input as `Merged <date> <time>.csv`.

---

## 3. Finding things

| Command | What it does |
|---|---|
| `open folder office` | Finds and opens it, wherever it lives |
| `find nova folder` | Same, phrased the other way |
| `find july data` | Searches your drives for a file |
| `find document tehsil list` | Searches Documents, Data and Downloads only |
| `recent downloads` | Newest files in Downloads, numbered |
| `clean downloads` | Lists Downloads files older than 30 days |

Searching covers `D:\`, your Office and Entertainment roots and your home
folder, five levels deep, skipping `venv`, `node_modules`, `.git`,
`AppData`, `Program Files` and similar. It takes 20–60 ms. Add more roots
under `defaults.search_roots` in `config/core.json`.

---

## 4. Projects

| Command | What it does |
|---|---|
| `list projects` | All of them, numbered |
| `list python projects` | Filtered by type (also `flutter`, `wordpress`) |
| `open <name>` | Opens it in VS Code |
| `run 3` | **Runs** it — the report pipeline if it's a generator, else its exe/script |
| `folder 3` | Opens it in Explorer |
| `terminal 3` | A shell in that folder |
| `create python project scraper` | Scaffolds `main.py`, requirements, `.gitignore`, registers it (add `with venv`) |
| `create flutter app myapp` | Runs `flutter create`, registers it |
| `register python project x at D:/path` | For projects it didn't create |
| `rescan projects` | Re-scan, dropping anything that moved |
| `flutter run myapp` | Runs a registered Flutter project |

---

## 5. Websites and links

| Command | What it does |
|---|---|
| `open youtube` | Default browser, existing window |
| `open my portfolio in a new window` | Fresh window |
| `open client site admin in edge` | That specific browser |
| `open github in a new firefox window` | Both at once |
| `open gmail in incognito` | Private window |
| `open example.com` | Any bare address, no setup needed |
| `add website github.com as gh` | Registers it — live immediately |
| `add wordpress site clientco as https://clientco.com/wp-admin` | |
| `add link portfolio as https://myportfolio.com` | Then `open my portfolio` |
| `list websites` | Everything registered |
| `default browser to edge` | Which browser "new window" uses |

Three separate files on purpose — `websites.json`, `wordpress.json`,
`personal_links.json` — so a client's site can never be confused with
`open my portfolio`.

---

## 6. Media

| Command | What it does |
|---|---|
| `play interstellar` | Searches **every** library — video and audio — and plays it |
| `play parasyte` | Several matches → numbered list, say `8` |
| `folder 3` | Opens where that file lives instead of playing it |
| `list movies` / `list music` / `list clips` / `list series` | Library contents |
| `open movies folder` / `open entertainment` | Explorer |
| `add movies folder E:/Films` | Registers another location |

`play <name>` searches by filename across all libraries. It no longer only
looks in the Windows Music folder — that was the old behaviour and it's gone.

---

## 7. Local chat (Ollama)

Chat is **opt-in by prefix** so the assistant stays predictable: `open chrome`
always opens Chrome, and only `nova <message>` reaches the model.

| Command | What it does |
|---|---|
| `nova <anything>` | Talk to the model in the current mode |
| `ask <question>` | Same thing, different word |
| `<name> mode` | Switch persona — e.g. `office mode`, `fun mode` |
| `list modes` | Every persona, numbered |
| `chat status` | Exactly why chat is or isn't working |
| `enable chat` | Switches it on and warms the model |
| `reset chat` | Clears the conversation memory |
| `list models` | What Ollama has installed |
| `model to qwen2.5:3b-instruct` | Switch model |

### The 14 modes

| Mode | For |
|---|---|
| `default` | Everyday helper — short, practical |
| `office` | Excel, MS Office, reporting, data — professional |
| `flutter` | Flutter/Dart expert |
| `wordpress` | Themes, plugins, hosting, portfolio sites |
| `portfolio` | **New** — LinkedIn posts, blog ideas, portfolio copy in your voice |
| `writer` | General drafting |
| `study` | Patient tutor — explains, then quizzes |
| `islamic` | Twelver Shia (Ja'fari) perspective, always with references |
| `motivation` | **New** — direct accountability coach, one concrete action |
| `fun` | Banter, jokes, hanging out |
| `meme` | Meme-speak, lowercase, emoji |
| `ragebait` | Provocative hot takes, for argument practice |
| `personality` | Reply as any personality you name |
| `podcast` | Long-form two-way conversation |

Typos are tolerated — `memo mode` switches to `meme`.

### Speed and length, honestly

This machine generates about **6.5 tokens per second**. That is the hard
floor; no prompt tweak changes it. What makes it usable:

- **Answers are short now.** Every mode is capped — 80–110 tokens for banter,
  170 for the work modes, 260 for drafting — and the system prompt says two
  or three sentences is a normal reply. Typical answers are now ~50 words in
  15–20 seconds, instead of 150+ words in over a minute.
- **The answer appears as it's written**, in the chat bubble, repainted a few
  times a second. You read along instead of watching nothing.
- **`keep_alive`** keeps the model loaded between questions, saving the 3–6
  seconds it used to spend reloading weights every single time.
- **Context window of 2048** — plenty for chat, much faster on CPU.
- **Warm-up on `enable chat`** so the first question isn't a cold start.

If you want a long answer, ask for one ("give me a full draft", "explain in
detail") — the cap is a default, not a ceiling on what you can request.

**On persona quality:** you have ~2.2 GB RAM free, so `qwen2.5:1.5b-instruct`
is the right size. A 1.5B model *will* drift out of character sometimes —
that's the model, not the prompt. After the 16 GB upgrade,
`ollama pull qwen2.5:3b-instruct` then `model to qwen2.5:3b-instruct` will be
markedly better at ragebait, personality and podcast modes.

Your profile (Lahore, Khushab, BSIT at Bahria, Shia, the work you do) is
injected into the modes where context helps — office, islamic, portfolio,
study, flutter, wordpress, writer, default. It is deliberately **not**
injected into fun, meme, ragebait, personality, podcast or motivation: a small
model handed a biography recites it back instead of staying in character,
which is exactly why ragebait mode turned into an interview about your degree.

---

## 8. Reminders

| Command | What it does |
|---|---|
| `remind me to post on linkedin every monday at 9am` | Weekly |
| `remind me to stretch every day at 15:30` | Daily |
| `remind me to renew the domain on 12 December at 11:00` | One-off |
| `remind me to call ali in 30 minutes` | Relative |
| `list reminders` | Everything, with next fire time |
| `remove reminder <text>` | Deletes it |

Reminders fire as a chat message, a tray notification and a spoken line.
One-offs that already fired don't re-fire after a restart.

---

## 9. Voice

| Command | What it does |
|---|---|
| `live listening` | Continuous — just talk, no hotkey |
| `stop listening` | Back to hotkey-only |
| `wake word to nova` | In live mode, only act on speech containing it |
| `wake word to none` | Act on everything you say |
| `mic test` | Checks the mic and reports what it actually captured |
| `hotkeys` | Lists the global shortcuts |

**Two separate bugs made this not work, fixed in two rounds.**

*Round one:* audio was captured at 16 kHz because that's what Whisper wants.
The mic runs at 44.1 kHz and, asked for 16 kHz, handed back clipped garbage —
ambient RMS 0.017 with peaks at 0.96, versus 0.005 / 0.27 at its native rate.
Fixed by capturing at the mic's own rate and resampling afterwards.

*Round two, the one that actually mattered:* the silence threshold used to be
calibrated from just **3 samples** taken the instant recording opened — and
that window nearly always contains the physical click of pressing the mic
button or hotkey, right next to the microphone. Measured live: that one click
produced a threshold of **0.168**, while real ambient room noise over the
next three seconds peaked at only 0.09. Nothing you said could ever exceed
0.168 RMS, so every recording silently produced nothing — no error, just
"I didn't catch that" forever. Fixed three ways: a short settle period is
discarded before calibration starts, many more (smaller) samples are taken
and the *20th percentile* is used instead of a 3-sample median so one bad
instant can't dominate, and a hard ceiling (0.05) means a bad calibration can
never lock real speech out no matter what. Run `mic test` to see the actual
numbers for your room.

Three ways to talk, all transcribed locally by faster-whisper (`base.en`,
int8, CPU — no internet, no API key):

1. **Tap** the MIC button or `Ctrl+Shift+Space` — records until you stop.
2. **Hold** `Ctrl+Shift+Space` — records for exactly as long as you hold it.
   More reliable in a noisy room where silence detection guesses wrong.
3. **Live mode** — say `live listening`. A cheap energy gate watches the mic
   and only wakes Whisper when it actually hears speech, so between utterances
   the machine is essentially idle.

A wake word is strongly recommended in live mode, or every passing comment
gets routed as a command.

### Voice commands were doing nothing at all - even exact matches

Not a matching problem, not the chat fallback below - a real threading bug.
`"open youtube."`, transcribed correctly, produced **no chat message, no
console log line, nothing** - not even an attempt. The transcript log line
was the last thing that ever appeared.

The Qt UI hopped from the microphone's background thread to the GUI thread
with `QTimer.singleShot(0, callback)`. That only works when the thread
*calling* it has its own Qt event loop already running to pump the timer.
Recording and transcription run on a plain `threading.Thread` - never a
`QThread`, nothing ever calls `exec()` on it - so it has no event loop of
its own. The timer was created successfully every single time and then just
never fired: no exception, no log, the callback silently never ran. That
callback was the one that calls `Assistant.handle()` - so voice input never
reached the command router at all, regardless of what was said.

Proven directly: emitting the exact same way from the exact same kind of
thread, a real Qt signal delivers every time; `QTimer.singleShot` delivers
nothing. Fixed by replacing it with `ListenerBridge`, a real signal-based
bridge (the same pattern the rest of the app already used for job results
and hotkeys) - `tests/test_listener_bridge.py` pins both halves of that
comparison so it can't quietly regress back to the broken version.

### A transcript that matches nothing now says so, fast

A garbled transcript (background noise, a half-finished sentence) used to be
silently handed to the local chat model whenever one was configured and
running - no "nova" prefix required, contradicting the documented "chat is
opt-in by prefix" rule elsewhere in this file. In practice that meant a
failed attempt at a real command (`"generate any penalties before."` instead
of `"generate pending penalties report"`) turned into an unrelated 15-20
second conversation with whatever chat persona happened to be active,
instead of a fast, clear "I don't have a command for that yet." Fixed:
unmatched text always gets the plain fallback message. A real
`nova <message>` still reaches chat exactly as before - it matches the
`chat` command directly and never touches this path.

Every transcript's outcome is now logged (`Transcript: '...'`, then either
`Dispatching '...' -> '<command>'` or `No command matched: '...'`), so what
happened to a voice command is always visible in the terminal log, not only
in the chat window.

---

## 10. Global hotkeys

| Hotkey | What it does |
|---|---|
| `Ctrl+Alt+Space` | Show / hide the window from anywhere |
| `Ctrl+Alt+H` | Hide (without toggling back) |
| `Ctrl+Shift+Space` | Hold to talk; tap to record until you stop |
| `Esc` | Hide the window (when it's focused) |
| `↑` / `↓` | Walk command history in the input box |

Hotkeys are global — they work whatever app you're in. If they don't register,
the status line says so; the usual cause is another app holding the same
combo, or Windows blocking the keyboard hook (try running as administrator).

The window's **✕ hides to tray**, it doesn't quit. Quit is on the tray menu
only, so a stray click can't kill it. Left-click the tray icon to toggle the
window; right-click for the quick menu.

---

## 11. Gestures

Count fingers at the camera. That is the whole vocabulary — it is the one
thing a webcam reads reliably, which is why everything cleverer was removed.

| Show | Action |
|---|---|
| ✊ Fist (0 fingers) | **Previous desktop** (`Ctrl+Win+←`) |
| ☝ **1** finger | Switch window (`Alt+Tab`) |
| ✌ **2** fingers | New desktop (`Ctrl+Win+D`) |
| **3** fingers | Next desktop (`Ctrl+Win+→`) |
| **4** fingers (thumb tucked) | Minimise everything (`Win+M`) |
| ✋ **5** fingers *pressed together* | **Stop talking** — cuts off speech mid-sentence |
| 🖐 **5** fingers *spread apart* | **Neutral** — resets, fires nothing |

Every count does something. **Open palm is the only neutral pose** —
deliberately singled out, because it is the one shape that could never be
mistaken for an action. Show it between gestures whenever you want to repeat
the one you just did (see "repeating a gesture" below). The two five-finger
poses are the traffic-policeman "stop" (fingers tight together, palm out)
versus a relaxed open hand (fingers splayed).

| Command | What it does |
|---|---|
| `start gestures` | Turns on the webcam and starts watching |
| `stop gestures` | Stops and frees the webcam |
| `list gestures` | Every pose and what it does |
| `rebind two to ctrl+windows+d` | Change a binding |
| `rebind three to command: what's my day` | Point a gesture at an assistant command |
| `gestures too sensitive` / `gestures too slow` | Retunes hold time and cooldown |

### Every gesture tells you what it did

Firing a gesture now writes a line into the chat and speaks it — the same
"opened Chrome for you" confirmation a typed command gives you:

```
Gesture (2 fingers): new desktop
Gesture (fist): previous desktop
```

This used to go nowhere at all - not the chat, not speech, nothing. The
webcam loop runs on its own background thread, and the status channel it was
wired to only works on threads the job queue explicitly binds; the gesture
thread never is one, so every notification silently evaporated the moment it
was sent. Gesture feedback now goes through a channel built for exactly this
- any thread, job or not - so it reaches chat and speech every time.

### Repeating a gesture

Firing the *same* pose twice in a row needs an open palm in between - show
two fingers, then two fingers again, and the second one is ignored until you
reset. This is deliberate, not a bug: without it, holding a pose a moment too
long (which is trivially easy - hands are not that precise) would fire it
twice. A *different* pose never needs a reset - two fingers then three fires
both, back to back, no palm required in between.

### What was removed, and why it works now

**Swipes are gone.** Motion detection and pose detection fought each other: a
hand sweeping across the frame is always holding *some* shape, so one
movement produced a stray action, the swipe, or both. Removing motion
entirely removed that whole class of collision — and every timing knob it
needed.

**Rock, OK, call sign, thumbs up/down are gone.** Each rested on a fiddly
geometric test that only held up in good light at one hand angle. A gesture
that works most of the time is worse than no gesture, because you stop
trusting the feature.

**The reason it often did nothing at all:** after any gesture fired, the
recogniser disarmed and *only an open palm re-armed it*. So two fingers
followed by three did nothing — the second pose was blocked until you
happened to flash a palm. Re-arming is per-pose now: a different shape always
fires, and only repeating the *same* shape needs a neutral in between.

**Four fingers vs five** used to be decided by measuring the thumb against
the index knuckle, where a thumb held alongside the index — exactly what a
flat hand does — was ambiguous. It is now measured against the *pinky*
knuckle: a folded thumb crosses the palm and lands near it, an extended one
is far outside. Clean separation.

**`start gestures` crashed outright, and deleted gestures kept coming back.**
Two separate bugs, both fixed:

- The command wiring still called the controller with a `min_travel=`
  argument left over from the swipe-based version, which no longer accepts
  it — every `start gestures` failed with a `TypeError` before the camera
  even opened.
- More subtly: the seed content used to fill in a config file that doesn't
  exist yet still listed every old swipe/thumbs/rock/OK/call binding. Config
  files are merged with that seed on every load so new settings appear in
  old files automatically — but that merge was recursive for *every* nested
  dict, including the gesture bindings themselves. So deleting a binding from
  `config/gestures.json` never actually stuck: the seed's copy of it merged
  back in on the very next load, which is why gestures kept "colliding" even
  after a rewrite that had already removed them from the file. Registries
  like `bindings` (and chat `modes`) are now replaced wholesale from your
  file when present, the same way a list already was — a deletion is a
  deletion.

**There was no way back to the previous desktop.** Fist used to be a second
neutral pose (alongside open palm), which wasted an entire finger-count on
nothing. It is now bound to previous desktop, pairing naturally with three
fingers → next desktop. Fist detection needed no changes to make this safe —
it was already the one pose with zero ambiguity (no fingers extended, full
stop), so handing it a real action costs nothing in reliability.

### Staying smooth

- **15 FPS**, down from 20. Static poses need no motion resolution, so this
  is pure headroom given back to whatever you're actually doing.
- **Detection threshold 0.5**, down from 0.6 — missing your hand entirely is
  the worst failure, and the hold requirement already filters weak frames.
- **Hold ~5 frames** (about a third of a second) before anything fires.
- **1 second cooldown** after each gesture.
- **One hand**, deliberately: two roughly doubles per-frame inference cost.

If it fires too easily, say `gestures too sensitive`. If you hold poses and
nothing happens, `gestures too slow`.

Bindings live in `config/gestures.json`. Any pose can fire a hotkey
(`"action": "hotkey"`), a full assistant command (`"action": "command"`), or
stop the speech (`"action": "stop_speaking"`).

---

## 12. Your PC

### Windows control
`shutdown pc` · `restart pc` · `cancel shutdown` · `lock pc` ·
`minimize all` · `minimize active window` · `maximize window` ·
`restore windows` · `snap window left` / `right` · `switch window` ·
`task view` · `new virtual desktop` · `close virtual desktop` ·
`next` / `previous virtual desktop` · `active window` · `close active window` ·
`open run dialog` · `open action center` · `open clipboard history` ·
`open emoji panel` · `screenshot` · `capture screenshot` ·
`empty recycle bin` · `toggle theme` · `show start` · `open windows search`

### Volume
`volume up` · `volume down` · `mute volume` · `current volume` ·
`set volume to 50`

### Settings pages
`open display settings` — also sound, wifi, network, airplane mode, bluetooth,
night light, power, battery, apps, personalization, update, storage, privacy,
accounts, notifications.

### Apps
`open notepad` (26 registered by default) · `register app spotify at C:\...` ·
`list running apps` · `is chrome running` · `close app notepad`

### Files
`list files in <path>` · `create folder` · `create file` ·
`rename <a> to <b>` · `move <a> to <b>` · `copy <a> to <b>` ·
`delete file` (confirms first) · `find files *.pdf in D:\Downloads` ·
`compress folder` · `extract archive` · `file info` ·
`open downloads` / `desktop` / `documents` / `pictures` / `videos` / `music`

### Chrome (26 commands)
`chrome new tab` · `close tab` · `reopen tab` · `next` / `previous tab` ·
`switch tab to 3` · `new window` · `close window` · `incognito` ·
`reload` · `hard reload` · `zoom in` / `out` / `reset zoom` · `print` ·
`find` · `address bar` · `view source` · `dev tools` · `full screen` ·
`history` · `downloads` · `bookmark` · `bookmarks manager` ·
`clear browsing data` · `task manager`

### YouTube
`youtube pause` · `play` · `fullscreen` · `captions` · `speed up` ·
`next` · `mute` — and `search youtube for lofi beats`

### Shell and clipboard
`run command ipconfig` (allowlisted) · `force run <cmd>` (bypasses it) ·
`read clipboard` · `copy to clipboard <text>` · `clear clipboard`

### System info
`what is the time` · `what is the date` · `system details` · `storage details` ·
`system uptime` · `network info` · `top processes` · `gpu info`

### Web
`weather in Lahore` · `wikipedia black holes` · `search google for <q>` ·
`my ip` · `my location` · `tell me a joke`

### Tools
`speed test` (download, upload, ping — ~20s) · `am i online` ·
`calculate 1250 * 0.17` · `timer for 10 minutes` · `what's my day`

---

## 13. Teaching it new things

Creating a command needs several pieces of information, so it's always a short
conversation — never hand-written JSON:

```
you : add command
nova: Which folder is this command for?
you : D:\Office\Scripts\MyThing
nova: What should this command actually do?   1. Python script  2. exe  …
you : 1
nova: Which file?   1. build.py   2. helper.py
you : 1
nova: What should I say to trigger this?
you : run my thing, do the thing
nova: Give it a short name:
you : my thing
nova: Anything after it runs?
you : no
nova: Here's what I'll save: …  Save it? (yes / no)
you : yes
```

`cancel`, `back` and `skip` work at any point. A trigger that would collide
with an existing command is refused before saving.

Also: `list my commands` · `remove command <name>` ·
`save this directory <path>` (the wizard offers it as the default next time).

---

## 14. Config and housekeeping

| Command | What it does |
|---|---|
| `refresh` | Rereads every config file and rebuilds commands — **no restart** |
| `open config folder` | Opens the JSON folder |
| `edit websites` | Opens one config file in your editor |
| `status` | Command count, report count, where things go |
| `setup workspace` | Creates any missing Entertainment/Office folders |
| `show terminals` / `hide terminals` | Whether scripts run in a visible window |
| `start with windows` | Adds a Startup shortcut (hidden in tray) |
| `don't start with windows` | Removes it |
| `help` / `help reports` | This list, live from the registry |

**`refresh` is the important one.** Hand-edit any file in `config/`, say
`refresh`, and it's live. A file that fails to parse keeps its last good copy
and is reported — one stray comma in `websites.json` can't stop reminders
firing.

### The config files

| File | Holds |
|---|---|
| `core.json` | Shared defaults, day-folder format, browsers, search roots, LLM settings |
| `reports.json` | Report generators, their input rules, where output is filed |
| `scripts.json` | Commands you added through the wizard |
| `projects.json` | Registered code projects |
| `websites.json` / `wordpress.json` / `personal_links.json` | Links |
| `media.json` | Movie/series/music/clip libraries |
| `reminders.json` | Reminders (human source of truth) |
| `gestures.json` | Gesture bindings, hold time, cooldown |
| `personas.json` | Chat modes, your profile |
| `workspaces.json` | Folder scaffold |
