"""Finding things on disk by name instead of by full path.

"open the nova directory" should not require typing
``D:/AI/AI-Virtual-Assitant``. Almost everything lives on D:, so the
assistant searches there by name, and when a name is ambiguous it offers the
matches as a numbered list rather than guessing or failing.

Kept fast by bounding the search: a depth limit and an aggressive prune list
(``node_modules``, ``venv``, ``.git``, ``$RECYCLE.BIN``, ``AppData`` and
friends). Without that, a full walk of a data drive takes tens of seconds and
the feature is unusable.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger("assistant.pathfinder")

try:
    from rapidfuzz import fuzz

    def _ratio(a: str, b: str) -> float:
        return float(fuzz.token_set_ratio(a, b))
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0


#: Never descend into these - they are large, uninteresting, or both.
PRUNE = {
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", "appdata", "node_modules", "venv",
    ".venv", "env", ".git", "__pycache__", "site-packages", ".pytest_cache",
    "build", ".idea", ".vscode", "temp", "tmp", "cache", ".cache",
}

DEFAULT_MAX_DEPTH = 5
DEFAULT_LIMIT = 25
#: Below this score a candidate is not worth showing.
MIN_SCORE = 62.0


@dataclass
class Hit:
    path: Path
    score: float
    is_dir: bool

    @property
    def label(self) -> str:
        return self.path.name

    @property
    def detail(self) -> str:
        return str(self.path.parent)


def normalise(name: str) -> str:
    """Lowercase and collapse separators so 'ai-virtual assitant' ~ 'AI_Virtual_Assitant'."""
    return re.sub(r"[\s._\-]+", " ", str(name)).strip().lower()


def default_roots(store=None) -> list[Path]:
    """Where to look, in priority order."""
    roots: list[Path] = []
    if store is not None:
        configured = store.value("core", "defaults.search_roots") or []
        roots.extend(Path(p) for p in configured)
        for key in ("workspace_root", "office_root", "entertainment_root", "downloads_dir"):
            value = store.value("core", f"defaults.{key}")
            if value:
                roots.append(Path(value))
    if not roots:
        for letter in ("D:", "E:"):
            candidate = Path(letter + "\\")
            if candidate.exists():
                roots.append(candidate)
        roots.append(Path.home())
    # Dedupe while keeping order, and drop roots nested inside earlier ones.
    seen: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if not resolved.is_dir():
            continue
        if any(_is_within(resolved, existing) for existing in seen):
            continue
        seen.append(resolved)
    return seen


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _walk(root: Path, max_depth: int):
    """Depth-limited, pruned walk yielding ``(dirpath, dirnames, filenames)``."""
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in PRUNE and not d.startswith(".") and not d.startswith("$")
            ]
        yield dirpath, dirnames, filenames


def _score(query: str, name: str) -> float:
    """How well a filesystem name answers a query. Exact wins outright."""
    q, n = normalise(query), normalise(name)
    if not q:
        return 0.0
    if q == n:
        return 100.0
    if n.startswith(q):
        return 95.0
    if q in n:
        return 88.0
    return _ratio(q, n)


def find_dirs(
    query: str,
    roots: Sequence[Path] | None = None,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    limit: int = DEFAULT_LIMIT,
    store=None,
) -> list[Hit]:
    """Directories anywhere under ``roots`` whose name matches ``query``."""
    return _find(query, roots, max_depth=max_depth, limit=limit, store=store, want_dirs=True)


def find_files(
    query: str,
    roots: Sequence[Path] | None = None,
    *,
    extensions: Iterable[str] = (),
    max_depth: int = DEFAULT_MAX_DEPTH,
    limit: int = DEFAULT_LIMIT,
    store=None,
) -> list[Hit]:
    """Files anywhere under ``roots`` whose name matches ``query``."""
    return _find(
        query, roots, max_depth=max_depth, limit=limit, store=store,
        want_dirs=False, extensions={e.lower() for e in extensions},
    )


def _find(
    query: str,
    roots: Sequence[Path] | None,
    *,
    max_depth: int,
    limit: int,
    store,
    want_dirs: bool,
    extensions: set[str] | None = None,
) -> list[Hit]:
    query = (query or "").strip().strip("\"'")
    if not query:
        return []

    # An actual path was given - honour it and skip the search entirely.
    direct = Path(os.path.expandvars(query)).expanduser()
    if direct.exists() and (direct.is_dir() == want_dirs):
        return [Hit(direct.resolve(), 100.0, want_dirs)]

    search_roots = [Path(r) for r in roots] if roots else default_roots(store)
    hits: dict[Path, Hit] = {}
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for dirpath, dirnames, filenames in _walk(root, max_depth):
                names = dirnames if want_dirs else filenames
                for name in names:
                    if extensions and Path(name).suffix.lower() not in extensions:
                        continue
                    score = _score(query, Path(name).stem if not want_dirs else name)
                    if score < MIN_SCORE:
                        continue
                    path = Path(dirpath) / name
                    existing = hits.get(path)
                    if existing is None or score > existing.score:
                        hits[path] = Hit(path, score, want_dirs)
        except OSError as exc:
            logger.debug("Skipping %s: %s", root, exc)

    ranked = sorted(hits.values(), key=lambda h: (-h.score, len(str(h.path))))
    return ranked[:limit]


def newest_files(
    directory: Path | str,
    extensions: Iterable[str] = (),
    limit: int = 10,
    name_contains: str = "",
) -> list[Path]:
    """Most recently modified files in one folder - used for Downloads."""
    base = Path(directory)
    if not base.is_dir():
        return []
    wanted = {e.lower() for e in extensions}
    needle = normalise(name_contains)
    found: list[Path] = []
    for path in base.iterdir():
        if not path.is_file() or path.name.startswith(("~$", ".")):
            continue
        if wanted and path.suffix.lower() not in wanted:
            continue
        if needle and needle not in normalise(path.stem):
            continue
        found.append(path)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:limit]
