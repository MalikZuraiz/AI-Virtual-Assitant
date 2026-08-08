"""Everyday utilities: finding things, network checks, quick maths, timers.

The theme here is "things you'd otherwise open a browser tab or an app for".
Each one is small; together they are most of what a desktop assistant gets
used for between the big workflows.

The ``find`` commands matter most: they are the answer to
"'nova directory': D:\\AI\\ai-virtual assitant does not exist". Nothing should
require typing an exact path - search by name, and when several things match,
ask with a numbered list.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from assistant.core.pathfinder import find_dirs, find_files, newest_files
from assistant.core.router import CommandRouter, Reply
from assistant.core.selection import Choice, offer
from assistant.integrations.netspeed import run_speed_test
from assistant.store.store import ConfigStore

DOC_EXTENSIONS = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".txt", ".md")


def _open_folder(path: Path) -> str:
    os.startfile(str(path))  # noqa: S606
    return f"Opened {path}."


def _open_in_code(path: Path) -> str:
    editor = shutil.which("code") or shutil.which("code.cmd")
    if not editor:
        return _open_folder(path)
    subprocess.Popen([editor, str(path)], shell=True)  # noqa: S603
    return f"Opened {path.name} in VS Code."


def _terminal_at(path: Path) -> str:
    subprocess.Popen(["cmd.exe", "/c", "start", "cmd.exe", "/k", f"cd /d {path}"])  # noqa: S603
    return f"Terminal open in {path.name}."


def open_folder_by_name(ctx, query: str) -> str:
    """Open a folder given a path *or* just its name.

    Shared with the legacy ``open folder`` command so both spellings behave
    the same. A real path opens immediately; a bare name is searched for, and
    several matches become a numbered list instead of a failure.
    """
    query = (query or "").strip().strip("\"'")
    if not query:
        return "Open which folder?"

    direct = Path(os.path.expandvars(query)).expanduser()
    if direct.is_dir():
        ctx.store.set_value("core", "last_active_path", direct.as_posix())
        return _open_folder(direct)
    if direct.is_file():
        return _open_folder(direct.parent)

    ctx.progress(f"Looking for a folder called '{query}'...")
    hits = find_dirs(query, store=ctx.store)
    if not hits:
        return (
            f"No folder called '{query}' on your drives.\n"
            "I search D: and your home folder - add more under "
            "defaults.search_roots in config/core.json."
        )

    def _open(choice, inner_ctx) -> str:
        path = Path(choice.payload)
        inner_ctx.store.set_value("core", "last_active_path", path.as_posix())
        return _open_folder(path)

    return offer(
        ctx,
        f"{len(hits)} folder(s) matching '{query}':",
        [Choice(h.label, str(h.path), h.detail) for h in hits],
        actions={
            "open": _open,
            "folder": _open,
            "use": _open,
            "code": lambda c, _cx: _open_in_code(Path(c.payload)),
            "run": lambda c, _cx: _terminal_at(Path(c.payload)),
        },
        default_action="open",
        hint="Say a number to open it, 'vs code 2' to edit it, 'run 2' for a terminal there.",
    )


def register(router: CommandRouter, store: ConfigStore) -> None:
    # -- finding things ---------------------------------------------------
    @router.register(
        "open folder by name",
        # The folder word can come first ("open folder office") or last
        # ("open the office folder"); both are natural and both used to miss.
        pattern=r"^\s*(?:open|go to|show)\s+(?:the\s+)?(?:folder|directory|dir)\s+(.+?)\s*$",
        help="open folder office  -  finds it by name, no path needed.",
        category="find",
        priority=4,
    )
    def cmd_open_folder_named(text, ctx):
        query = re.search(
            r"(?:open|go to|show)\s+(?:the\s+)?(?:folder|directory|dir)\s+(.+?)\s*$",
            text, re.IGNORECASE,
        ).group(1)
        return open_folder_by_name(ctx, query)

    @router.register(
        "find folder",
        pattern=r"^\s*(?:find|where is|locate|open)\s+(?:the\s+)?(.+?)\s+(?:folder|directory|dir)\s*$",
        help="find nova folder  -  searches your drives by name instead of needing a path.",
        category="find",
    )
    def cmd_find_folder(text, ctx):
        query = re.search(
            r"(?:find|where is|locate|open)\s+(?:the\s+)?(.+?)\s+(?:folder|directory|dir)\s*$",
            text, re.IGNORECASE,
        ).group(1).strip()

        ctx.progress(f"Searching for '{query}'...")
        hits = find_dirs(query, store=ctx.store)
        if not hits:
            return (
                f"Nothing called '{query}' on your drives.\n"
                "I search D: and your home folder - if it's elsewhere, add it "
                "under defaults.search_roots in config/core.json."
            )

        def _open(choice, inner_ctx) -> str:
            path = Path(choice.payload)
            inner_ctx.store.set_value("core", "last_active_path", path.as_posix())
            return _open_folder(path)

        def _code(choice, inner_ctx) -> str:
            return _open_in_code(Path(choice.payload))

        def _terminal(choice, inner_ctx) -> str:
            return _terminal_at(Path(choice.payload))

        def _save(choice, inner_ctx) -> str:
            path = Path(choice.payload)
            inner_ctx.store.set_value("core", "last_active_path", path.as_posix())
            return f"Saved {path} as the working folder - 'add command' will offer it."

        return offer(
            ctx,
            f"{len(hits)} folder(s) matching '{query}':",
            [Choice(h.label, str(h.path), h.detail) for h in hits],
            actions={
                "open": _open, "folder": _open, "use": _save,
                "code": _code, "run": _terminal,
            },
            default_action="open",
            hint="Say a number to open it, 'vs code 2' to edit it, 'run 2' for a terminal there, 'use 2' to remember it.",
        )

    @router.register(
        "find file",
        pattern=r"^\s*(?:find|locate|search for|where is)\s+(?:the\s+)?(?:file\s+)?(.+?)\s*(?:file)?\s*$",
        help="find july data  -  searches your drives for a file by name.",
        category="find",
        priority=7,
    )
    def cmd_find_file(text, ctx):
        query = re.search(
            r"(?:find|locate|search for|where is)\s+(?:the\s+)?(?:file\s+)?(.+?)\s*$",
            text, re.IGNORECASE,
        ).group(1).strip().removesuffix(" file").strip()
        if not query:
            return "Find what?"

        ctx.progress(f"Searching for '{query}'...")
        hits = find_files(query, store=ctx.store, limit=20)
        if not hits:
            folders = find_dirs(query, store=ctx.store, limit=8)
            if folders:
                return offer(
                    ctx,
                    f"No file called '{query}', but these folders match:",
                    [Choice(h.label, str(h.path), h.detail) for h in folders],
                    actions={"open": lambda c, _cx: _open_folder(Path(c.payload))},
                    default_action="open",
                )
            return f"Nothing called '{query}' turned up on your drives."

        def _open(choice, _ctx) -> str:
            os.startfile(str(choice.payload))  # noqa: S606
            return f"Opened {Path(choice.payload).name}."

        def _folder(choice, _ctx) -> str:
            return _open_folder(Path(choice.payload).parent)

        return offer(
            ctx,
            f"{len(hits)} file(s) matching '{query}':",
            [Choice(h.label, str(h.path), h.detail) for h in hits],
            actions={"open": _open, "use": _open, "folder": _folder, "code": _open},
            default_action="open",
            hint="Say a number to open it, or 'folder 2' to open where it lives.",
        )

    @router.register(
        "recent downloads",
        keywords=(
            "recent downloads", "latest downloads", "what did i download",
            "show downloads", "list downloads", "my downloads",
        ),
        help="Lists the newest files in Downloads, numbered.",
        category="find",
        instant=True,
    )
    def cmd_recent_downloads(text, ctx):
        folder = Path(ctx.store.value("core", "defaults.downloads_dir") or (Path.home() / "Downloads"))
        files = newest_files(folder, limit=15)
        if not files:
            return f"{folder} is empty."

        def _open(choice, _ctx) -> str:
            os.startfile(str(choice.payload))  # noqa: S606
            return f"Opened {Path(choice.payload).name}."

        def _use(choice, inner_ctx) -> str:
            inner_ctx.store.set_value("core", "last_active_path", str(Path(choice.payload).parent))
            return f"Noted {Path(choice.payload).name}. Use it with: generate <report> from \"{choice.payload}\""

        return offer(
            ctx,
            f"Newest in {folder.name}:",
            [
                Choice(
                    p.name, p,
                    f"{p.stat().st_size / 1024:,.0f} KB · {datetime.fromtimestamp(p.stat().st_mtime):%d %b %H:%M}",
                )
                for p in files
            ],
            actions={"open": _open, "use": _use, "folder": lambda c, _cx: _open_folder(Path(c.payload).parent)},
            default_action="open",
        )

    # -- network ----------------------------------------------------------
    @router.register(
        "speed test",
        keywords=(
            "speed test", "internet speed", "test my internet", "check internet speed",
            "how fast is my internet", "network speed", "test connection speed",
        ),
        help="Measures download, upload and ping (takes ~20 seconds).",
        category="network",
        priority=7,
    )
    def cmd_speed_test(text, ctx):
        ctx.progress("Starting speed test...")
        return Reply(run_speed_test(on_progress=ctx.progress).summary(), speak=True)

    @router.register(
        "am i online",
        keywords=("am i online", "is the internet working", "check internet", "internet status", "am i connected"),
        help="Quick check that the connection is alive.",
        category="network",
    )
    def cmd_online(text, ctx):
        import requests

        checks = {"Cloudflare": "https://1.1.1.1", "Google": "https://www.google.com"}
        results = []
        for name, url in checks.items():
            started = time.perf_counter()
            try:
                requests.head(url, timeout=5)
                results.append(f"  {name}: up ({(time.perf_counter() - started) * 1000:.0f} ms)")
            except requests.RequestException:
                results.append(f"  {name}: unreachable")
        alive = sum(1 for r in results if "up" in r)
        head = "You're online." if alive else "No internet connection I can see."
        return head + "\n" + "\n".join(results)

    # -- quick maths / units ----------------------------------------------
    @router.register(
        "calculate",
        pattern=r"^\s*(?:calc|calculate|what(?:'s| is))\s+([\d\s\.\+\-\*/%\(\)]+?)\s*$",
        help="calculate 1250 * 0.17  -  quick arithmetic.",
        category="tools",
        instant=True,
    )
    def cmd_calc(text, ctx):
        expression = re.search(
            r"(?:calc|calculate|what(?:'s| is))\s+([\d\s\.\+\-\*/%\(\)]+?)\s*$", text, re.IGNORECASE
        ).group(1)
        # Digits and operators only (the pattern already guarantees it), so
        # there is nothing here for eval to reach - no names, no calls.
        try:
            value = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
        except (SyntaxError, ZeroDivisionError, TypeError, ValueError) as exc:
            return f"I couldn't work that out ({exc})."
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{expression.strip()} = {value:,}" if isinstance(value, (int, float)) else str(value)

    @router.register(
        "timer",
        pattern=r"^\s*(?:set (?:a )?)?timer\s+(?:for\s+)?(\d+)\s*(second|sec|minute|min|hour|hr)s?\s*(?:for\s+(.+))?\s*$",
        help="timer for 10 minutes  -  a one-off countdown with a notification.",
        category="tools",
        instant=True,
    )
    def cmd_timer(text, ctx):
        match = re.search(
            r"timer\s+(?:for\s+)?(\d+)\s*(second|sec|minute|min|hour|hr)s?\s*(?:for\s+(.+))?\s*$",
            text, re.IGNORECASE,
        )
        amount, unit = int(match.group(1)), match.group(2).lower()
        label = (match.group(3) or "").strip()
        seconds = amount * {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}[unit]
        if seconds > 12 * 3600:
            return "That's more than half a day - use a reminder instead: 'remind me to X at 09:00'."

        message = label or f"{amount} {unit}{'s' if amount != 1 else ''} is up"
        fires = datetime.now() + timedelta(seconds=seconds)

        def _ring() -> None:
            time.sleep(seconds)
            ctx.notify("Timer", message)

        threading.Thread(target=_ring, name="timer", daemon=True).start()
        return f"Timer set - I'll tell you at {fires:%H:%M:%S}. ({message})"

    # -- everyday desktop -------------------------------------------------
    @router.register(
        "what's my day",
        keywords=(
            "what's my day", "whats my day", "my day", "daily briefing",
            "good morning", "brief me", "start my day",
        ),
        help="Weather, today's reports folder, reminders and battery in one go.",
        category="tools",
        priority=4,
    )
    def cmd_briefing(text, ctx):
        from assistant.integrations import web
        from assistant.workspace.daybook import day_folder, format_day

        lines = [f"{datetime.now():%A, %d %B %Y — %H:%M}"]

        city = getattr(ctx.config, "weather_city", "") or "Lahore"
        ctx.progress("Checking the weather...")
        lines.append("")
        lines.append(web.weather(city))

        root = ctx.store.value("reports", "output_root") or ctx.store.value("core", "defaults.reports_root")
        if root:
            fmt = ctx.store.value("core", "day_folder.format", "{d} {month}")
            folder = day_folder(root, fmt=fmt, create=False)
            count = len([p for p in folder.iterdir() if p.is_file()]) if folder.is_dir() else 0
            lines.append("")
            lines.append(f"Today's reports folder ({format_day(fmt=fmt)}): {count} file(s)")

        service = getattr(ctx, "reminders", None)
        if service is not None:
            rows = service.upcoming(limit=4)
            if rows:
                lines.append("")
                lines.append("Coming up:")
                for reminder, when in rows:
                    stamp = when.strftime("%a %H:%M") if when else "unscheduled"
                    lines.append(f"  - {reminder.text} ({stamp})")

        try:
            lines.append("")
            lines.append(ctx.system_info.battery())
        except Exception:  # noqa: BLE001 - desktops have no battery
            pass
        return Reply("\n".join(lines), speak=True)

    @router.register(
        "empty downloads",
        keywords=("clean downloads", "tidy downloads", "downloads older than"),
        help="Lists Downloads files older than 30 days so you can clear them out.",
        category="tools",
        instant=True,
    )
    def cmd_clean_downloads(text, ctx):
        folder = Path(ctx.store.value("core", "defaults.downloads_dir") or (Path.home() / "Downloads"))
        cutoff = time.time() - 30 * 86400
        old = [
            p for p in folder.iterdir()
            if p.is_file() and p.stat().st_mtime < cutoff and not p.name.startswith(".")
        ]
        if not old:
            return f"Nothing in {folder.name} is older than 30 days."
        size = sum(p.stat().st_size for p in old) / (1024 * 1024)
        listing = "\n".join(f"  - {p.name}" for p in sorted(old, key=lambda p: p.stat().st_mtime)[:15])
        return (
            f"{len(old)} file(s) in {folder.name} older than 30 days ({size:,.0f} MB):\n{listing}\n\n"
            "I won't delete anything automatically - open the folder and clear what you want."
        )

    @router.register(
        "open documents",
        keywords=("open my documents", "open documents folder", "open office documents", "my documents"),
        help="Opens your Documents folder.",
        category="tools",
    )
    def cmd_open_documents(text, ctx):
        path = Path(ctx.store.value("core", "defaults.documents_dir") or (Path.home() / "Documents"))
        if not path.is_dir():
            return f"{path} doesn't exist."
        return _open_folder(path)

    @router.register(
        "find document",
        pattern=r"^\s*(?:find|open)\s+(?:the\s+)?document\s+(.+?)\s*$",
        help="find document tehsil list  -  searches your documents and data folders.",
        category="find",
    )
    def cmd_find_document(text, ctx):
        query = re.search(r"document\s+(.+?)\s*$", text, re.IGNORECASE).group(1).strip()
        roots = [
            Path(p) for p in (
                ctx.store.value("core", "defaults.documents_dir"),
                ctx.store.value("core", "defaults.data_dir"),
                ctx.store.value("core", "defaults.office_root"),
                ctx.store.value("core", "defaults.downloads_dir"),
            ) if p and Path(p).is_dir()
        ]
        ctx.progress(f"Searching documents for '{query}'...")
        hits = find_files(query, roots or None, extensions=DOC_EXTENSIONS, store=ctx.store)
        if not hits:
            return f"No document matching '{query}' in your Documents, Data or Downloads folders."

        def _open(choice, _ctx) -> str:
            os.startfile(str(choice.payload))  # noqa: S606
            return f"Opened {Path(choice.payload).name}."

        return offer(
            ctx,
            f"{len(hits)} document(s) matching '{query}':",
            [Choice(h.label, str(h.path), h.detail) for h in hits],
            actions={"open": _open, "use": _open, "folder": lambda c, _cx: _open_folder(Path(c.payload).parent)},
            default_action="open",
        )
