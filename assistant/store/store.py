"""``ConfigStore`` - the only thing that reads or writes ``config/*.json``.

Design points that matter (project brief §4.2 / §4.4):

* **Hand-edits must never need a restart.** ``refresh()`` rereads every file
  from disk and reports exactly what changed, and it is also what the
  ``"refresh"`` chat command calls.
* **A broken JSON file must not take the assistant down.** A file that fails
  to parse keeps its last-good in-memory document and is reported as an
  error, so one stray comma in ``websites.json`` cannot stop reminders from
  firing.
* **Writes are atomic.** Every write goes to a temp file in the same
  directory and is then ``os.replace``-d over the target, so a crash or a
  power cut can never leave a truncated config behind.
* **New default keys appear in old files.** Defaults are deep-merged
  *underneath* whatever the user has, so upgrading the assistant never
  silently drops a new setting - and never overwrites a customised one.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from assistant.store import defaults as _defaults
from assistant.store.paths import config_dir

logger = logging.getLogger("assistant.store")


@dataclass
class RefreshReport:
    """What a ``refresh()`` actually did - surfaced verbatim in chat."""

    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        bits: list[str] = []
        if self.created:
            bits.append(f"created {', '.join(sorted(self.created))}")
        if self.changed:
            bits.append(f"reloaded {', '.join(sorted(self.changed))}")
        if not self.changed and not self.created:
            bits.append(f"all {len(self.unchanged)} config files already up to date")
        text = "Config refreshed - " + "; ".join(bits) + "."
        if self.errors:
            problems = "\n".join(f"  - {name}.json: {err}" for name, err in self.errors.items())
            text += f"\nBut {len(self.errors)} file(s) could not be parsed (kept the last good copy):\n{problems}"
        return text


#: Top-level keys, per config domain, that are *user-owned registries* -
#: name-keyed maps the user adds to and deletes from - rather than nested
#: settings-with-sub-settings. These are replaced wholesale from the user's
#: file when present, exactly like a list, instead of being recursively
#: merged with the seed defaults.
#:
#: Without this, deleting an entry never actually sticks: the next load
#: merges the seed's version of that key back in key-by-key, so a removed
#: gesture binding or chat persona silently reappears. That is not
#: hypothetical - it is exactly what made "gestures keep colliding" survive
#: a rewrite that had already deleted every stale binding from the JSON on
#: disk: the seed in defaults.py still listed them, and every load re-merged
#: them back into the in-memory document no matter what the file said.
WHOLESALE_DICT_KEYS: dict[str, frozenset[str]] = {
    "gestures": frozenset({"bindings"}),
    "personas": frozenset({"modes"}),
}


def deep_merge(base: dict, override: dict, wholesale: frozenset[str] = frozenset()) -> dict:
    """Return ``base`` with ``override`` layered on top (override wins).

    Nested dicts merge key-by-key; lists are replaced wholesale, because a
    user who trimmed a list down to two entries means it, and re-adding the
    defaults would be infuriating. ``wholesale`` extends that same treatment
    to specific top-level dict keys that are really registries, not settings
    - see :data:`WHOLESALE_DICT_KEYS`. Only applies at this call's own level;
    it is not threaded into the recursive calls, because those keys only
    exist at the top of a config document.
    """
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in wholesale:
            out[key] = copy.deepcopy(value)
        elif key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ConfigStore:
    """Loads, caches, refreshes and atomically writes the split config files."""

    def __init__(self, directory: Path | None = None, names: Iterable[str] | None = None) -> None:
        self.dir = Path(directory) if directory else config_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.names: tuple[str, ...] = tuple(names) if names else _defaults.FILE_ORDER
        self._docs: dict[str, dict] = {}
        self._stamps: dict[str, tuple[float, int]] = {}
        self._lock = threading.RLock()
        self._listeners: list[Callable[[RefreshReport], None]] = []
        self.load_all()

    # -- paths ------------------------------------------------------------
    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    # -- reading ----------------------------------------------------------
    def load_all(self) -> RefreshReport:
        """Initial load: create anything missing, read everything else."""
        return self._read(force=True)

    def refresh(self) -> RefreshReport:
        """Reread every config file from disk (the ``refresh`` command)."""
        report = self._read(force=True)
        for listener in list(self._listeners):
            try:
                listener(report)
            except Exception:  # noqa: BLE001 - a bad listener must not break refresh
                logger.exception("Config refresh listener failed")
        return report

    def reload_if_changed(self) -> RefreshReport:
        """Cheap poll: only reread files whose mtime/size moved."""
        return self._read(force=False)

    def _read(self, *, force: bool) -> RefreshReport:
        report = RefreshReport()
        with self._lock:
            for name in self.names:
                path = self.path(name)
                if not path.exists():
                    doc = _defaults.default_for(name)
                    self._docs[name] = doc
                    self._write_atomic(path, doc)
                    self._stamps[name] = self._stamp(path)
                    report.created.append(name)
                    continue

                stamp = self._stamp(path)
                if not force and self._stamps.get(name) == stamp:
                    report.unchanged.append(name)
                    continue

                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                    if not isinstance(raw, dict):
                        raise ValueError("top level must be a JSON object")
                except (json.JSONDecodeError, OSError, ValueError) as exc:
                    report.errors[name] = str(exc)
                    logger.warning("Config %s.json failed to parse: %s", name, exc)
                    self._docs.setdefault(name, _defaults.default_for(name))
                    continue

                merged = deep_merge(
                    _defaults.default_for(name), raw, wholesale=WHOLESALE_DICT_KEYS.get(name, frozenset())
                )
                previous = self._docs.get(name)
                self._docs[name] = merged
                self._stamps[name] = stamp
                if previous != merged:
                    report.changed.append(name)
                else:
                    report.unchanged.append(name)
        return report

    @staticmethod
    def _stamp(path: Path) -> tuple[float, int]:
        st = path.stat()
        return (st.st_mtime, st.st_size)

    # -- accessors --------------------------------------------------------
    def get(self, name: str) -> dict:
        """The in-memory document for ``name`` (a live reference - do not edit)."""
        with self._lock:
            if name not in self._docs:
                self._docs[name] = _defaults.default_for(name)
            return self._docs[name]

    def value(self, name: str, dotted: str, fallback: Any = None) -> Any:
        """``store.value("core", "defaults.reports_root")`` with a fallback."""
        node: Any = self.get(name)
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return fallback
            node = node[part]
        return node if node is not None else fallback

    def items(self, name: str, key: str) -> list:
        """A list-valued section, always a list even if the file is malformed."""
        value = self.get(name).get(key)
        return value if isinstance(value, list) else []

    # -- writing ----------------------------------------------------------
    def update(self, name: str, mutator: Callable[[dict], Any]) -> Any:
        """Mutate a document in place and persist it atomically.

        The mutator runs under the store lock so two chat commands landing at
        once cannot interleave a read-modify-write and lose one of the edits.
        """
        with self._lock:
            doc = copy.deepcopy(self.get(name))
            result = mutator(doc)
            self._docs[name] = doc
            path = self.path(name)
            self._write_atomic(path, doc)
            self._stamps[name] = self._stamp(path)
            return result

    def set_value(self, name: str, dotted: str, value: Any) -> None:
        """Set one nested key, creating intermediate dicts as needed."""

        def _mutate(doc: dict) -> None:
            node = doc
            parts = dotted.split(".")
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value

        self.update(name, _mutate)

    def append(self, name: str, key: str, entry: dict) -> None:
        """Append to a list-valued section (creating the list if absent)."""

        def _mutate(doc: dict) -> None:
            listing = doc.get(key)
            if not isinstance(listing, list):
                listing = []
                doc[key] = listing
            listing.append(entry)

        self.update(name, _mutate)

    def _write_atomic(self, path: Path, doc: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def backup(self, name: str) -> Path | None:
        """Copy a config file next to itself as ``<name>.json.bak``."""
        src = self.path(name)
        if not src.exists():
            return None
        dst = src.with_suffix(".json.bak")
        shutil.copy2(src, dst)
        return dst

    # -- change notification ---------------------------------------------
    def on_refresh(self, listener: Callable[[RefreshReport], None]) -> None:
        """Register a callback fired after every explicit ``refresh()``."""
        self._listeners.append(listener)
