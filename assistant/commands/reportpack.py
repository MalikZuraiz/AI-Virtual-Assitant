"""Report generators, and filing what they produce into today's folder.

The daily reality this automates: run one of the generators under
``D:/Office/Scripts/...``, wait, then hand-copy the resulting spreadsheet
into ``D:/Office/Reports/<today>`` - creating today's folder first, because
it is a new morning and nobody made it yet.

"generate pending penalties report" now does all of that in one line. The
list of generators is *data* (``config/reports.json``, seeded by scanning the
drive once), so a new report project appears as a command the moment it is
registered - no code change.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from assistant.core.conversation import Retry, Step, Wizard
from assistant.core.router import CommandRouter, make_command
from assistant.core.selection import Choice, offer
from assistant.store.store import ConfigStore
from assistant.workspace.daybook import day_folder, format_day, recent_day_folders
from assistant.workspace.inputs import build_args, parse_date_range, resolve_input, stage
from assistant.workspace.reports import DEFAULT_EXTENSIONS, run_report

#: "generate X from <path>" / "using <path>" - hands the generator its input.
#: Input paths in the command itself. Matches each of
#:   generate x from "D:/a.csv" and "D:/b.csv"
#:   generate x using D:/a.csv, D:/b.csv
#: The leading keyword is optional after the first, so a list of paths
#: separated by commas or "and" is picked up whole.
_INPUT_RE = re.compile(
    r"(?:\b(?:from|using|with|for file)\s+|,\s*|\s+and\s+)"
    r"[\"']?([A-Za-z]:[\\/][^\"',]+?\.(?:csv|xlsx|xlsm|xls)|[^\s\"',]+\.(?:csv|xlsx|xlsm|xls))[\"']?",
    re.IGNORECASE,
)


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


def _downloads(store: ConfigStore) -> Path:
    return Path(store.value("core", "defaults.downloads_dir") or (Path.home() / "Downloads"))


#: A period stated in the command itself, after "for" / "from" / "between".
_RANGE_RE = re.compile(
    r"\b(?:for|covering|between|period)\s+(.+?)\s*$|\bfrom\s+((?!\S+\.(?:csv|xlsx|xlsm|xls))\S.*?)\s*$",
    re.IGNORECASE,
)


def _stated_range(text: str):
    """Pull a date range out of the command, if one is there."""
    match = _RANGE_RE.search(text)
    if not match:
        return None
    return parse_date_range(match.group(1) or match.group(2) or "")


def _ask_for_range(entry: dict, ctx, resolution, extensions) -> str:
    """One-question wizard for the reporting period, then run the report."""
    chosen = [str(p) for p in resolution.chosen]
    name = entry.get("name")

    def _parse(answer: str, _data: dict):
        parsed = parse_date_range(answer)
        if parsed is None:
            raise Retry(
                "I couldn't read that as a date range. Try '1 august to 7 august', "
                "'2026-08-01 to 2026-08-07', 'last 7 days' or 'this month'."
            )
        return parsed

    def _finish(data: dict) -> str:
        return run_with_input(
            entry, ctx, explicit=chosen, extensions=extensions, date_range=data["range"]
        )

    using = f"Using {Path(chosen[0]).name}. " if chosen else ""
    wizard = Wizard(
        name=f"{name} period",
        steps=[
            Step(
                "range",
                f"{using}What period should '{name}' cover?\n"
                "  e.g. '1 august to 7 august', 'last 7 days', 'this month'",
                _parse,
            )
        ],
        on_finish=_finish,
        summarise=lambda d: f"  {d['range'][0]:%d %b %Y}  to  {d['range'][1]:%d %b %Y}",
    )
    return ctx.conversation.begin(wizard)


def run_with_input(
    entry: dict,
    ctx,
    *,
    explicit: list[str] | None = None,
    extensions=None,
    date_range=None,
) -> str:
    """Find the raw data file, stage it, run the generator, file the output.

    When the input is ambiguous this returns a numbered list instead of
    guessing - picking the wrong export silently produces a wrong report,
    which is far worse than one extra question.
    """
    resolution = resolve_input(
        entry, downloads_dir=_downloads(ctx.store), explicit=explicit or []
    )

    if resolution.ambiguous:
        def _use(choice, inner_ctx) -> str:
            return run_with_input(entry, inner_ctx, explicit=[str(choice.payload)])

        def _use_many(chosen, inner_ctx) -> str:
            return run_with_input(
                entry, inner_ctx, explicit=[str(c.payload) for c in chosen]
            )

        wanted = resolution.spec.count
        howmany = (
            f"Say the {wanted} numbers in one go, e.g. '1 2 3 4'."
            if wanted > 1
            else "Say a number - or several ('1 2 3') if this one takes more than one file."
        )
        return offer(
            ctx,
            f"Which file(s) should '{entry.get('name')}' use?",
            [Choice(c.label, c.path, c.detail) for c in resolution.candidates],
            actions={"use": _use, "open": _use, "run": _use},
            default_action="use",
            on_multi=_use_many,
            hint=f"{howmany} (Tip: name your download 'data' and I'll just pick it up.)",
        )

    if not resolution.ready:
        return resolution.message or f"'{entry.get('name')}' needs an input file."

    # Reports whose numbers depend on a period ask for it rather than
    # assuming this month - a report silently covering the wrong dates looks
    # completely fine and is completely wrong.
    if resolution.spec.ask_dates and date_range is None:
        return _ask_for_range(entry, ctx, resolution, extensions)

    staged = stage(resolution.chosen, entry, resolution.spec) if resolution.chosen else []
    args = build_args(staged, resolution.spec, date_range=date_range)

    if resolution.message:
        ctx.progress(resolution.message)
    if date_range:
        ctx.progress(f"Period {date_range[0]:%d %b %Y} to {date_range[1]:%d %b %Y}.")
    ctx.progress(f"Running '{entry.get('name')}'...")

    result = run_report(
        entry,
        output_root=_output_root(ctx.store),
        day_format=_day_format(ctx.store),
        extensions=extensions or (ctx.store.value("reports", "collect_extensions") or DEFAULT_EXTENSIONS),
        extra_args=args,
        on_output=ctx.progress,
        show_console=bool(ctx.store.value("core", "behaviour.show_terminals", True)),
    )
    head = f"{resolution.message}\n" if resolution.message else ""
    return head + result.summary()


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
                # Several paths can be given at once - some generators take
                # four files, and naming them one prompt at a time is worse
                # than typing them.
                explicit = [m.group(1).strip() for m in _INPUT_RE.finditer(text)]
                # A period stated up front skips the follow-up question:
                # "generate tmo scoring report for 1 august to 7 august".
                stated = _stated_range(text)
                return run_with_input(
                    _entry, ctx, explicit=explicit, extensions=_exts, date_range=stated
                )

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
        keywords=(
            "list reports", "what reports", "which reports", "show reports",
            "available reports", "list scripts", "show scripts", "my scripts",
            "list my scripts", "list generators",
        ),
        help="Lists every report generator, numbered - then say '3' to run it.",
        category="reports",
        instant=True,
    )
    def cmd_list_reports(text, ctx):
        entries = ctx.store.items("reports", "reports")
        if not entries:
            return (
                "No report generators registered yet.\n"
                "Say 'scan for reports' and I'll look through your scripts folder."
            )

        def _run(choice, inner_ctx) -> str:
            return run_with_input(choice.payload, inner_ctx)

        def _folder(choice, inner_ctx) -> str:
            path = Path(choice.payload.get("working_directory", ""))
            if not path.is_dir():
                return f"{path} isn't there any more - say 'scan for reports'."
            os.startfile(str(path))  # noqa: S606
            return f"Opened {path}."

        def _code(choice, inner_ctx) -> str:
            path = Path(choice.payload.get("working_directory", ""))
            editor = shutil.which("code") or shutil.which("code.cmd")
            if not editor:
                os.startfile(str(path))  # noqa: S606
                return f"VS Code isn't on PATH, so I opened {path} in Explorer."
            subprocess.Popen([editor, str(path)], shell=True)  # noqa: S603
            return f"Opened {path.name} in VS Code."

        today = format_day(fmt=_day_format(ctx.store))
        choices = []
        for entry in entries:
            spec = entry.get("input") or {}
            need = "no input needed" if spec.get("mode") == "none" else "needs raw data"
            choices.append(Choice(str(entry.get("name")), entry, f"({need})"))

        return offer(
            ctx,
            f"{len(entries)} report(s) - output goes to {_output_root(ctx.store)}\\{today}:",
            choices,
            actions={"run": _run, "use": _run, "open": _folder, "folder": _folder, "code": _code},
            default_action="run",
            hint="Say a number to generate it, 'folder 3' to open its folder, 'vs code 3' to edit it.",
        )

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
