"""Commands about the assistant itself: help, refresh, config, workspace.

``refresh`` is the important one. The project brief calls it a core
reliability requirement, not a nice-to-have: after hand-editing any JSON
file, one word must make the change live - reread every file, rebuild every
config-driven command, and say plainly what changed. If that is ever
unreliable, hand-editing config stops being trustworthy and the whole
config-driven design falls over.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from assistant.core.router import CommandRouter, Reply
from assistant.store.store import ConfigStore
from assistant.workspace.daybook import format_day
from assistant.workspace.scaffold import ensure_workspace


def register(router: CommandRouter, store: ConfigStore) -> None:
    @router.register(
        "help",
        keywords=("help", "what can you do", "commands", "list commands", "show commands"),
        help="Lists everything I can do, grouped by area.",
        category="assistant",
        instant=True,
    )
    def cmd_help(text, ctx):
        match = re.search(r"help\s+(?:with\s+)?(.+?)\s*$", text.strip(), re.IGNORECASE)
        category = match.group(1).strip() if match else None
        # Spoken like everything else; the length cap in TextToSpeech stops a
        # 200-line listing from occupying the speaker and says the rest is in
        # the chat.
        return Reply(ctx.router.help_text(category))

    @router.register(
        "refresh config",
        keywords=(
            "refresh", "reread config", "reload settings", "reload config",
            "reread settings", "refresh config", "pick up my changes",
        ),
        help="Rereads every config/*.json from disk and rebuilds my commands.",
        category="assistant",
        instant=True,
    )
    def cmd_refresh(text, ctx):
        report = ctx.store.refresh()
        count = ctx.router.rebuild()
        line = report.summary()
        return Reply(f"{line}\n{count} config-driven command(s) rebuilt - {len(ctx.router)} total.")

    @router.register(
        "save this directory",
        keywords=(
            "save this directory", "save this folder", "remember this directory",
            "remember this folder", "save this path", "bookmark this folder",
        ),
        pattern=r"^\s*(?:save|remember)\s+(?:this\s+)?(?:directory|folder|path)(?:\s+(.+))?\s*$",
        help="save this directory D:/scripts/reportgen  -  the 'add command' wizard reuses it.",
        category="assistant",
    )
    def cmd_save_directory(text, ctx):
        match = re.search(r"(?:directory|folder|path)\s+(.+?)\s*$", text, re.IGNORECASE)
        if match:
            candidate = Path(match.group(1).strip().strip("\"'")).expanduser()
            if candidate.is_file():
                candidate = candidate.parent
            if not candidate.is_dir():
                return f"I can't see a folder at {candidate}."
            ctx.store.set_value("core", "last_active_path", candidate.as_posix())
            return f"Saved {candidate}. Say 'add command' and I'll offer it as the default."

        current = ctx.store.value("core", "last_active_path")
        if current:
            return (
                f"Currently saved: {current}\n"
                "To change it: 'save this directory <path>'."
            )
        return "Tell me which one: 'save this directory D:/scripts/reportgen'."

    @router.register(
        "setup workspace",
        keywords=("setup workspace", "set up workspace", "create my folders", "build workspace", "make my folders"),
        help="Creates any missing Entertainment/Office folders. Never moves or deletes anything.",
        category="assistant",
    )
    def cmd_setup_workspace(text, ctx):
        return ensure_workspace(ctx.store).summary()

    @router.register(
        "open config folder",
        keywords=("open config", "open config folder", "edit config", "show config folder", "where is your config"),
        help="Opens the folder holding all the JSON config files.",
        category="assistant",
    )
    def cmd_open_config(text, ctx):
        os.startfile(str(ctx.store.dir))  # noqa: S606
        return f"Opened {ctx.store.dir}. Edit anything in there, then say 'refresh'."

    @router.register(
        "edit config file",
        pattern=r"^\s*(?:edit|open)\s+(core|projects|scripts|reports|websites|wordpress|personal_links|personal links|reminders|media|workspaces)(?:\.json)?\s*(?:config)?\s*$",
        help="edit websites  -  opens that config file in your editor.",
        category="assistant",
    )
    def cmd_edit_config(text, ctx):
        name = re.search(
            r"(core|projects|scripts|reports|websites|wordpress|personal_links|personal links|reminders|media|workspaces)",
            text,
            re.IGNORECASE,
        ).group(1).lower().replace(" ", "_")
        path = ctx.store.path(name)
        if not path.exists():
            return f"{path} doesn't exist yet."
        os.startfile(str(path))  # noqa: S606
        return f"Opened {path.name}. Say 'refresh' when you've saved your changes."

    @router.register(
        "status",
        keywords=("status", "what's running", "whats running", "are you busy", "job status"),
        help="Shows what I'm working on and how much I know about.",
        category="assistant",
        instant=True,
    )
    def cmd_status(text, ctx):
        store = ctx.store
        root = store.value("reports", "output_root") or store.value("core", "defaults.reports_root")
        today = format_day(fmt=store.value("core", "day_folder.format", "{d} {month}"))
        lines = [
            f"{len(ctx.router)} commands loaded ({len(ctx.router.by_category())} areas).",
            f"{len(store.items('reports', 'reports'))} report generator(s), "
            f"{len(store.items('projects', 'projects'))} project(s), "
            f"{len(store.items('scripts', 'commands'))} custom command(s).",
            f"Reports file into {root}/{today}.",
            f"Config: {store.dir}",
        ]
        pending = getattr(ctx, "jobs", None)
        if pending is not None:
            lines.append(f"{pending.pending} job(s) queued.")
        if ctx.conversation.active:
            lines.append(f"Mid-way through the '{ctx.conversation.name}' wizard.")
        return "\n".join(lines)

    @router.register(
        "cancel",
        keywords=("cancel", "never mind", "nevermind", "stop that", "forget it"),
        help="Cancels whatever multi-step flow is in progress.",
        category="assistant",
        instant=True,
    )
    def cmd_cancel(text, ctx):
        return ctx.conversation.cancel()

    @router.register(
        "start with windows",
        keywords=(
            "start with windows", "launch at startup", "run at startup",
            "enable autostart", "start on boot", "auto start",
        ),
        help="Adds a Startup shortcut so I launch (hidden in the tray) at login.",
        category="assistant",
    )
    def cmd_autostart_on(text, ctx):
        from assistant.automation import autostart

        if re.search(r"\b(don't|dont|stop|disable|remove|no longer)\b", text, re.IGNORECASE):
            return autostart.disable()
        return autostart.enable()

    @router.register(
        "stop starting with windows",
        keywords=("don't start with windows", "disable autostart", "remove from startup", "stop starting with windows"),
        help="Removes the Startup shortcut.",
        category="assistant",
    )
    def cmd_autostart_off(text, ctx):
        from assistant.automation import autostart

        return autostart.disable()

    # NOTE: "ask"/"nova <prompt>" live in assistant/commands/chat.py, which
    # owns everything that reaches the local model. Keeping a second entry
    # point here made "chat status" route into the model instead of the
    # diagnostics command.

    @router.register(
        "live listening",
        keywords=(
            "live listening", "always listen", "start listening", "listen continuously",
            "hands free", "hands free mode", "keep listening",
        ),
        help="Listens continuously so you can talk without touching a hotkey.",
        category="voice",
        instant=True,
    )
    def cmd_live_on(text, ctx):
        control = getattr(ctx, "listener_control", None)
        if control is None:
            return "The microphone isn't available in this mode (try the main window)."
        if re.search(r"\b(stop|off|disable|end|quit)\b", text, re.IGNORECASE):
            return control("stop")
        return control("start")

    @router.register(
        "stop listening",
        keywords=("stop listening", "stop live listening", "turn off listening", "hands free off"),
        help="Turns continuous listening back off.",
        category="voice",
        instant=True,
    )
    def cmd_live_off(text, ctx):
        control = getattr(ctx, "listener_control", None)
        if control is None:
            return "The microphone isn't available in this mode."
        return control("stop")

    @router.register(
        "set wake word",
        pattern=r"^\s*(?:set\s+)?wake\s*word\s+(?:to\s+)?(.+?)\s*$",
        help="wake word to nova  -  in live mode I only act on speech containing it ('none' to clear).",
        category="voice",
        instant=True,
    )
    def cmd_wake_word(text, ctx):
        word = re.search(r"wake\s*word\s+(?:to\s+)?(.+?)\s*$", text, re.IGNORECASE).group(1).strip().lower()
        if word in {"none", "off", "nothing", "clear"}:
            ctx.store.set_value("core", "voice.live_wake_word", "")
            return "Wake word cleared - in live mode I'll act on everything I hear."
        ctx.store.set_value("core", "voice.live_wake_word", word)
        return (
            f"Wake word set to '{word}'. In live mode I'll ignore anything without it.\n"
            "Restart live listening for this to take effect."
        )

    @router.register(
        "hotkeys",
        keywords=("hotkeys", "shortcuts", "what are the hotkeys", "keyboard shortcuts"),
        help="Lists the global keyboard shortcuts.",
        category="voice",
        instant=True,
    )
    def cmd_hotkeys(text, ctx):
        return (
            "Global shortcuts (work anywhere in Windows):\n"
            "  Ctrl+Alt+Space    show / hide this window\n"
            "  Ctrl+Alt+H        hide it\n"
            "  Ctrl+Shift+Space  hold to talk (release to send)\n"
            "  Esc               hide the window when it's focused\n"
            "  Up / Down         previous commands in the input box\n\n"
            "Prefer not to use a key at all? Say 'live listening' and just talk."
        )

    @router.register(
        "show terminals",
        keywords=(
            "show terminals", "show the terminal", "show consoles",
            "hide terminals", "hide the terminal", "run scripts quietly",
        ),
        help="Whether scripts and reports run in a visible terminal window.",
        category="assistant",
        instant=True,
    )
    def cmd_show_terminals(text, ctx):
        wants = not re.search(r"\b(hide|quiet|quietly|off|without)\b", text, re.IGNORECASE)
        ctx.store.set_value("core", "behaviour.show_terminals", wants)
        if wants:
            return (
                "Scripts and reports will run in their own terminal window so you "
                "can watch them. I still wait for them to finish and file the output."
            )
        return (
            "Scripts will run hidden, with their output going to the status line. "
            "Note: anything that prompts for input will fail this way."
        )

    @router.register(
        "stop talking",
        keywords=(
            "stop talking", "be quiet", "shut up", "stop speaking",
            "quiet", "silence", "stop the voice",
        ),
        help="Cuts off whatever I'm saying right now (same as the palm-out gesture).",
        category="voice",
        instant=True,
    )
    def cmd_stop_talking(text, ctx):
        return Reply(ctx.tts.silence(), speak=False)

    @router.register(
        "mic test",
        keywords=("mic test", "test my mic", "test microphone", "check my mic", "is my mic working"),
        help="Checks the microphone and reports what it actually captured.",
        category="voice",
    )
    def cmd_mic_test(text, ctx):
        from assistant.core.listening import SAMPLE_RATE, SpeechInput

        listener = getattr(ctx, "listener", None) or SpeechInput(on_text=lambda _t: None)
        rate = listener.device_rate()
        ctx.progress("Listening to the room for a moment...")
        try:
            import numpy as np

            stream = listener._open_stream()
            if stream is None:
                return "I couldn't open the microphone at all."
            with stream:
                threshold = listener._noise_floor(stream, rate)
                frames = int(rate * 0.1)
                chunks = [stream.read(frames)[0][:, 0] for _ in range(10)]
            audio = np.concatenate(chunks)
            rms = float(np.sqrt(np.mean(np.square(audio))))
            peak = float(np.max(np.abs(audio)))
        except Exception as exc:  # noqa: BLE001
            return f"Microphone test failed: {exc}"

        verdict = "looks healthy"
        if peak > 0.95:
            verdict = "is clipping - the input gain is too high, or the driver is mangling it"
        elif peak < 0.002:
            verdict = "is picking up almost nothing - check it isn't muted"
        return (
            f"Microphone at {rate} Hz (resampled to {SAMPLE_RATE} Hz for transcription).\n"
            f"  room noise : rms {rms:.4f}, peak {peak:.4f}\n"
            f"  speech starts above rms {threshold:.4f}\n"
            f"The signal {verdict}."
        )

    @router.register(
        "where do reports go",
        keywords=("where do reports go", "where are reports saved", "report location", "where do you save reports"),
        help="Explains where generated reports are filed.",
        category="assistant",
        instant=True,
    )
    def cmd_where_reports(text, ctx):
        root = ctx.store.value("reports", "output_root") or ctx.store.value("core", "defaults.reports_root")
        fmt = ctx.store.value("core", "day_folder.format", "{d} {month}")
        return (
            f"Generated reports go into {root}/{format_day(fmt=fmt)} - a folder per day, "
            f"created automatically if it doesn't exist yet.\n"
            f"Change the root with 'reports folder to <path>', or the naming via "
            f"day_folder.format in config/core.json (currently '{fmt}')."
        )
