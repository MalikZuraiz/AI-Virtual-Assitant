"""Getting the raw data file into a report generator.

The real workflow: export from a web dashboard, it lands in Downloads, you
rename it ``data``. Typing its full path every time is the friction this
removes.

Search order, best first:

1. a path given in the command (``generate X from D:/somewhere/raw.csv``)
2. a file called ``data.*`` in Downloads - the user's own convention
3. a file called ``data.*`` already staged in the generator's ``files/``
4. the newest matching export in Downloads
5. whatever is already in the generator's ``files/``

Whatever is chosen gets **copied** (never moved) into the generator's
``files/`` folder before the run, because that is where these scripts look
when given no argument, and because a copy means a failed run never costs you
the download.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from assistant.core.pathfinder import newest_files, normalise

logger = logging.getLogger("assistant.inputs")

#: The name the user gives their downloaded raw data.
DATA_STEM = "data"
DEFAULT_EXTENSIONS = (".csv", ".xlsx", ".xls", ".xlsm")

#: How a generator wants to be handed its input file.
#:   arg        positional path(s)
#:   flag       named option, e.g. --input / --csv
#:   files_dir  no argument at all; it reads its own files/ folder
#:   none       takes no input
INPUT_MODES = ("arg", "flag", "files_dir", "none")


@dataclass
class InputSpec:
    mode: str = "arg"
    flag: str = ""
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    stage_dir: str = "files"
    count: int = 1
    #: Optional date range options, e.g. {"from": "--from-date", "to": "--to-date"}
    date_flags: dict[str, str] = field(default_factory=dict)
    date_format: str = "%Y-%m-%d"
    #: Ask for the reporting period instead of assuming this month. Set
    #: for reports whose numbers change completely with the range.
    ask_dates: bool = False

    @classmethod
    def from_entry(cls, entry: dict) -> "InputSpec":
        raw = entry.get("input") or {}
        mode = str(raw.get("mode") or "arg").lower()
        return cls(
            mode=mode if mode in INPUT_MODES else "arg",
            flag=str(raw.get("flag") or ""),
            extensions=tuple(raw.get("extensions") or DEFAULT_EXTENSIONS),
            stage_dir=str(raw.get("stage_dir") or "files"),
            count=int(raw.get("count") or 1),
            date_flags={str(k): str(v) for k, v in (raw.get("date_flags") or {}).items()},
            date_format=str(raw.get("date_format") or "%Y-%m-%d"),
            ask_dates=bool(raw.get("ask_dates", False)),
        )

    @property
    def needs_input(self) -> bool:
        return self.mode != "none"


@dataclass
class Candidate:
    path: Path
    reason: str

    @property
    def label(self) -> str:
        return self.path.name

    @property
    def detail(self) -> str:
        try:
            size = self.path.stat().st_size / 1024
            when = date.fromtimestamp(self.path.stat().st_mtime)
            return f"{self.reason} · {size:,.0f} KB · {when:%d %b}"
        except OSError:
            return self.reason


@dataclass
class Resolution:
    """What input the assistant found, and whether it needs the user to pick."""

    spec: InputSpec
    chosen: list[Path] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    message: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.chosen) or not self.spec.needs_input

    @property
    def ambiguous(self) -> bool:
        return not self.chosen and len(self.candidates) > 1


def _stage_dir(entry: dict, spec: InputSpec) -> Path:
    base = Path(entry.get("working_directory") or ".")
    return base / spec.stage_dir if spec.stage_dir else base


def _matching(paths: Iterable[Path], spec: InputSpec) -> list[Path]:
    wanted = {e.lower() for e in spec.extensions}
    return [
        p for p in paths
        if p.is_file()
        and not p.name.startswith(("~$", "."))
        and (not wanted or p.suffix.lower() in wanted)
    ]


def _named_data(directory: Path, spec: InputSpec) -> list[Path]:
    """Files the user named ``data`` (data.csv, data (1).xlsx, raw data.csv)."""
    if not directory.is_dir():
        return []
    found = [
        p for p in _matching(directory.iterdir(), spec)
        if normalise(p.stem) == DATA_STEM
        or re.fullmatch(rf"{DATA_STEM}(\s*\(\d+\))?", normalise(p.stem))
        or normalise(p.stem).endswith(f" {DATA_STEM}")
    ]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found


def resolve_input(
    entry: dict,
    *,
    downloads_dir: Path | str,
    explicit: Sequence[Path | str] = (),
) -> Resolution:
    """Work out which file(s) to feed this generator."""
    spec = InputSpec.from_entry(entry)
    resolution = Resolution(spec=spec)
    if not spec.needs_input:
        return resolution

    if explicit:
        paths = [Path(p) for p in explicit]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            resolution.message = f"I can't find {', '.join(str(m) for m in missing)}."
            return resolution
        resolution.chosen = paths
        return resolution

    downloads = Path(downloads_dir)
    staged = _stage_dir(entry, spec)

    named = _named_data(downloads, spec)
    if named:
        resolution.chosen = named[: spec.count]
        resolution.message = f"Using {named[0].name} from Downloads."
        return resolution

    named_staged = _named_data(staged, spec)
    if named_staged:
        resolution.chosen = named_staged[: spec.count]
        resolution.message = f"Using {named_staged[0].name} already in {spec.stage_dir}/."
        return resolution

    # Nothing called "data" - offer everything plausible, newest first.
    seen: set[Path] = set()
    for path in newest_files(downloads, spec.extensions, limit=12):
        if path not in seen:
            seen.add(path)
            resolution.candidates.append(Candidate(path, "Downloads"))
    if staged.is_dir():
        for path in sorted(
            _matching(staged.iterdir(), spec),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:8]:
            if path not in seen:
                seen.add(path)
                resolution.candidates.append(Candidate(path, f"{spec.stage_dir}/"))

    if len(resolution.candidates) == 1:
        resolution.chosen = [resolution.candidates[0].path]
        resolution.message = f"Using {resolution.candidates[0].label}."
    elif not resolution.candidates:
        resolution.message = (
            f"No input file found. Download the raw data, rename it to "
            f"'{DATA_STEM}{spec.extensions[0]}', and try again - or say "
            f"'generate <report> from <path>'."
        )
    return resolution


def stage(paths: Sequence[Path], entry: dict, spec: InputSpec) -> list[Path]:
    """Copy the chosen file(s) into the generator's own input folder.

    Copied, not moved: these scripts are re-run often, and a failed run that
    ate the download would mean re-exporting from the dashboard.
    """
    destination = _stage_dir(entry, spec)
    if not spec.stage_dir:
        return [Path(p) for p in paths]
    destination.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for path in paths:
        source = Path(path)
        target = destination / source.name
        if source.resolve() == target.resolve():
            staged.append(target)
            continue
        try:
            shutil.copy2(source, target)
        except (OSError, shutil.Error) as exc:
            logger.warning("Could not stage %s: %s", source, exc)
            staged.append(source)
            continue
        staged.append(target)
    return staged


def build_args(
    paths: Sequence[Path],
    spec: InputSpec,
    when: date | None = None,
    date_range: tuple[date, date] | None = None,
) -> list[str]:
    """Turn staged paths (and any date range) into command-line arguments."""
    args: list[str] = []
    if spec.mode == "arg":
        args.extend(str(p) for p in paths)
    elif spec.mode == "flag" and spec.flag:
        for path in paths:
            args.extend([spec.flag, str(path)])
    # files_dir needs nothing: the script reads its own folder.

    if spec.date_flags:
        if date_range is not None:
            start, end = date_range
        else:
            # Only a fallback. Reports whose numbers depend on the period
            # set ``ask_dates`` so the range is asked for rather than
            # guessed - a silently wrong month is worse than a question.
            today = when or date.today()
            start, end = today.replace(day=1), today
        values = {"from": start, "to": end}
        for key, flag in spec.date_flags.items():
            args.extend([flag, values.get(key, end).strftime(spec.date_format)])
    return args


# ---------------------------------------------------------------------------
# Date ranges
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DMY = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})")
_DAY_MONTH = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?(?:\s+(\d{4}))?")
_MONTH_DAY = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?")


def _month_number(word: str) -> int | None:
    return _MONTHS.get(word.strip().lower()[:4].rstrip(".")) or _MONTHS.get(
        word.strip().lower()[:3]
    )


def _one_date(text: str, today: date) -> date | None:
    """Parse a single date in any of the shapes a person actually types."""
    text = text.strip().strip(",")
    if not text:
        return None
    low = text.lower()
    if low in ("today", "now"):
        return today
    if low == "yesterday":
        return today - timedelta(days=1)

    match = _ISO.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    match = _DMY.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    match = _DAY_MONTH.search(text)
    if match:
        month = _month_number(match.group(2))
        if month:
            year = int(match.group(3)) if match.group(3) else today.year
            try:
                return date(year, month, int(match.group(1)))
            except ValueError:
                return None

    match = _MONTH_DAY.search(text)
    if match:
        month = _month_number(match.group(1))
        if month:
            year = int(match.group(3)) if match.group(3) else today.year
            try:
                return date(year, month, int(match.group(2)))
            except ValueError:
                return None
    return None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return start, end


def parse_date_range(text: str, today: date | None = None) -> tuple[date, date] | None:
    """Understand a reporting period however it is phrased.

    Accepts "1 august to 7 august", "2026-08-01 to 2026-08-07", "01/08/2026 -
    07/08/2026", "last 7 days", "this month", "last month", "july", "today".
    Returns ``None`` when nothing sensible is there, so the caller can ask
    again rather than silently reporting on the wrong period.
    """
    today = today or date.today()
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower()

    relative = re.search(r"\blast\s+(\d+)\s*(day|week|month)s?\b", low)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        days = {"day": 1, "week": 7, "month": 30}[unit] * amount
        return today - timedelta(days=days - 1 if unit == "day" else days), today
    if re.search(r"\b(this|current)\s+month\b", low):
        return _month_bounds(today.year, today.month)
    if re.search(r"\blast\s+month\b", low):
        previous = today.replace(day=1) - timedelta(days=1)
        return _month_bounds(previous.year, previous.month)
    if re.search(r"\b(this|current)\s+week\b", low):
        return today - timedelta(days=today.weekday()), today
    if re.search(r"\blast\s+week\b", low):
        return today - timedelta(days=today.weekday() + 7), today - timedelta(days=today.weekday() + 1)
    if low in ("today", "so far today"):
        return today, today
    if low == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)

    # An explicit two-ended range: "A to B", "A - B", "between A and B".
    parts = re.split(r"\s+(?:to|until|till|through|and)\s+|\s+-\s+|\s*\.\.\.?\s*", raw, maxsplit=1)
    if len(parts) == 2:
        left = _one_date(parts[0], today)
        right = _one_date(parts[1], today)
        if left and right:
            # "1 august to 7 august" with the month only on one side.
            return (left, right) if left <= right else (right, left)
        if right and not left:
            # "to 7 august" only - treat as month-start up to that date.
            return right.replace(day=1), right

    # A bare month name means the whole month.
    bare_month = re.fullmatch(r"([A-Za-z]{3,9})\.?(?:\s+(\d{4}))?", raw.strip())
    if bare_month:
        month = _month_number(bare_month.group(1))
        if month:
            year = int(bare_month.group(2)) if bare_month.group(2) else today.year
            return _month_bounds(year, month)

    single = _one_date(raw, today)
    if single:
        return single, single
    return None
