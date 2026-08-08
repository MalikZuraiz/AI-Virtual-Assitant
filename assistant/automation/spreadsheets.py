"""Excel work the user currently does by hand.

The specific chore this replaces: download two exports, open both, select
everything in the second below the header, copy, paste under the first, save.
Doing it manually is slow and it is easy to paste one row too high and
silently lose a header row - or to paste columns that were in a different
order in the second file, which produces a file that looks fine and is wrong.

So the merge here is **column-aware**: rows are aligned by column *name*, not
position. A column that exists in one file and not another is kept and left
blank rather than shifting everything sideways, and the report says exactly
what it did.

**Output is CSV.** The first version wrote .xlsx and a 819,000-row merge took
minutes - openpyxl builds every cell as a Python object before writing. CSV
of the same data is a few seconds, opens in Excel just the same, and is what
the report generators want to be fed anyway.

**Two merge paths.** When every input is a CSV with an identical header - the
normal case, two exports of the same dashboard - the files are concatenated
as raw bytes without ever being parsed. That turns a minutes-long
parse-concat-serialise cycle into a disk copy. Anything else (Excel inputs,
mismatched columns, de-duplication) falls back to the pandas path, which is
slower but handles the messy cases correctly.
"""
from __future__ import annotations

import csv
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger("assistant.spreadsheets")

READABLE = {".xlsx", ".xlsm", ".xls", ".csv"}

#: Copy buffer for the streaming path. Big enough that syscall overhead
#: disappears on a 120MB export, small enough not to matter on an 8GB laptop.
COPY_CHUNK = 4 * 1024 * 1024


class SpreadsheetError(RuntimeError):
    pass


def _require_pandas():
    try:
        import pandas as pd

        return pd
    except ImportError as exc:  # pragma: no cover
        raise SpreadsheetError(
            "This needs pandas. Run: .\\.venv\\Scripts\\pip install pandas openpyxl"
        ) from exc


