"""Report generators, and filing what they produce into today's folder.

The daily reality this automates: run one of the generators under
``D:/Python Reporting/...``, wait, then hand-copy the resulting spreadsheet
into ``D:/Reports/<today>`` - creating today's folder first, because it is a
new morning and nobody made it yet.

"generate pending penalties report" now does all of that in one line. The
list of generators is *data* (``config/reports.json``, seeded by scanning the
drive once), so a new report project appears as a command the moment it is
registered - no code change.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from assistant.core.router import CommandRouter, make_command
from assistant.store.store import ConfigStore
from assistant.workspace.daybook import day_folder, format_day, recent_day_folders
from assistant.workspace.reports import DEFAULT_EXTENSIONS, run_report

#: "generate X from <path>" / "using <path>" - hands the generator its input.
_INPUT_RE = re.compile(r"\b(?:from|using|with|for file)\s+[\"']?([A-Za-z]:[\\/][^\"']+|[^\s\"']+\.(?:csv|xlsx|xlsm))[\"']?", re.IGNORECASE)


def _output_root(store: ConfigStore) -> Path:
    return Path(
        store.value("reports", "output_root")
        or store.value("core", "defaults.reports_root")
        or (Path.home() / "Reports")
    )


def _day_format(store: ConfigStore) -> str:
    return str(store.value("core", "day_folder.format", "{d} {month}") or "{d} {month}")


def _report_triggers(entry: dict) -> list[str]:
    triggers = [t for t in (entry.get("trigger") or []) if t]
    return triggers or [str(entry.get("name", ""))]


def register(router: CommandRouter, store: ConfigStore) -> None:
    # -- one command per configured report generator ----------------------
    def provider():
        commands = []
        extensions = store.value("reports", "collect_extensions") or list(DEFAULT_EXTENSIONS)
        for entry in store.items("reports", "reports"):
            name = str(entry.get("name") or "").strip()
            if not name:
                continue

            def handler(text, ctx, _entry=entry, _exts=extensions):
                root = _output_root(ctx.store)
                fmt = _day_format(ctx.store)
                extra = []
                match = _INPUT_RE.search(text)
                if match:
                    path = match.group(1).strip()
                    if not Path(path).is_file():
                        return f"I can't find the input file {path}."
                    extra.append(path)
                ctx.progress(f"Running '{_entry.get('name')}'...")
                result = run_report(
                    _entry,
                    output_root=root,
                    day_format=fmt,
                    extensions=_exts,
                    extra_args=extra,
                    on_output=ctx.progress,
                )
                return result.summary()

            commands.append(
                make_command(
                    name=f"report: {name}",
                    triggers=_report_triggers(entry),
                    handler=handler,
                    help=str(entry.get("description") or f"Runs the {name} and files it under today's folder."),
                    category="reports",
                    priority=8,  # long-running: let quick commands overtake it
                    payload={"entry": entry},
                )
            )
        return commands

    router.add_provider(provider)

    # -- browsing what exists ---------------------------------------------
    @router.register(
        "list reports",
        keywords=("list reports", "what reports", "which reports", "show reports", "available reports"),
        help="Lists every report generator I can run.",
        category="reports",
        instant=True,
    )
    def cmd_list_reports(text, ctx):
        entries = ctx.store.items("reports", "reports")
        if not entries:
            return (
                "No report generators registered yet.\n"
                "Say 'scan for reports' and I'll look through your projects folder."
            )
        root = _output_root(ctx.store)
        today = format_day(fmt=_day_format(ctx.store))
        lines = [f"{len(entries)} report(s) I can generate - output goes to {root}/{today}:"]
        for entry in entries:
            triggers = _report_triggers(entry)
            lines.append(f"  - {entry.get('name')}   (say: \"{triggers[0]}\")")
        return "\n".join(lines)

    @router.register(
        "open today's reports",
        keywords=(
            "open today's reports", "open todays reports", "open today's report folder",
            "open reports folder", "open report folder", "show today's reports",
            "open today's folder",
        ),
        help="Opens (and creates if needed) today's folder under the reports root.",
        category="reports",
    )
    def cmd_open_today(text, ctx):
        root = _output_root(ctx.store)
        folder = day_folder(root, fmt=_day_format(ctx.store), create=True)
        os.startfile(str(folder))  # noqa: S606
        count = sum(1 for _ in folder.iterdir()) if folder.is_dir() else 0
        return f"Opened {folder} ({count} file(s) in there)."

    @router.register(
        "what's in today's reports",
        keywords=(
            "what's in today's reports", "whats in todays reports", "today's reports",
            "todays reports", "list today's reports", "reports generated today",
        ),
        help="Lists the files already filed into today's folder.",
        category="reports",
        instant=True,
    )
    def cmd_today_contents(text, ctx):
        root = _output_root(ctx.store)
        fmt = _day_format(ctx.store)
        folder = day_folder(root, fmt=fmt, create=False)
        if not folder.is_dir():
            return f"Nothing yet today - {folder} doesn't exist. It'll be created on the first report."
        files = sorted(
            p for p in folder.iterdir()
            if p.is_file() and not p.name.startswith(("~$", "."))
        )
        if not files:
            return f"{folder.name} is empty so far."
        lines = [f"{len(files)} file(s) in {folder.name}:"]
        lines.extend(f"  - {p.name}  ({p.stat().st_size / 1024:.0f} KB)" for p in files)
        return "\n".join(lines)

    @router.register(
        "recent report days",
        keywords=("recent reports", "recent report days", "last few days reports", "report history"),
        help="Shows the most recent day folders in your reports archive.",
        category="reports",
        instant=True,
    )
    def cmd_recent_days(text, ctx):
        root = _output_root(ctx.store)
        folders = recent_day_folders(root, limit=10)
        if not folders:
            return f"No day folders under {root} yet."
        lines = [f"Most recent day folders in {root}:"]
        for folder in folders:
            count = sum(1 for p in folder.iterdir() if p.is_file())
            lines.append(f"  - {folder.name}  ({count} file(s))")
        return "\n".join(lines)

    @router.register(
        "scan for reports",
        keywords=("scan for reports", "rescan reports", "find my reports", "discover reports", "rescan projects"),
        help="Re-scans your projects folder and registers any new report generators.",
        category="reports",
    )
    def cmd_scan(text, ctx):
        from assistant.store.bootstrap import seed_from_machine

        report = seed_from_machine(ctx.store)
        ctx.router.rebuild()
        return report.summary()

    @router.register(
        "set reports folder",
        pattern=r"^\s*(?:set\s+)?reports?\s+folder\s+(?:to\s+)?(.+?)\s*$",
        help="reports folder to D:/Reports  -  where generated reports get filed.",
        category="reports",
    )
    def cmd_set_root(text, ctx):
        raw = re.search(r"folder\s+(?:to\s+)?(.+?)\s*$", text, re.IGNORECASE).group(1).strip().strip("\"'")
        path = Path(raw)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        ctx.store.set_value("reports", "output_root", path.as_posix())
        ctx.store.set_value("core", "defaults.reports_root", path.as_posix())
        return f"Reports will now be filed under {path}/<today>."
