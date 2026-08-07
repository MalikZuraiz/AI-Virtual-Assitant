"""Today's folder.

The user's existing report archive is organised as one folder per day named
like ``5 August`` / ``6 August`` / ``7 August`` - day number with no leading
zero, then the full month name. That naming is *data*, not a constant: the
format lives in ``config/core.json`` under ``day_folder.format`` so it can be
changed to ``{yyyy}-{mm}-{dd}`` or anything else without touching code.

Matching an existing folder is case- and whitespace-insensitive on purpose:
``7 August``, ``7 august`` and ``7  August`` are the same day, and creating a
near-duplicate folder because of a stray capital would quietly scatter a
day's reports across two places.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

DEFAULT_FORMAT = "{d} {month}"


def format_day(when: date | datetime | None = None, fmt: str = DEFAULT_FORMAT) -> str:
    """Render a day-folder name from a token format string.

    Tokens: ``{d} {dd} {m} {mm} {month} {mon} {yyyy} {yy} {weekday} {wd}``
    """
    when = when or datetime.now()
    tokens = {
        "d": str(when.day),
        "dd": f"{when.day:02d}",
        "m": str(when.month),
        "mm": f"{when.month:02d}",
        "month": when.strftime("%B"),
        "mon": when.strftime("%b"),
        "yyyy": f"{when.year:04d}",
        "yy": f"{when.year % 100:02d}",
        "weekday": when.strftime("%A"),
        "wd": when.strftime("%a"),
    }
    try:
        return fmt.format(**tokens)
    except (KeyError, IndexError, ValueError):
        # A hand-edited format with a typo should degrade, not crash the app.
        return format_day(when, DEFAULT_FORMAT)


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def find_day_folder(root: Path | str, name: str) -> Path | None:
    """Return an existing folder under ``root`` matching ``name`` loosely."""
    root = Path(root)
    if not root.is_dir():
        return None
    wanted = _normalise(name)
    for child in root.iterdir():
        if child.is_dir() and _normalise(child.name) == wanted:
            return child
    return None


def day_folder(
    root: Path | str,
    when: date | datetime | None = None,
    fmt: str = DEFAULT_FORMAT,
    *,
    create: bool = True,
) -> Path:
    """Path to the day folder for ``when``, reusing an existing one if present.

    With ``create=True`` (the default) both the root and the day folder are
    created if missing - that is the whole point of the feature: "generate the
    pending penalties report" on a fresh morning should just work, not fail
    because nobody made today's folder yet.
    """
    root = Path(root)
    name = format_day(when, fmt)
    existing = find_day_folder(root, name)
    if existing is not None:
        return existing
    target = root / name
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def recent_day_folders(root: Path | str, limit: int = 10) -> list[Path]:
    """Day folders under ``root``, newest-modified first."""
    root = Path(root)
    if not root.is_dir():
        return []
    folders = [p for p in root.iterdir() if p.is_dir()]
    folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return folders[:limit]
