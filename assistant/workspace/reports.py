"""Run a report generator, then file whatever it produced into today's folder.

The user's real workflow: run one of the generators under
``D:/Python Reporting/...``, then hand-copy the resulting ``.xlsx``/``.pdf``
into ``D:/Reports/<today>``. This automates the whole thing - including
creating today's folder when it does not exist yet, which is the normal case
first thing in the morning.

Output is discovered by **diffing the filesystem around the run** rather than
by teaching the assistant each generator's naming scheme. Every generator
names its files differently ("Agency Wise Pending Penalties Jul-26 (as of
05-Aug-2026).xlsx", "6-August-2026 10Am.xlsx", ...) and those names change
whenever the user edits a script; a before/after snapshot keeps working
regardless, and keeps this module honest about what actually got produced.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from assistant.core.runner import RunResult, Runnable, execute, snapshot
from assistant.workspace.daybook import day_folder

logger = logging.getLogger("assistant.reports")

#: Never collect output from these - they are build plumbing, not reports.
IGNORED_PARTS = {
    "venv", ".venv", "env", "build", "__pycache__", ".git", "logs",
    "site-packages", ".pytest_cache", "node_modules",
}

DEFAULT_COLLECT_DIRS = ("dist", "output", "out", ".")
DEFAULT_EXTENSIONS = (".xlsx", ".xlsm", ".csv", ".pdf", ".docx", ".pptx")


@dataclass
class ReportRun:
    """Everything that happened during one report generation."""

    name: str
    run: RunResult | None = None
    day_folder: Path | None = None
    filed: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.run is None or self.run.ok)

    def summary(self) -> str:
        if self.error:
            return f"'{self.name}' failed: {self.error}"
        lines: list[str] = []
        took = f" in {self.run.duration:.0f}s" if self.run else ""
        if self.run and not self.run.ok:
            lines.append(f"'{self.name}' exited with code {self.run.returncode}{took}.")
            tail = self.run.tail()
            if tail:
                lines.append(tail)
            return "\n".join(lines)

        lines.append(f"'{self.name}' finished{took}.")
        if self.filed:
            where = self.day_folder.name if self.day_folder else "the output folder"
            lines.append(f"Filed {len(self.filed)} file(s) into '{where}':")
            lines.extend(f"  - {p.name}" for p in self.filed)
        else:
            lines.append(
                "No new output files were detected - check the generator's own "
                "output folder if you expected one."
            )
        if self.skipped:
            lines.append(f"({len(self.skipped)} file(s) already there, left alone.)")
        return "\n".join(lines)


def _is_ignored(path: Path, base: Path) -> bool:
    # "~$Report.xlsx" is Excel's lock file for an open workbook, not output.
    # Filing one away breaks the open document and leaves junk in the day
    # folder, so temp/hidden files never count as produced artefacts.
    if path.name.startswith(("~$", ".~", "~")) or path.name.startswith("."):
        return True
    try:
        relative = path.relative_to(base)
    except ValueError:
        relative = path
    return any(part in IGNORED_PARTS for part in relative.parts)


def _collect_dirs(runnable: Runnable, entry: dict) -> list[Path]:
    base = runnable.cwd or Path.cwd()
    names = entry.get("collect_from") or DEFAULT_COLLECT_DIRS
    out: list[Path] = []
    for name in names:
        candidate = Path(name)
        resolved = candidate if candidate.is_absolute() else (base / candidate)
        if resolved.is_dir():
            out.append(resolved.resolve())
    return out or [base.resolve()]


def _unique_destination(directory: Path, name: str, on_conflict: str) -> Path | None:
    """Pick where a collected file lands, honouring the conflict policy."""
    target = directory / name
    if not target.exists():
        return target
    if on_conflict == "overwrite":
        return target
    if on_conflict == "skip":
        return None
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(2, 100):
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem} ({datetime.now():%H%M%S}){suffix}"


def file_outputs(
    produced: Iterable[Path],
    destination: Path,
    *,
    mode: str = "move",
    on_conflict: str = "version",
) -> tuple[list[Path], list[Path]]:
    """Move/copy produced files into ``destination``. Returns (filed, skipped)."""
    destination.mkdir(parents=True, exist_ok=True)
    filed: list[Path] = []
    skipped: list[Path] = []
    for source in sorted(produced):
        if not source.is_file():
            continue
        if source.parent.resolve() == destination.resolve():
            skipped.append(source)
            continue
        target = _unique_destination(destination, source.name, on_conflict)
        if target is None:
            skipped.append(source)
            continue
        try:
            if mode == "copy":
                shutil.copy2(source, target)
            else:
                shutil.move(str(source), str(target))
            filed.append(target)
        except (OSError, shutil.Error) as exc:
            logger.warning("Could not file %s into %s: %s", source, destination, exc)
            skipped.append(source)
    return filed, skipped


def run_report(
    entry: dict,
    *,
    output_root: Path | str,
    day_format: str,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
    extra_args: list[str] | None = None,
    on_output: Callable[[str], None] | None = None,
    when: datetime | None = None,
    show_console: bool = False,
) -> ReportRun:
    """Run one ``reports.json`` entry and file its output into today's folder."""
    runnable = Runnable.from_entry(entry)
    if extra_args:
        runnable.args = [*runnable.args, *extra_args]
    if show_console:
        runnable.console = True
    result = ReportRun(name=runnable.name)

    exts = list(entry.get("extensions") or extensions)
    collect_dirs = _collect_dirs(runnable, entry)
    base = runnable.cwd or Path.cwd()

    def _scan() -> dict[Path, float]:
        return {
            path: mtime
            for path, mtime in snapshot(collect_dirs, exts, prune=IGNORED_PARTS).items()
            if not _is_ignored(path, base)
        }

    before = _scan()

    try:
        result.run = execute(runnable, on_output=on_output)
    except Exception as exc:  # noqa: BLE001 - reported to the user, not raised
        logger.exception("Report %s failed to start", runnable.name)
        result.error = str(exc)
        return result

    # Never file away a file we handed the generator as *input* - a script
    # that rewrites or re-saves its own source CSV would otherwise have it
    # moved out from under the folder the user keeps it in.
    inputs = {Path(arg).resolve() for arg in (extra_args or []) if Path(arg).exists()}
    after = _scan()
    produced = [p for p, mtime in after.items() if before.get(p) != mtime and p not in inputs]

    routing = str(entry.get("route_output") or "day_folder").lower()
    if routing == "none" or not produced:
        result.filed = []
        result.day_folder = None if routing == "none" else Path(output_root)
        if produced and routing == "none":
            result.filed = produced
        return result

    if routing == "day_folder":
        destination = day_folder(output_root, when=when, fmt=day_format, create=True)
    else:
        destination = Path(routing)
    result.day_folder = destination
    result.filed, result.skipped = file_outputs(
        produced,
        destination,
        mode=str(entry.get("collect_mode") or "move"),
        on_conflict=str(entry.get("on_conflict") or "version"),
    )
    return result
