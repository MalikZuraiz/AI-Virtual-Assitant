"""Spreadsheet commands - merging downloads without touching Excel.

Everything here leans on the numbered picker, because the real workflow is
"I just downloaded two exports" and neither of them has a name worth typing.
``merge downloads`` lists what is actually in the Downloads folder and merges
the ones picked.
"""
from __future__ import annotations

import re
from pathlib import Path

from assistant.automation.spreadsheets import (
    READABLE,
    SpreadsheetError,
    combine_sheets,
    describe,
    merge_files,
    to_csv,
    to_excel,
)
from assistant.core.pathfinder import find_files, newest_files
from assistant.core.router import CommandRouter
from assistant.core.selection import Choice, offer
from assistant.store.store import ConfigStore


def _downloads(store: ConfigStore) -> Path:
    return Path(store.value("core", "defaults.downloads_dir") or (Path.home() / "Downloads"))


def _resolve(store: ConfigStore, raw: str) -> list[Path]:
    """Turn a typed name into real files, searching if it isn't a path."""
    raw = raw.strip().strip("\"'")
    direct = Path(raw)
    if direct.is_file():
        return [direct]
    hits = find_files(raw, extensions=READABLE, store=store, limit=6)
    return [h.path for h in hits]


def register(router: CommandRouter, store: ConfigStore) -> None:
    @router.register(
        "merge downloads",
        keywords=(
            "merge downloads", "merge my downloads", "merge downloaded files",
            "combine downloads", "merge excel files", "merge files", "merge data files",
        ),
        help="Lists recent spreadsheets in Downloads and stacks the ones you pick into one file.",
        category="excel",
        instant=True,
    )
    def cmd_merge_downloads(text, ctx):
        folder = _downloads(ctx.store)
        files = newest_files(folder, READABLE, limit=15)
        if len(files) < 2:
            return (
                f"I need at least two spreadsheets in {folder} to merge "
                f"(found {len(files)}). Download them first, or say "
                "'merge <file a> and <file b>'."
            )

        def _merge(chosen: list[Choice], inner_ctx) -> str:
            """Merge everything picked, stacked in the order given."""
            paths = [Path(c.payload) for c in chosen]
            if len(paths) < 2:
                return "Give me at least two numbers, e.g. '1 2 3'."
            try:
                return merge_files(paths, on_progress=inner_ctx.progress).summary()
            except SpreadsheetError as exc:
                return str(exc)

        def _one(choice, inner_ctx) -> str:
            return "Pick at least two, e.g. '1 2' or '1 2 3 4'."

        return offer(
            ctx,
            f"{len(files)} spreadsheet(s) in {folder.name}:",
            [Choice(p.name, p, _detail(p)) for p in files],
            actions={"use": _one, "open": _one},
            default_action="use",
            on_multi=_merge,
            hint=(
                "Say every number you want, in one go - '1 2 3 4' (or '1-4'). "
                "They're stacked in the order you list them."
            ),
        )

    @router.register(
        "merge two files",
        pattern=r"^\s*(?:merge|combine|stack)\s+(.+?)\s+(?:and|with|\+|,)\s+(.+?)\s*$",
        help="merge july data and august data  -  stacks the second file under the first.",
        category="excel",
    )
    def cmd_merge_named(text, ctx):
        match = re.search(r"^\s*(?:merge|combine|stack)\s+(.+?)\s+(?:and|with|\+|,)\s+(.+?)\s*$", text, re.IGNORECASE)
        first, second = _resolve(ctx.store, match.group(1)), _resolve(ctx.store, match.group(2))
        for label, hits in (("first", first), ("second", second)):
            if not hits:
                return f"I couldn't find the {label} file. Try 'merge downloads' to pick from a list."
            if len(hits) > 1:
                return offer(
                    ctx,
                    f"Several files match the {label} one - which did you mean?",
                    [Choice(p.name, p, str(p.parent)) for p in hits],
                    actions={"use": lambda c, cx: f"Use the full path instead: merge \"{c.payload}\" and ..."},
                    default_action="use",
                )
        try:
            return merge_files([first[0], second[0]], on_progress=ctx.progress).summary()
        except SpreadsheetError as exc:
            return str(exc)

    @router.register(
        "combine sheets",
        pattern=r"^\s*combine\s+(?:the\s+)?sheets?\s+(?:in|of|from)\s+(.+?)\s*$",
        help="combine sheets in monthly report  -  stacks every sheet of a workbook into one.",
        category="excel",
    )
    def cmd_combine_sheets(text, ctx):
        raw = re.search(r"sheets?\s+(?:in|of|from)\s+(.+?)\s*$", text, re.IGNORECASE).group(1)
        hits = _resolve(ctx.store, raw)
        if not hits:
            return f"I couldn't find a workbook called '{raw}'."
        try:
            return combine_sheets(hits[0]).summary()
        except SpreadsheetError as exc:
            return str(exc)

    @router.register(
        "excel info",
        pattern=r"^\s*(?:excel|sheet|workbook)\s+info\s+(?:for\s+|of\s+)?(.+?)\s*$",
        help="excel info july data  -  sheets, rows and columns without opening Excel.",
        category="excel",
    )
    def cmd_excel_info(text, ctx):
        raw = re.search(r"info\s+(?:for\s+|of\s+)?(.+?)\s*$", text, re.IGNORECASE).group(1)
        hits = _resolve(ctx.store, raw)
        if not hits:
            return f"I couldn't find a spreadsheet called '{raw}'."
        try:
            return "\n\n".join(describe(p) for p in hits[:3])
        except SpreadsheetError as exc:
            return str(exc)

    @router.register(
        "csv to excel",
        pattern=r"^\s*convert\s+(.+?)\s+to\s+(?:excel|xlsx)\s*$",
        help="convert data to excel  -  turns a CSV into a real .xlsx.",
        category="excel",
    )
    def cmd_convert(text, ctx):
        raw = re.search(r"convert\s+(.+?)\s+to\s+(?:excel|xlsx)\s*$", text, re.IGNORECASE).group(1)
        hits = _resolve(ctx.store, raw)
        if not hits:
            return f"I couldn't find '{raw}'."
        try:
            return to_excel(hits[0]).summary()
        except SpreadsheetError as exc:
            return str(exc)

    @router.register(
        "excel to csv",
        pattern=r"^\s*convert\s+(.+?)\s+to\s+csv\s*$",
        help="convert july data to csv  -  turns a spreadsheet into a CSV.",
        category="excel",
    )
    def cmd_to_csv(text, ctx):
        raw = re.search(r"convert\s+(.+?)\s+to\s+csv\s*$", text, re.IGNORECASE).group(1)
        hits = _resolve(ctx.store, raw)
        if not hits:
            return f"I couldn't find '{raw}'."
        try:
            return to_csv(hits[0]).summary()
        except SpreadsheetError as exc:
            return str(exc)

    @router.register(
        "merge all downloads",
        # Requires "last/newest/latest" or an explicit count. A bare
        # "merge downloads" must fall through to the numbered picker rather
        # than silently merging whichever two files happen to be newest.
        pattern=r"^\s*merge\s+(?:the\s+)?(?:(?:last|newest|latest)\s+(\d+)?|(\d+))\s*(?:downloads|files|exports)\s*$",
        help="merge last 3 downloads  -  stacks the newest N spreadsheets with no picking.",
        category="excel",
        priority=6,
    )
    def cmd_merge_newest(text, ctx):
        match = re.search(
            r"merge\s+(?:the\s+)?(?:(?:last|newest|latest)\s+(\d+)?|(\d+))\s*(?:downloads|files|exports)\s*$",
            text, re.IGNORECASE,
        )
        count = int(match.group(1) or match.group(2) or 2)
        folder = _downloads(ctx.store)
        files = newest_files(folder, READABLE, limit=count)
        if len(files) < 2:
            return f"Only {len(files)} spreadsheet(s) in {folder.name} - I need at least two."
        # newest_files is newest-first; stack oldest-first so the merge reads
        # chronologically, which is what "paste the new one underneath" means.
        files = list(reversed(files))
        try:
            return merge_files(files, on_progress=ctx.progress).summary()
        except SpreadsheetError as exc:
            return str(exc)


def _detail(path: Path) -> str:
    try:
        stat = path.stat()
        from datetime import datetime

        return f"{stat.st_size / 1024:,.0f} KB · {datetime.fromtimestamp(stat.st_mtime):%d %b %H:%M}"
    except OSError:
        return ""
