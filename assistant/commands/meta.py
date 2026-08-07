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
        return Reply(
            ctx.router.help_text(category),
            speak=False,  # a 200-line list is not something to read aloud
        )

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

    @router.register(
        "ask",
        pattern=r"^\s*(?:ask|chat)\s*:?\s+(.+)$",
        help="ask what's a good name for this function  -  open chat via the local model.",
        category="assistant",
        priority=6,
    )
    def cmd_ask(text, ctx):
        prompt = re.search(r"^\s*(?:ask|chat)\s*:?\s+(.+)$", text, re.IGNORECASE | re.DOTALL).group(1)
        llm = ctx.llm
        if llm is None:
            return "Open chat isn't wired up on this build."
        ctx.progress("Thinking (local model, this can take a few seconds)...")
        return llm.chat(prompt)

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
