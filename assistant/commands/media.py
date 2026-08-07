"""Entertainment: movies, series, music and clips.

The libraries themselves are just folders listed in ``config/media.json``,
so pointing this at a NAS, an external drive or a second movies folder is a
one-line edit rather than a code change.

Searching is fuzzy and matches *folders as well as files*, because that is
how film libraries are actually laid out on disk - "Interstellar (2014)
(2014) [1080p]/" is a folder with the video inside it, and asking for
"interstellar" should find it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from assistant.core.router import CommandRouter
from assistant.store.store import ConfigStore

try:
    from rapidfuzz import fuzz

    def _score(a: str, b: str) -> float:
        return float(fuzz.partial_ratio(a, b))
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def _score(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0


KIND_WORDS = {
    "movies": ("movie", "film"),
    "series": ("series", "show", "episode", "anime"),
    "music": ("song", "music", "track"),
    "clips": ("clip", "funny clip", "video"),
}

#: Release-metadata noise stripped before matching, so "interstellar" scores
#: against "Interstellar" and not "Interstellar (2014) (2014) [1080p]".
_NOISE = re.compile(r"[\[\(](?:[^\]\)]*)[\]\)]|\b(?:1080p|720p|2160p|4k|x264|x265|bluray|webrip|hdrip)\b", re.IGNORECASE)


def _clean(name: str) -> str:
    return re.sub(r"[\s._-]+", " ", _NOISE.sub(" ", name)).strip().lower()


def _libraries(store: ConfigStore) -> dict[str, list[Path]]:
    raw = store.get("media").get("libraries") or {}
    return {kind: [Path(p) for p in paths or []] for kind, paths in raw.items()}


def _extensions(store: ConfigStore, kind: str) -> set[str]:
    media = store.get("media")
    key = "audio_extensions" if kind == "music" else "video_extensions"
    return {e.lower() for e in (media.get(key) or [])}


def _entries(store: ConfigStore, kind: str) -> list[Path]:
    """Playable items in a library: media files plus their containing folders."""
    exts = _extensions(store, kind)
    found: list[Path] = []
    for root in _libraries(store).get(kind, []):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir():
                found.append(child)
            elif child.suffix.lower() in exts:
                found.append(child)
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                found.append(path)
    return found


def _playable(path: Path, exts: set[str]) -> Path | None:
    """Resolve a folder to the biggest media file inside it."""
    if path.is_file():
        return path
    candidates = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _find(store: ConfigStore, kind: str, query: str) -> tuple[Path | None, list[str]]:
    query = _clean(query)
    scored: list[tuple[float, Path]] = []
    for entry in _entries(store, kind):
        scored.append((_score(query, _clean(entry.name)), entry))
    scored.sort(key=lambda t: t[0], reverse=True)
    if not scored or scored[0][0] < 60:
        return None, [p.name for _s, p in scored[:5]]
    return scored[0][1], [p.name for _s, p in scored[1:4]]


def _kind_from_text(text: str, default: str = "movies") -> str:
    low = text.lower()
    for kind, words in KIND_WORDS.items():
        if any(word in low for word in words):
            return kind
    return default


def register(router: CommandRouter, store: ConfigStore) -> None:
    @router.register(
        "play media",
        pattern=r"^\s*(?:play|watch|put on)\s+(?:the\s+)?(?:movie|film|series|show|episode|anime|song|track|clip|video)\s+(.+?)\s*$",
        help="play movie interstellar  /  play song bohemian rhapsody  /  play clip <name>",
        category="entertainment",
        priority=4,
    )
    def cmd_play(text, ctx):
        query = re.search(
            r"(?:play|watch|put on)\s+(?:the\s+)?(?:movie|film|series|show|episode|anime|song|track|clip|video)\s+(.+?)\s*$",
            text,
            re.IGNORECASE,
        ).group(1).strip()
        kind = _kind_from_text(text)
        match, near = _find(ctx.store, kind, query)
        if match is None:
            hint = f" Closest I have: {', '.join(near)}." if near else ""
            return f"Nothing in your {kind} library matches '{query}'.{hint}"
        target = _playable(match, _extensions(ctx.store, kind))
        if target is None:
            os.startfile(str(match))  # noqa: S606
            return f"'{match.name}' has no playable file directly inside, so I opened the folder."
        os.startfile(str(target))  # noqa: S606
        return f"Playing {target.name}."

    @router.register(
        "list library",
        pattern=r"^\s*(?:list|show|what)\s+(?:my\s+)?(movies|films|series|shows|music|songs|clips)\b.*$",
        help="list movies  /  list music  /  list clips",
        category="entertainment",
        instant=True,
    )
    def cmd_list_library(text, ctx):
        word = re.search(r"(movies|films|series|shows|music|songs|clips)", text, re.IGNORECASE).group(1).lower()
        kind = {"films": "movies", "shows": "series", "songs": "music"}.get(word, word)
        roots = _libraries(ctx.store).get(kind, [])
        if not roots:
            return f"No {kind} folders configured. Add one under libraries.{kind} in config/media.json."
        names = sorted({p.name for p in _entries(ctx.store, kind) if p.is_dir() or p.parent in roots})
        if not names:
            return f"Your {kind} folders ({', '.join(str(r) for r in roots)}) are empty."
        listing = "\n".join(f"  - {n}" for n in names[:40])
        more = f"\n  ... and {len(names) - 40} more" if len(names) > 40 else ""
        return f"{len(names)} item(s) in your {kind} library:\n{listing}{more}"

    @router.register(
        "open library folder",
        pattern=r"^\s*open\s+(?:my\s+)?(movies|films|series|shows|music|clips|entertainment)\s*(?:folder)?\s*$",
        help="open movies folder  /  open entertainment",
        category="entertainment",
    )
    def cmd_open_library(text, ctx):
        word = re.search(r"(movies|films|series|shows|music|clips|entertainment)", text, re.IGNORECASE).group(1).lower()
        if word == "entertainment":
            root = Path(ctx.store.value("core", "defaults.entertainment_root", ""))
            if not root.is_dir():
                return f"{root} doesn't exist yet - say 'setup workspace' and I'll create it."
            os.startfile(str(root))  # noqa: S606
            return f"Opened {root}."
        kind = {"films": "movies", "shows": "series"}.get(word, word)
        roots = [p for p in _libraries(ctx.store).get(kind, []) if p.is_dir()]
        if not roots:
            return f"No existing folder configured for {kind}."
        os.startfile(str(roots[0]))  # noqa: S606
        return f"Opened {roots[0]}."

    @router.register(
        "add media folder",
        pattern=r"^\s*add\s+(movies|films|series|shows|music|clips)\s+folder\s+(.+?)\s*$",
        help="add movies folder E:/Films  -  registers another library location.",
        category="entertainment",
    )
    def cmd_add_library(text, ctx):
        match = re.search(r"add\s+(movies|films|series|shows|music|clips)\s+folder\s+(.+?)\s*$", text, re.IGNORECASE)
        word = match.group(1).lower()
        kind = {"films": "movies", "shows": "series"}.get(word, word)
        path = Path(match.group(2).strip().strip("\"'"))
        if not path.is_dir():
            return f"{path} isn't a folder I can see."

        def _mutate(doc: dict) -> None:
            libraries = doc.setdefault("libraries", {})
            current = libraries.setdefault(kind, [])
            if path.as_posix() not in current:
                current.append(path.as_posix())

        ctx.store.update("media", _mutate)
        count = len(_entries(ctx.store, kind))
        return f"Added {path} to your {kind} library ({count} item(s) visible now)."