@dataclass
class MergeResult:
    output: Path
    rows: int
    columns: int
    sources: list[tuple[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    fast_path: bool = False

    def summary(self) -> str:
        lines = [f"Merged {len(self.sources)} file(s) -> {self.output.name}"]
        for name, count in self.sources:
            lines.append(f"  + {name}  ({count:,} rows)")
        lines.append(f"Total: {self.rows:,} rows x {self.columns} columns")
        if self.seconds:
            how = "streamed" if self.fast_path else "parsed"
            lines.append(f"Took {self.seconds:.1f}s ({how}).")
        lines.append(f"Saved to {self.output}")
        lines.extend(f"  ! {w}" for w in self.warnings)
        return "\n".join(lines)


def read_table(path: Path, sheet: str | int = 0):
    """Read a csv/xlsx into a DataFrame, coping with odd encodings."""
    pd = _require_pandas()
    path = Path(path)
    if path.suffix.lower() == ".csv":
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return pd.read_csv(path, encoding=encoding, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise SpreadsheetError(f"Couldn't decode {path.name} as text.")
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception as exc:  # noqa: BLE001 - pandas raises a wide variety here
        raise SpreadsheetError(f"Couldn't read {path.name}: {exc}") from exc


def describe(path: Path) -> str:
    """Sheets, row and column counts - a quick look without opening Excel."""
    pd = _require_pandas()
    path = Path(path)
    if not path.is_file():
        raise SpreadsheetError(f"{path} doesn't exist.")
    if path.suffix.lower() == ".csv":
        frame = read_table(path)
        return (
            f"{path.name}\n  {len(frame):,} rows x {len(frame.columns)} columns\n"
            f"  Columns: {', '.join(str(c) for c in frame.columns[:12])}"
        )
    book = pd.ExcelFile(path)
    lines = [f"{path.name} - {len(book.sheet_names)} sheet(s)"]
    for name in book.sheet_names:
        frame = book.parse(name, nrows=200)
        lines.append(f"  - {name}: {len(frame.columns)} columns")
    return "\n".join(lines)


def _read_header(path: Path) -> tuple[str, list[str]] | None:
    """First line of a CSV, plus its parsed column names. None if unreadable."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                line = fh.readline()
            if not line:
                return None
            columns = next(csv.reader([line]), [])
            return line.rstrip("\r\n"), [c.strip() for c in columns]
        except UnicodeDecodeError:
            continue
    return None


def _can_stream(files: Sequence[Path]) -> tuple[bool, list[str]]:
    """True when every file is a CSV with the same columns in the same order.

    Compares the *parsed* column names, not the raw header line. Two exports
    of the same dashboard often quote differently ("Penalty ID",District vs
    Penalty ID,District) while being structurally identical, and a byte
    comparison would needlessly reject the fast path. Order still has to
    match, since streaming cannot reorder columns.
    """
    if any(f.suffix.lower() != ".csv" for f in files):
        return False, []
    first = _read_header(files[0])
    if first is None:
        return False, []
    columns = first[1]
    for path in files[1:]:
        other = _read_header(path)
        if other is None or other[1] != columns:
            return False, []
    return True, columns


def _stream_concat(
    files: Sequence[Path],
    target: Path,
    on_progress: Callable[[str], None] | None = None,
) -> list[int]:
    """Byte-copy CSVs under one header. Returns rows contributed by each file.

    No parsing at all: after skipping each subsequent file's header line the
    rest is copied verbatim, so quoted fields containing commas or embedded
    newlines survive untouched - and it runs at disk speed.
    """
    counts: list[int] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as out:
        last_byte = b"\n"
        for index, path in enumerate(files):
            if on_progress:
                size = path.stat().st_size / (1024 * 1024)
                on_progress(f"Copying {path.name} ({size:,.0f} MB)...")
            rows = 0
            with open(path, "rb") as fh:
                header = fh.readline()
                if index == 0:
                    out.write(header)
                    if header and not header.endswith(b"\n"):
                        out.write(b"\n")
                # A file whose final line has no newline would otherwise glue
                # itself onto the next file's first row.
                elif last_byte not in (b"\n", b"\r"):
                    out.write(b"\n")
                while True:
                    chunk = fh.read(COPY_CHUNK)
                    if not chunk:
                        break
                    rows += chunk.count(b"\n")
                    out.write(chunk)
                    last_byte = chunk[-1:]
            counts.append(rows)
    return counts


def merge_files(
    paths: Sequence[Path | str],
    output: Path | str | None = None,
    *,
    add_source_column: bool = False,
    sheet: str | int = 0,
    drop_duplicates: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> MergeResult:
    """Stack several files into one CSV, aligning columns by name.

    The first file defines the column order; later files are reindexed onto
    it so nothing lands in the wrong column when an export changes shape.
    """
    import time

    started = time.monotonic()
    files = [Path(p) for p in paths]
    if len(files) < 2:
        raise SpreadsheetError("Give me at least two files to merge.")

    missing = [f for f in files if not f.is_file()]
    if missing:
        raise SpreadsheetError(f"Can't find: {', '.join(m.name for m in missing)}")

    target = Path(output) if output else _default_output(files[0])

    # Fast path: identical CSV headers and nothing that needs the data parsed.
    if not add_source_column and not drop_duplicates:
        streamable, columns = _can_stream(files)
        if streamable:
            logger.info("Streaming merge of %d CSVs into %s", len(files), target)
            counts = _stream_concat(files, target, on_progress)
            return MergeResult(
                output=target,
                rows=sum(counts),
                columns=len(columns),
                sources=[(f.name, c) for f, c in zip(files, counts)],
                seconds=time.monotonic() - started,
                fast_path=True,
            )

    if on_progress:
        on_progress("Headers differ, so I'm aligning columns by name - this takes longer...")

    pd = _require_pandas()
    frames = []
    sources: list[tuple[str, int]] = []
    warnings: list[str] = []
    master_columns: list | None = None

    for path in files:
        if on_progress:
            on_progress(f"Reading {path.name}...")
        frame = read_table(path, sheet)
        frame = frame.dropna(axis=0, how="all")
        if master_columns is None:
            master_columns = list(frame.columns)
        else:
            extra = [c for c in frame.columns if c not in master_columns]
            absent = [c for c in master_columns if c not in frame.columns]
            if extra:
                warnings.append(
                    f"{path.name} has {len(extra)} column(s) the first file doesn't "
                    f"({', '.join(str(c) for c in extra[:4])}) - kept, blank elsewhere."
                )
                master_columns += extra
            if absent:
                warnings.append(
                    f"{path.name} is missing {len(absent)} column(s) "
                    f"({', '.join(str(c) for c in absent[:4])}) - left blank."
                )
        if add_source_column:
            frame["Source File"] = path.name
        sources.append((path.name, len(frame)))
        frames.append(frame)

    if add_source_column and master_columns is not None and "Source File" not in master_columns:
        master_columns.append("Source File")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    if master_columns:
        ordered = [c for c in master_columns if c in combined.columns]
        ordered += [c for c in combined.columns if c not in ordered]
        combined = combined[ordered]

    if drop_duplicates:
        before = len(combined)
        subset = [c for c in combined.columns if c != "Source File"]
        combined = combined.drop_duplicates(subset=subset)
        removed = before - len(combined)
        if removed:
            warnings.append(f"Dropped {removed:,} duplicate row(s).")

    if on_progress:
        on_progress(f"Writing {len(combined):,} rows to {target.name}...")
    target.parent.mkdir(parents=True, exist_ok=True)
    _write(combined, target)

    return MergeResult(
        output=target,
        rows=len(combined),
        columns=len(combined.columns),
        sources=sources,
        warnings=warnings,
        seconds=time.monotonic() - started,
    )


def combine_sheets(
    path: Path | str,
    output: Path | str | None = None,
    add_source_column: bool = True,
) -> MergeResult:
    """Stack every sheet of one workbook into a single sheet."""
    pd = _require_pandas()
    path = Path(path)
    if not path.is_file():
        raise SpreadsheetError(f"{path} doesn't exist.")

    book = pd.ExcelFile(path)
    frames, sources = [], []
    for name in book.sheet_names:
        frame = book.parse(name).dropna(axis=0, how="all")
        if frame.empty:
            continue
        if add_source_column:
            frame["Source Sheet"] = name
        frames.append(frame)
        sources.append((name, len(frame)))

    if not frames:
        raise SpreadsheetError(f"{path.name} has no sheets with data.")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    target = Path(output) if output else path.with_name(f"{path.stem} - combined.csv")
    _write(combined, target)
    return MergeResult(output=target, rows=len(combined), columns=len(combined.columns), sources=sources)


def to_excel(path: Path | str, output: Path | str | None = None) -> MergeResult:
    """Convert a CSV to a real .xlsx (slow on big files - that is openpyxl)."""
    frame = read_table(Path(path))
    target = Path(output) if output else Path(path).with_suffix(".xlsx")
    _write(frame, target)
    return MergeResult(
        output=target, rows=len(frame), columns=len(frame.columns),
        sources=[(Path(path).name, len(frame))],
    )


def to_csv(path: Path | str, output: Path | str | None = None) -> MergeResult:
    """Convert a spreadsheet to CSV."""
    frame = read_table(Path(path))
    target = Path(output) if output else Path(path).with_suffix(".csv")
    _write(frame, target)
    return MergeResult(
        output=target, rows=len(frame), columns=len(frame.columns),
        sources=[(Path(path).name, len(frame))],
    )


def _default_output(first: Path) -> Path:
    stamp = datetime.now().strftime("%d-%b-%Y %H%M")
    return first.parent / f"Merged {stamp}.csv"


def _write(frame, target: Path) -> None:
    target = Path(target)
    try:
        if target.suffix.lower() == ".csv":
            frame.to_csv(target, index=False, encoding="utf-8-sig")
            return
        frame.to_excel(target, index=False, engine="openpyxl")
    except PermissionError as exc:
        raise SpreadsheetError(
            f"{target.name} is open in Excel - close it and try again."
        ) from exc
