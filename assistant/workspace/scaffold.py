"""Creating the folders the assistant expects to exist.

Strictly additive: this creates missing folders and nothing else. It never
moves, renames or deletes anything the user already has - existing libraries
(``D:/Movies``, ``D:/Reports``, ``D:/Python Reporting``) are *registered* in
config and left exactly where they are. Reorganising someone's drive is
their call, not the assistant's.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from assistant.store.store import ConfigStore

logger = logging.getLogger("assistant.scaffold")

README_NAME = "_what-is-this.txt"

HUB_BLURBS = {
    "entertainment": (
        "Entertainment hub, kept by the Nova assistant.\n"
        "Movies / Series / Music / Funny Clips are searched by 'play <name>'.\n"
        "Point it at other folders instead by editing config/media.json.\n"
    ),
    "office": (
        "Work hub, kept by the Nova assistant.\n"
        "Scripts/   runnable scripts ('add command' registers them)\n"
        "Reports/   generated reports, one folder per day\n"
        "Data/      raw data exports the report scripts read\n"
        "Projects/  Python and Flutter projects created from chat\n"
    ),
}


@dataclass
class ScaffoldReport:
    created: list[Path] = field(default_factory=list)
    existing: list[Path] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        if not self.created and not self.failed:
            return f"Workspace already in place ({len(self.existing)} folders checked)."
        lines: list[str] = []
        if self.created:
            lines.append(f"Created {len(self.created)} folder(s):")
            lines.extend(f"  + {p}" for p in self.created)
        if self.existing:
            lines.append(f"{len(self.existing)} folder(s) already existed - left untouched.")
        if self.failed:
            lines.append("Could not create:")
            lines.extend(f"  ! {path}: {err}" for path, err in self.failed.items())
        return "\n".join(lines)


def _ensure(path: Path, report: ScaffoldReport) -> bool:
    """Create ``path`` if missing. Returns True when it was newly created."""
    try:
        if path.is_dir():
            report.existing.append(path)
            return False
        path.mkdir(parents=True, exist_ok=True)
        report.created.append(path)
        return True
    except OSError as exc:
        logger.warning("Could not create %s: %s", path, exc)
        report.failed[str(path)] = str(exc)
        return False


def ensure_workspace(store: ConfigStore, *, write_readmes: bool = True) -> ScaffoldReport:
    """Create every hub folder and shared default directory that is missing."""
    report = ScaffoldReport()

    for hub in store.items("workspaces", "hubs"):
        raw_path = hub.get("path")
        if not raw_path:
            continue
        hub_root = Path(raw_path)
        newly_created = _ensure(hub_root, report)
        for sub in hub.get("folders") or []:
            _ensure(hub_root / sub, report)
        if newly_created and write_readmes:
            blurb = HUB_BLURBS.get(str(hub.get("name", "")).lower())
            if blurb:
                try:
                    (hub_root / README_NAME).write_text(blurb, encoding="utf-8")
                except OSError:
                    pass

    for key in (
        "scripts_dir",
        "python_projects_dir",
        "flutter_projects_dir",
        "reports_root",
        "data_dir",
    ):
        value = store.value("core", f"defaults.{key}")
        if value:
            _ensure(Path(value), report)

    for paths in (store.get("media").get("libraries") or {}).values():
        for entry in paths or []:
            _ensure(Path(entry), report)

    return report
