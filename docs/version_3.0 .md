# Personal Desktop Assistant v2.0 — Full Architecture Spec
### For implementation by Claude Code

**Hardware:** Lenovo T470s, i7 7th gen, 8GB RAM, no GPU, 512GB SSD, Windows
**Reuse target:** the threading/config patterns already proven in the existing Nova build (worker pool, signal bridge, priority queue, `refresh`, config-as-data) — this doc extends that foundation with a 3-tier local-LLM routing layer and a stricter no-freeze/live-logging contract.

---

## 0. Non-negotiable requirements (read first, build everything to satisfy these)

1. **The UI must never freeze, at any tier, ever.** Not during Tier 1 routing, not during Tier 2 multi-minute planning loops, not during Tier 3 code generation + execution. The window stays responsive, animations keep running, the user can scroll chat, minimize, move the window, open settings — always — no matter what's happening in the backend.
2. **While a job is running, the user watches it happen.** Every backend step — which tool is about to run, which model was called, what it returned, what's happening next — is written to the chat log **live, as it happens**, not dumped all at once at the end. This is a visibility requirement, not a nice-to-have: the user has explicitly said they will sit and wait, so the wait must be legible.
3. **One active job at a time, by the user's own choice.** The user will not issue a second command while one is running — but the system must not crash, hang, or silently drop a command if one arrives anyway. New input during an active job is queued (not executed, not discarded) and announced ("Queued — I'll get to this after the report finishes"), consistent with the existing Nova priority-queue pattern.
4. **All of this applies identically to Tier 1, Tier 2, and Tier 3** — the mechanism is the same background-thread-plus-signal pattern throughout; only the *content* of what gets logged differs by tier (Tier 1 has almost nothing to report since it's near-instant; Tier 2/3 have real step-by-step progress).
5. **Nothing about this document should cause you to skip or drop any tool from the existing tool registry.** Every tool from the original 32/34-tool spec (Groups A–F) is carried forward below, tagged with which tier owns it. If a tool isn't explicitly re-tagged here, default it to Tier 0 (dumb, no LLM) unless its original spec already marked it SMART/LLM-code-gen, in which case it's Tier 2/3 per Section 5.
6. **This is a personal buddy, not a corporate helpdesk bot.** Sections 8–9 define exactly what gets spoken vs. kept in chat-only (no full URLs/paths/code read aloud), the persona/mode system (including an unfiltered teasing/banter mode), and the baseline "proper assistant" behaviors — greetings, weather, small talk, proactive reminders, motivation — that should feel native, not bolted on. Sections 11–15 extend this further: a named identity with multiple mode-specific voices, a real personal-data profile (Section 12) injected only into the appropriate work-adjacent modes, and extra capability tools that stay within the project's existing bounds (free/local only, no paid APIs).

---

## 1. Model tiers — what runs where, and why

| Tier | Model | Role | Q4 RAM | Resident? |
|---|---|---|---|---|
| 0 | none | Regex/pattern dispatch for atomic single-arg commands | 0 | n/a |
| 1 | Qwen3-1.7B | Classification + single-tool-call routing/arg extraction | ~1.4GB | Resident (`keep_alive: -1`) |
| 2 | Qwen3-4B | Multi-step ReAct planning loop | ~2.5GB | **Load-on-demand** (`keep_alive: 0`) |
| 3 | Qwen2.5-Coder-7B (fallback: 1.5B) | Code generation only (pandas, Flutter, debugging) | ~4.5GB / ~1GB | Load-on-demand (`keep_alive: 0`) |

**Revised after review:** only Tier 1 stays resident. An earlier draft of this doc kept Tier 1 + Tier 2 both resident (~3.9GB) — that figure is model-weight-only and understates real memory pressure once Windows, Qt, Ollama's own overhead, KV cache growth, and whatever else is open (Chrome, Excel, VS Code) are accounted for. On an 8GB no-GPU box that margin isn't safe. Loading Tier 2 on demand costs a few seconds of cold-load latency per call — an acceptable trade since Tier 2 requests are already multi-step and slower than Tier 1. Section 1a makes "only one heavy model generating at a time" a hard invariant, not just a default.

```
ollama pull qwen3:1.7b
ollama pull qwen3:4b
ollama pull qwen2.5-coder:7b-instruct-q4_K_M
ollama pull qwen2.5-coder:1.5b-instruct-q4_K_M   # lighter fallback, test before committing to 7B
```

Set `num_ctx` per-call, not globally — see Section 1c (Context Budget). Model choice is configurable, not hardcoded — see Section 1d.

### 1a. ModelManager — an explicit component, not implicit swap logic

Build a real component the rest of the system calls through, rather than scattering Ollama calls with ad-hoc `keep_alive` flags:

```
ModelManager
├── get_router_model()      # Tier 1, always available
├── get_planner_model()     # Tier 2, ensures loaded, may evict something first
├── get_coder_model()       # Tier 3, ensures loaded, may evict something first
├── unload(model)
├── unload_all_except(model)
├── ensure_available(model) # checks RAM (1b), evicts if needed, loads, waits for ready
└── status()                # which model(s) loaded, RAM used, last-used time per model
```

**Hard architectural invariant, not a guideline:** only one heavy model (Tier 2 or Tier 3) may be actively generating at a time. `ensure_available()` for Tier 2 or Tier 3 always evicts the other heavy tier first if it's loaded. Tier 1 is the exception — small enough to stay resident alongside whichever heavy tier is active.

### 1b. RAM safety contract

`ModelManager.ensure_available()` checks real free memory via `psutil` before loading anything heavier than Tier 1:

```
if free_ram_mb < MIN_FREE_RAM_MB:
    do not load Tier 2/3 — report to the user instead of silently degrading
    ("Low on memory right now — close something and try again" beats a mystery hang)

if Tier 3 requested and Tier 2 is loaded:
    unload Tier 2 → load Tier 3 → run → unload Tier 3
    (the next Tier 2 call reloads it fresh — don't try to restore a specific
    prior state, that's unnecessary complexity for a load that only costs seconds)
```

Start with `MIN_FREE_RAM_MB = 1800` as a placeholder, not a fixed constant — benchmark real headroom on the actual T470s (Section 16 build order) and adjust before treating it as settled.

### 1c. Context budget (kept intentionally simple for v1)

Every LLM call's context is capped, not assembled unbounded. For v1, keep this a simple heuristic rather than a summarization system: cap `num_ctx` per tier (small for Tier 1 routing, larger only for Tier 2/3 when genuinely needed — e.g. many dataframe columns), and truncate conversation history to the last N turns rather than including full history by default. A full `ContextManager` abstraction (deciding what to summarize vs. truncate vs. include) is reasonable v2 scope once you've seen where context actually overflows in practice — don't build it speculatively.

### 1d. Models are configurable, not hardcoded

```json
// config/models.json
{
  "router": {"model": "qwen3:1.7b"},
  "planner": {"model": "qwen3:4b"},
  "coder": {"model": "qwen2.5-coder:7b-instruct-q4_K_M"}
}
```
`ModelManager` reads this rather than having model names baked into code, so swapping in a benchmark-proven better model later (Section 16) never requires a code change.


---

## 2. Routing decision tree

```
User input arrives (typed or transcribed — same entry point, per existing pattern)
       │
       ▼
Is a job already running?
  YES → enqueue this input, announce "Queued — finishing <current task> first", return immediately (UI stays free)
  NO  → continue
       │
       ▼
Tier 0: regex/exact/substring/fuzzy match (same precedence as existing router)
  Matches an ATOMIC single-arg pattern (open app, set volume, explicit-path copy)?
  YES → run inline or as a trivial background job, done in <1s, minimal/no chat noise
  NO  → continue
       │
       ▼
Tier 1 (Qwen3-1.7B): classify + attempt single tool-call extraction
  Resolves to exactly ONE tool call, args unambiguous?
  YES → validate → run as background job (Section 3) → done
  NO (multi-step / ambiguous / low confidence) → escalate to Tier 2
       │
       ▼
Tier 2 (Qwen3-4B): ReAct planning loop, runs as background job
  Each step: does it require code generation (excel pandas, flutter, debug)?
    YES → dispatch to Tier 3 as a sub-call within the same background job
    NO  → execute the tool directly
  Loop continues, logging each step to chat, until planner reports done
```

The escalation decision (Tier 1 → Tier 2) lives in **code**, not in the model's self-assessment — check for multiple clauses/verbs, multiple tool matches, or low-confidence single-tool extraction, and escalate automatically rather than asking Qwen3-1.7B whether it's capable enough.

---

## 3. Threading & non-freezing architecture

This is the most important section. Adapt directly from the proven Nova pattern (`core/jobs.py`, `ui/qt/bridge.py`) and extend it with model-call awareness.

### 3.1 Thread layout

```
GUI thread (PyQt/PySide)
  ├─ never calls Ollama directly
  ├─ never calls subprocess directly
  ├─ never calls exec() directly
  ├─ only: reads input → Assistant.handle(text) → enqueue or run-inline decision
  └─ receives ALL backend output via Qt signals only (never touched directly by a worker thread)

Worker pool (plain threading.Thread / ThreadPoolExecutor — NO Qt import in this module,
             mirroring Nova's core/jobs.py so the backbone stays unit-testable headless)
  ├─ pulls one job at a time off a PriorityQueue
  ├─ for Tier 1/2/3 calls: makes the Ollama HTTP request from this thread, blocking
  │   is fine HERE because it's off the GUI thread entirely
  ├─ for tool execution (file ops, excel, exec() of generated code): runs here too
  ├─ emits progress via a bridge object at every meaningful step (see 3.2)
  └─ on completion or error, emits a final result

Bridge (the ONLY place worker-thread and GUI-thread code touch)
  ├─ mirrors Nova's AssistantBridge / ListenerBridge pattern — pyqtSignal-based,
  │   NEVER QTimer.singleShot from a non-Qt thread (this was Nova's exact voice-input
  │   bug — a plain threading.Thread has no event loop to pump the timer, so it silently
  │   never fires; a real signal always delivers regardless of the calling thread)
  ├─ progress(text) → appends a line to chat log, live, as it happens
  ├─ announce(text, speak=True) → same as progress but also routes to TTS if enabled
  └─ result(payload) → final answer, unblocks the "ready for next input" state
```

### 3.2 What gets logged, live, per tier

**Tier 0:** essentially nothing — it's instant. Optionally a single confirmation line after execution ("Opened Chrome").

**Tier 1:** one line when the model is called ("Understanding that..."), one line with the resolved tool + args before execution ("→ file_copy: Downloads/report.csv → D:/Reports/"), one line on result. If it escalates to Tier 2, say so explicitly ("This needs multiple steps — planning...") so the user isn't left guessing why it's taking longer than a Tier 1 call normally would.

**Tier 2 (this is where most of the visible waiting happens):** log every iteration of the ReAct loop as it happens:
```
Planning: make June report and email Nike
  → Step 1: recall("nike_email")  ...  boss@nike.com
  → Step 2: this step needs code — calling coder model (Tier 3)...
       [Tier 3 sub-log, see below]
  → Step 3: excel_write — saved 4 sheets to D:/Reports/June.xlsx
  → Step 4: excel_email → boss@nike.com  ...  sent
  Done. Report generated and emailed.
```
Each `→ Step N` line is emitted the moment that step *starts*, not after it finishes, so a slow step is visibly "in progress" rather than looking stalled. If a step is expected to take more than ~3 seconds (e.g. a coder-model call), emit an explicit "...still working on this" heartbeat line every few seconds rather than going silent — silence reads as "frozen" even when it isn't, and this is exactly the class of bug the existing Nova build already fixed once for its gesture/mute system (a channel with no active listener fails silently); don't reintroduce that failure mode here.

**Tier 3 (nested inside Tier 2's log, indented or visually distinguished):**
```
     Calling coder model (Qwen2.5-Coder)...
     Generating pandas code for: <2% sheet, >5hrs pending, district-wise, tehsil-wise
     Code generated (14 lines). Validating...
     Executing...
     4 sheets created: raw_data, under_2pct, pending_5hr, district_summary
```
Log the model swap explicitly ("Loading coder model — this takes a few seconds") since that's real, visible latency the user would otherwise misattribute to a hang.

### 3.3 Job states and the single-active-job rule

```python
# conceptual shape, mirror Nova's jobs.py structure
class JobState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

# GUI-thread-visible state, updated only via bridge signals
current_job: Job | None
queue: PriorityQueue[Job]
```

- Only one `Job` is ever `RUNNING` at a time.
- New input while a job is `RUNNING` → wrapped as a new `Job`, pushed to `queue`, bridge emits an immediate chat line: `"Queued — I'll start this once <current job description> finishes."` This must return to the GUI instantly; queuing is O(1) and never blocks.
- When the running job finishes (`DONE` or `FAILED`), the worker pool pulls the next `QUEUED` job automatically and the bridge emits `"Starting: <next job description>"`.
- Priority still matters the way Nova already does it — a trivial Tier 0/1 lookup that somehow arrives mid-Tier-2-job should still be able to jump the queue ahead of, say, a second heavy report request, so use the same priority-int-on-the-queue-item approach already proven in `core/jobs.py`.

### 3.4 Failure isolation

- A worker thread crash must **never** take down the GUI thread or the app. Wrap the top of every job's execution in try/except, emit a `FAILED` result with the actual error message to chat (not a silent swallow — the user needs to know *why* something didn't finish), and return the worker to the pool for the next job.
- If Tier 3 (coder model) generates code that fails validation or throws on `exec()`, do not silently retry forever — log the failure, optionally let Tier 2 attempt one re-plan/re-generation with the error fed back as context (this is the same "planner gets the error and tries again" rule from the original tool spec's error-handling section), and give up with a clear chat message after at most 1 retry, rather than looping invisibly.

---

## 4. Excel report pipeline (data reshaping + formatting, kept separate)

This directly answers the "raw data → 4 sheets with proper formatting matching an existing report" requirement, and is a concrete instance of the Tier 2 → Tier 3 flow above.

**Two genuinely different problems, two different mechanisms:**

1. **Data reshaping (filtering/grouping/pivoting into sheets)** — always dynamic, always goes through Tier 3. Never write a hardcoded function per report type. At request time, Tier 2 hands the coder model the raw dataframe's actual `df.columns` plus the user's plain-English instructions; the coder model writes fresh pandas code for *that specific request* (`df1 = df[df.pct < 2]`, `df.groupby('district').agg(...)`, etc.); Tier 2 validates and `exec()`s it, producing `df1..dfN`; a dumb tool (`excel_write`) saves them as sheets.

2. **Formatting (colors, fonts, layout matching an existing report)** — this should mostly **not** involve an LLM at all, since it's deterministic. Two Tier 0 dumb tools:
   - `extract_style(reference_report_path)` — opens an existing report with openpyxl, pulls header color, font, borders, column widths into a JSON style spec. Cache this per client/report-type in config (same `settings.json`/`templates` pattern already used elsewhere) so it's extracted once and reused, not re-extracted on every run.
   - `apply_style(output_path, style_spec)` — writes that spec onto the new sheets after data reshaping is done.
   The only LLM involvement on this side is a trivial Tier 1 lookup — matching "format it like the Nike report" to the right cached style spec.

**Full pipeline for a request like "make report from penalties.csv, 4 sheets: raw, <2%, >5hrs pending, district-wise, tehsil-wise, format like last report":**

```
Tier 2 (Qwen3-4B) plans, logging each step live to chat:
  1. extract_style(reference) [Tier 0, dumb, instant — cached after first run]
  2. Tier 3 call: generate pandas code for the 4 sheets from instructions + df.columns
  3. exec() the generated code → df1..df4
  4. excel_write() → saves all sheets
  5. apply_style() → applies cached formatting
  6. notify "Report done: D:/Reports/Penalties_Aug9.xlsx"
```

---

## 5. Complete tool registry (nothing dropped — every tool tagged with its tier)

All tools below are carried forward from the original 32/34-tool spec. **Default tier is 0 (dumb, direct call, no LLM) unless otherwise marked.** SMART tools are the ones with LLM code-gen already flagged in the original spec — those are Tier 2 (orchestration) dispatching to Tier 3 (actual code generation) as described in Section 4.

### Group A — OS & PC Control (8 tools, all Tier 0)
`open_app`, `close_app`, `set_volume`, `screenshot`, `ocr_screenshot`, `system_info`, `run_command` (allowlisted), `notify`
— All atomic, single-arg, deterministic. Tier 0 regex dispatch handles these directly. `ocr_screenshot` may need Tier 1 if the user's phrasing of "what does it say" needs interpretation, but the OCR call itself is still a dumb tool.

### Group B — File & Directory Management (7 tools)
`file_list`, `file_read`, `file_write`, `file_copy`, `file_move`, `file_rename`, `file_search`
— Tier 0 when paths are explicit and the command is single-step. Tier 1 when paths need inference ("that file I just made", "the one I downloaded"). Tier 2 when the command is multi-step ("copy this file there and rename it," "find and archive everything older than 30 days") — Tier 2 composes these same functions into a multi-call plan; the functions themselves are never rewritten per combination.

### Group C — Excel & Reports (6 tools, 2 dumb + 4 smart per original spec)
- `excel_read(path, sheet)` — Tier 0, dumb
- `excel_write(path, df, sheet)` — Tier 0, dumb
- `excel_create_report(raw_path, output_path, instructions)` — **Tier 2 orchestrates, Tier 3 generates the pandas code** (Section 4, step 2)
- `excel_add_chart(path, chart_type, data_range)` — Tier 0, dumb (openpyxl, deterministic once the target range is known)
- `excel_summarize(path)` — **Tier 2/3**: LLM reads the report and extracts insights ("revenue up 12%")
- `excel_email(path, to, subject, body)` — Tier 0, dumb (smtplib)
- **New in v2.0** (Section 4): `extract_style(reference_path)` and `apply_style(output_path, style_spec)` — both Tier 0, dumb, deterministic openpyxl reads/writes

### Group D — Web, Search & Social (6 tools, 2 dumb + 4 smart per original spec)
- `web_search(query)` — Tier 1 (single call, needs light interpretation of intent but not planning)
- `web_scrape(url)` — Tier 0, dumb once the URL is known
- `youtube_search(query)` — Tier 1
- `youtube_summarize(video_url)` — Tier 2/3: needs the LLM to actually summarize a transcript
- `wordpress_post(client, title, content)` — Tier 2: drafting tone/structure needs planning-level judgment, not just tool-call extraction
- `linkedin_post(text)` — Tier 2, same reasoning as wordpress_post
- **New in v2.0** (Section 9.2): `weather(location)` — Tier 0, dumb, plain API fetch, no ambiguity to resolve

### Group E — Dev & Flutter (3 tools, all smart per original spec)
- `flutter_generate(prompt)` — **Tier 3 directly**, invoked by Tier 2 as a sub-call, same pattern as excel's coder dispatch
- `flutter_fix(error_log)` — **Tier 3 directly**, same pattern
- `git_action(action, message)` — Tier 0, dumb (deterministic subprocess call — commit/push/pull don't need a model at all; keep this off Tier 3 even though it's dev-adjacent)

### Group F — Memory, Life & Learning (4 tools, 2 dumb + 2 smart per original spec)
- `remember(key, value)` — Tier 0, dumb (writes to settings.json, same pattern as Nova's config store)
- `recall(key)` — Tier 0, dumb
- `add_reminder(text, time)` — Tier 1 (needs light parsing of natural-language time expressions — reuse Nova's existing reminder-phrase parser rather than routing this through a model at all if that parser is already solid)
- `context_tip()` — Tier 1, trivial lookup against current app + a small rule set, not full generation

---

## 6. Logging system (backend + chat, both required)

Two destinations, always both written:

1. **Chat log (user-facing, live)** — every `progress()`/`announce()` call from Section 3.2, in plain language, no stack traces, no internal tier/model names unless the user asks ("what model did you use").
2. **File log (`logs/`, developer-facing, for debugging)** — every tool call, every model call (which tier, which model, latency), every validation pass/fail, every escalation decision (Tier 1 → Tier 2, with the reason), every error with full traceback. Structured enough to answer, after a week of use, "where is this system actually slow or wrong" — this is how tier-escalation thresholds and model choice get tuned later, per the original spec's `logs/` intent.

Every dispatch outcome gets one line minimum in the file log, mirroring Nova's existing `Dispatching '...' -> '<command>'` / `No command matched: '...'` pattern — extend it with `Tier: 1|2|3`, `Model: <name>`, `Latency: Xs`.

---

## 8. Speech output rules — what gets spoken vs. what stays chat-only

**The problem being fixed:** a naive TTS integration reads everything verbatim — full URLs, full file paths, raw markdown, code blocks, error stack traces — none of which anyone would actually say out loud. This section is a single chokepoint function, `_for_speech(text)`, that every spoken reply passes through before hitting the TTS engine. The chat log always keeps the full, untouched text; only the *spoken* version is transformed. Never the reverse — never strip detail from what's shown in chat for the sake of speech.

### 8.1 Always shorten/transform before speaking

| In chat (unchanged) | Spoken as |
|---|---|
| `https://www.youtube.com/watch?v=abc123` | "youtube.com" |
| `D:/Office/Reports/7 August/Pending_Penalties.xlsx` | "Pending Penalties, in today's reports folder" (filename + folder context, never the full path) |
| A raw code block | "I've written the code — check the chat" (never read code aloud, ever, regardless of length) |
| A full stack trace / exception | One-sentence plain-language summary of what went wrong ("The report script hit an error reading the CSV — full details are in the chat") |
| `**bold**`, `` `code` ``, `# headers`, `- bullets` | Markdown symbols stripped entirely; structure conveyed through pauses/pacing, not spoken symbols |
| A long file hash / ID / API key / UUID | Never spoken in full — referred to generically ("the file" / "that ID") or by a short human-friendly name if one exists |
| Numbers/currency/percentages | Spoken naturally ("twelve percent", "eighty-five thousand rupees") — rely on the TTS engine's own normalization (edge-tts handles this natively); don't hand it raw symbols like `%` or `Rs.` if the engine mishandles them |

### 8.2 List detection (carry forward from the existing Nova pattern — this already works, keep it)

Three or more consecutive lines matching a bullet/number pattern (`- `, `* `, `1.`, `2)`, …) = a listing, not a short reply meant to be read item-by-item. Only the first non-list line (the header/question) is spoken, followed by "Check the chat for the full list." Two list-shaped lines still read in full — the threshold exists specifically so an ordinary two-item answer isn't over-triggered into a summary.

### 8.3 Length and pacing

- Every mode has a token cap appropriate to its purpose (see Section 9) — a quick banter reply should never run long, a drafting request can.
- The system prompt for every mode includes an explicit brevity instruction as a default assumption ("two or three sentences unless asked for more") — this is a *default*, not a hard ceiling; if the user explicitly asks for a full draft or detailed explanation, honor that and skip the cap.
- A hard character-count safety valve (independent of the model's own token cap) cuts any reply at a sentence boundary before speech, never mid-word/mid-sentence.

### 8.4 What NEVER gets spoken, full stop, regardless of mode or request

- Raw code, in any language, any length.
- Full file paths (speak filename + human context instead — "the June report" not "D colon slash Office slash Reports slash June dot xlsx").
- Full URLs (domain only, per 8.1).
- Passwords, API keys, tokens, or anything from a `settings.json` marked sensitive.
- Full stack traces / raw exception text.

### 8.5 Interruptibility

Speech must be mutable mid-sentence (mute gesture/hotkey/command stops it within milliseconds, not at the next sentence boundary) — reuse the non-blocking play-and-poll pattern already proven in the existing build rather than a blocking "play and wait" call, since cross-thread interruption of a blocking audio call is not reliable and produces the "stuck audio" failure class that was already fixed once.

---

## 9. Personas / modes — this is a buddy, not just a task-runner

The assistant needs to support genuine register-switching: same voice/pipeline, different *personality*, selected explicitly by the user ("fun mode", "office mode") and remembered until changed. Reuse the proven lesson from the existing build: **don't inject the user's full profile/biography into every mode.** A small model handed a biography in a casual mode recites it back or starts interviewing the user about it instead of just being a friend — profile context goes only into modes where it's functionally useful (work, formatting, professional writing), never into the purely social modes.

### 9.1 Mode list — consolidated

Rethinking this from scratch rather than just carrying forward every mode from the earlier draft: `fun`, `tease`, and a separate `ragebait`-style mode are really the same energy wearing different names, and splitting them just adds mode-switching friction for no real gain. Collapsed into one mode that does all of it well:

| Mode | Purpose | Profile injected? | Energy | Token cap |
|---|---|---|---|---|
| `buddy` (default) | Everyday hangout — the baseline personality, casual and quick-witted | No | High — playful, not flat or corporate | Medium |
| `roast` | Savage banter, uncensored jabs, meme-speak, comebacks — the "friend who talks smack" mode | No | Max — fast, punchy, unfiltered | Short (quick hits, not essays) |
| `office` | Excel, reports, professional drafting | Yes | Calm, focused | Medium |
| `flutter` / `dev` | Coding-adjacent Q&A (code generation itself is Tier 3, not this) | Yes | Calm, focused | Medium |
| `wordpress` | Sites, hosting, portfolio work | Yes | Calm, focused | Medium |
| `writer` | General drafting help | Yes | Measured | Medium-high |
| `study` | Patient tutor — explains, then quizzes | Yes | Warm, patient | Medium |
| `motivation` | Direct accountability, one concrete next action | Light context (9.3) | High, firm — a push, not a lecture | Short |
| `islamic` | Twelver Shia (Ja'fari) perspective, with references | Yes | Calm, respectful | Medium |
| `podcast` | Long-form two-way conversation | No | Relaxed, conversational | Long |

Mode names fuzzy-match, per the existing router precedence, and switch mid-conversation on a single phrase.

### 9.1a Casual modes are energetic, not "calm and measured"

For `buddy` and `roast` specifically — these should read as genuinely hyped, playful, quick-witted; not a toned-down, careful version of the assistant. Short punchy sentences, real slang, actual enthusiasm, willing to talk trash. This is the opposite instruction from the work modes on purpose — `office`/`dev`/`writer` stay measured because that's what the task needs; `buddy`/`roast` should feel like texting an actual hyped-up friend, not a slightly looser customer support bot.

### 9.2 What a proper assistant does *without being asked* — baseline behaviors

These aren't a mode, they're always-on assistant hygiene:

- **Time-of-day greeting.** On first interaction of the day / on wake from idle, a natural greeting appropriate to the hour ("Morning — here's what's up" vs a plain "hey" at 2am), not the identical canned line every time.
- **Weather, on request and optionally in the morning greeting.** A dedicated tool — `weather(location)` — Tier 0/1, hits a free weather API, returns current conditions + a short forecast. Speak it briefly ("Mid-20s and clear today"), keep full detail in chat. This is a genuinely missing tool from the original registry — add it to Group D as a dumb/Tier-0 call (a plain API fetch has no ambiguity for a model to resolve).
- **Small talk without being clinical.** "How's it going" gets a real, brief, warm reply — not a feature list, not "I am an AI assistant, how may I help you." This is the `buddy`/`default` mode's whole job.
- **Reminders are proactive, not just reactive.** Beyond the existing `add_reminder`/`recall` tools (Group F), firing a reminder should feel the same as the assistant initiating a thought: a chat message + system notification + spoken line, in the persona currently active (don't switch tone mid-reminder), respecting whatever quiet-hours setting exists.
- **Motivation isn't just a mode you enter — it can be a check-in.** If the user has explicitly opted into occasional proactive nudges, this is a rate-limited, config-driven thing (e.g. once a day at most, only during set hours) — never unsolicited spam, always toggleable off entirely. On-demand ("give me a push") always works regardless of that setting.
- **Never robotic acknowledgment of the obvious.** Avoid restating what the user just said back at them before answering — get to the actual response.

### 9.3 Motivation mode specifics

Unlike the purely social modes, motivation mode benefits from *light* context — knowing roughly what the user is working on right now (current app, last task from Tier 2 job history) so the "one concrete next action" is actually relevant rather than generic. This is closer to `context_tip()` (Group F, Tier 1) than to a full biography injection — pull from `memory.json`'s live state (current_app, last task), not from a static personal-profile block.

---

## 11. Assistant identity & voice — it has a personality, not just a personality *setting*

**Give it a name.** A buddy doesn't refer to itself as "the assistant" — pick a name (or let the user pick one on first run: "what do you want to call me?") and use it consistently in self-reference, wake confirmations, and how it signs off proactive messages ("— Nova" style, if reusing the existing project's working name, or whatever the user chooses).

### 11.1 Multiple voices, mapped to mode

edge-tts ships a real range of natural voices (multiple English + Urdu options, male/female, different energy levels) — use more than one, not a single flat voice for every context:

| Mode | Voice character |
|---|---|
| `office` / `writer` / `study` / `flutter`/`dev` / `wordpress` | Calm, measured, professional |
| `buddy` | Warm but energetic — playful, not flat |
| `roast` | Fast pace, sharp inflection, max energy — the loudest voice in the lineup |
| `motivation` | Firm, direct, hyped — not soft |
| `islamic` | Calm, measured, respectful pacing — same register as recitative content deserves |

Store the voice-per-mode mapping in `personas.json` alongside the existing mode config, so it's hand-editable the same way everything else is. Switching mode switches voice immediately, not just tone of text.

### 11.2 Voice is switchable directly too

`voice to <name>` / `list voices` as standalone commands (Tier 0/1), independent of mode — so the user can override the default for a session without changing mode ("stay in office mode but use a different voice").

---

## 12. Personal data profile — feeding in who the user actually is

The user will supply full personal/professional data; below is the profile scraped from their portfolio (`codegenesis1st.com`) as a concrete starting point for the schema and a real seed file. Store as `config/profile.json`, following the same split-by-domain philosophy as the rest of config — this is data, not code, and hand-editable exactly like everything else.

```json
{
  "identity": {
    "name": "Zuraiz Nayyar",
    "title": "Flutter Developer / Mobile App Engineer",
    "location": "Islamabad, Pakistan",
    "experience_years": "2+",
    "specialty": "Scalable App Solutions",
    "email": "zuraiz@codegenesis1st.com",
    "phone": "+92 344 2607654",
    "portfolio_url": "https://codegenesis1st.com",
    "linkedin": "https://pk.linkedin.com/in/malik-zuraiz-nayyar",
    "github": "https://github.com/malikzuraiz",
    "whatsapp": "https://wa.me/923442607654"
  },
  "current_role": {
    "title": "Flutter Backend Developer",
    "company": "Duseca Software",
    "since": "March 2025",
    "focus": "Flutter with backend integrations — Firebase, Supabase, REST APIs, Google Maps, scalable architecture, real-time data handling"
  },
  "experience_history": [
    {"role": "Flutter Backend Developer", "company": "Duseca Software", "period": "Mar 2025 – Present"},
    {"role": "Flutter Developer", "company": "Mkaits Technologies", "period": "Oct 2024 – Mar 2025", "note": "GetX state management, cross-platform UI/UX"},
    {"role": "WordPress Developer", "company": "Digital Marvels Pvt. Ltd", "period": "May 2024 – Nov 2024", "note": "5+ client sites — backups, updates, performance"},
    {"role": "Flutter Developer Intern", "company": "EvesBytes", "period": "Apr 2024 – May 2024"},
    {"role": "Flutter Developer Intern", "company": "Diginammo", "period": "Aug 2023 – Nov 2023", "note": "MVC design, modular architecture"},
    {"role": "WordPress Intern", "company": "Digital Marvels Pvt. Ltd", "period": "Jan 2023 – Feb 2023"}
  ],
  "education": {
    "degree": "BSIT, Bahria University E-8 Campus, Islamabad",
    "period": "2020 – 2024",
    "cgpa": "3.06",
    "recognition": "2nd Best Final Year Project — 'Social Fit', a Flutter fitness app"
  },
  "skills": {
    "core": ["Flutter & Dart", "WordPress Development", "GetX State Management", "MVC Architecture", "UI/UX Optimization", "REST APIs Integration", "Supabase", "Firebase / Firestore"],
    "languages_programming": ["Dart", "Python"],
    "languages_spoken": ["English", "Urdu"]
  },
  "notable_projects": ["Finnora", "Luxora Store", "Chatty", "Social Fit"],
  "awards": [
    "Certificate of Appreciation — Duseca Software, 210% of monthly target (2026)",
    "Speaker — Flutter Cross Platform Development Webinar, Riphah AICP (2025)",
    "2nd Best Final Year Project — Bahria University (2024)",
    "1st Position — Project Gala, 'Friendzone' (2023)"
  ],
  "religion_context": "Twelver Shia (Ja'fari)"
}
```

**Injection rule (carries the existing lesson forward — don't relitigate it per mode):** this profile is available to `office`, `flutter`/`dev`, `wordpress`, `writer`, `study`, `islamic`, and `motivation` (per Section 9.3's light-context rule) — never to `fun`, `tease`, `buddy`/`default`, or `podcast`. The user asked for this specifically because a small model handed a full biography in a casual mode starts reciting it back instead of just talking. Use it for context, not as material to perform.

Add a wizard step (or a direct chat command, `update my profile`) so the user can append/edit this over time the same way they'd edit any other config file — this doc's seed data won't stay current on its own.

---

## 13. Roast mode, in detail

Built genuinely uncensored: no corporate softening, no hedging, no walking a joke back mid-sentence, no disclaimers. This mode's system prompt should say plainly — this is a personal buddy roasting its own user, for content, by request; be sharp, be harsh, don't sanitize it. Meme-speak, slang, exaggeration, savage comebacks — full send.

Scope note (technical, not moral): this mode is scoped to banter *with the user, about the user* — habits, choices, code quality, whatever they throw at it. It's not a general-purpose switch for generating targeted content about real third parties outside this context. That's a boundary about what the mode is *for*, not a leash on how harsh it gets within that lane.

Keep the token cap short (per 9.1) — roast mode works in quick hits and comebacks, not paragraphs.

---

## 14. Greetings, liveliness, and small touches that make it feel alive

### 14.1 Greetings — contextual, not canned

- **Time-of-day base greeting**, varied phrasing (not the literal same string every morning).
- **Context-aware layer on top**, when there's something to reference: a pending reminder ("morning — you've got the client call at 2"), yesterday's unfinished task, a weekday-vs-weekend tone shift, or referencing current_role context in work-mode greetings ("ready to look at the Duseca stuff?").
- **"Welcome back" after real idle time** (hours, not minutes) vs. a normal reply after a short gap — don't re-greet on every message.

### 14.2 Liveliness beyond greetings

- **Idle presence, not just reactive replies.** The floating widget's existing idle animation (per the original spec) can pair with an occasional, rate-limited ambient one-liner during long idle stretches — off by default or heavily throttled, fully toggleable, never intrusive.
- **Light acknowledgment of streaks/patterns**, sourced from the file logs already being kept (Section 6) — "third report today, you're on a roll" — genuine, based on real activity, not fabricated.
- **Easter eggs** on specific phrases (a couple of fun hardcoded responses to things like "who's the best" or "tell me a secret") — small, low-effort, high-charm additions that cost nothing to build.
- **Mood-adjacent HUD styling** (optional, cosmetic only): a subtle accent-color shift per active mode (calm blue for office, warmer tone for fun/tease) so the visual state matches the personality state at a glance — purely visual, doesn't touch any logic.

---

## 15. Additional capability tools (within the existing bounds — free/local only, no paid APIs, no Rainmeter coupling, no continuous cursor control)

New tools worth adding to the registry, all consistent with the no-paid-API / free-local-only constraint from the original project brief:

| Tool | Tier | Notes |
|---|---|---|
| `translate(text, to_lang)` | Tier 1 | Urdu ⇄ English, useful given the bilingual context already in the profile |
| `news_headlines(topic?)` | Tier 1/2 | Free RSS/search-based headline summary, not a paid news API |
| `convert_units(value, from, to)` | Tier 0 | Currency (via a free-tier rate source), length, weight — deterministic once the rate/factor is fetched |
| `quick_note(text)` | Tier 0 | Fast voice/typed memo capture, separate from full reminders — for "jot this down" moments |
| `github_activity(username)` | Tier 1 | Public GitHub API, no key needed — commit streak / recent activity for the user's own profile |
| `open_contact(name, via)` | Tier 0 | WhatsApp/email quick-open using the profile's own contact info — reuses `personal_links.json`-style config |
| `daily_digest()` | Tier 1/2 | End-of-day summary pulled from the file log (Section 6) — what ran, what got done, any failures — read back or shown in chat on request ("what did we do today") |

Each of these follows the same rule as everything else in this doc: dumb where the task is deterministic, Tier 1 where it needs light interpretation, and never given more model than the task actually requires.

---

## 16. Build order

1. **Threading backbone first** (Section 3) — worker pool, priority queue, bridge/signals, job states, queuing-while-running behavior. Get this right before wiring in any model calls; everything else depends on it and it's the exact thing that determines whether the "never freeze" requirement actually holds. Reuse Nova's `core/jobs.py` + `ui/qt/bridge.py` pattern directly rather than reinventing it.
2. **Tier 0 regex dispatcher** — wire in Group A/B atomic commands, confirm instant execution with zero chat noise beyond a single confirmation line.
3. **Tier 1 (Qwen3-1.7B)** — single-tool-call routing + validation layer, with live "Understanding..." / "→ tool: args" / result logging per Section 3.2.
4. **Escalation logic** — deliberately test multi-step commands to confirm they escalate rather than getting mis-handled by Tier 1.
5. **Tier 2 (Qwen3-4B) ReAct loop** — start with 2-step commands, build up to the Nike-report 4-step example, confirming live step-by-step chat logging the whole way, plus the "queued" behavior when a second command arrives mid-run.
6. **Tier 3 (Qwen2.5-Coder) swap-in** — wire into `excel_create_report` first (highest daily value), confirming the model-swap latency is logged explicitly rather than appearing as a stall. Expand to `flutter_generate`/`flutter_fix` once stable.
7. **Excel formatting pipeline** (`extract_style`/`apply_style`) — build and cache-test against a real reference report.
8. **File logging** — structured logs across all tiers, confirm a full session's worth of decisions is reconstructable from the log alone.
9. **Hardware validation on the actual T470s** — time real Tier 1/2/3 calls, confirm RAM stays under budget with Tier 1+2 resident and Tier 3 swapping in/out, tune the "still working on this" heartbeat interval based on real observed latency rather than the estimates in this doc.