"""First-run discovery: turn what is actually on this machine into config.

Keeping this separate from :mod:`assistant.store.defaults` is the whole
point. ``defaults`` stays portable and machine-agnostic; *this* module looks
at the real drive once and writes what it finds into the JSON files, which
are then the source of truth and freely hand-editable.

It is strictly additive and idempotent: an entry whose name already exists is
left alone, so re-running it after the user has renamed or retuned something
never clobbers their edits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from assistant.store.store import ConfigStore

logger = logging.getLogger("assistant.bootstrap")

#: Folder names that commonly hold a collection of report/script projects.
PROJECT_HUB_HINTS = ("python reporting", "reporting", "scripts", "python scripts", "automation")

#: Skipped when looking for a project's main script.
SKIP_SCRIPTS = {"setup.py", "__init__.py", "conftest.py"}

SKIP_DIRS = {"venv", ".venv", "env", "build", "__pycache__", ".git", "logs", "dist", "files", "output"}


@dataclass
class SeedReport:
    reports: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines: list[str] = []
        if self.reports:
            lines.append(f"Registered {len(self.reports)} report generator(s): {', '.join(self.reports)}")
        if self.projects:
            lines.append(f"Registered {len(self.projects)} project(s): {', '.join(self.projects)}")
        if self.media:
            lines.append(f"Registered media libraries: {', '.join(self.media)}")
        lines.extend(self.notes)
        return "\n".join(lines) if lines else "Nothing new to register."


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _main_script(project: Path) -> Path | None:
    """The most plausible entry-point ``.py`` for a project.

    Picks the largest candidate script, which in practice is the generator
    itself rather than a helper - these are single-file 40-80KB scripts with
    small utilities (``config.py``) alongside them. Looks at the top level
    first, then in a ``scripts/`` subfolder, since not every project keeps
    its entry point at the root.
    """
    for base in (project, project / "scripts", project / "src"):
        if not base.is_dir():
            continue
        candidates = [
            p for p in base.glob("*.py")
            if p.is_file() and p.name not in SKIP_SCRIPTS and not p.name.startswith("_")
        ]
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_size)
    return None


def _built_exe(project: Path) -> Path | None:
    """A PyInstaller build under ``dist/``, preferred over the raw script.

    A frozen exe carries its own dependencies, so it runs the same whether or
    not the project's venv is intact - fewer ways for a scheduled report to
    fail silently months from now.
    """
    dist = project / "dist"
    if not dist.is_dir():
        return None
    exes = sorted(dist.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return exes[0] if exes else None


def _triggers_for(name: str) -> list[str]:
    low = " ".join(name.lower().split())
    variants = {
        f"generate {low}",
        f"run {low}",
        f"make {low}",
        f"create {low}",
        low,
    }
    if not low.endswith("report"):
        variants.add(f"generate {low} report")
    return sorted(variants, key=len, reverse=True)


def discover_report_projects(hub: Path) -> list[dict]:
    """Build ``reports.json`` entries for every project folder under ``hub``."""
    if not hub.is_dir():
        return []
    entries: list[dict] = []
    for project in sorted(p for p in hub.iterdir() if p.is_dir()):
        if project.name.lower() in SKIP_DIRS or project.name.startswith("."):
            continue
        exe = _built_exe(project)
        script = _main_script(project)
        if exe is None and script is None:
            continue
        name = " ".join(project.name.split()).lower()
        if exe is not None:
            run_as, target = "exe", exe.relative_to(project).as_posix()
        else:
            run_as, target = "python", script.relative_to(project).as_posix()  # type: ignore[union-attr]
        entries.append(
            {
                "name": name,
                "trigger": _triggers_for(name),
                "working_directory": project.as_posix(),
                "run_as": run_as,
                "target": target,
                "args": [],
                "collect_from": ["dist", "output", "."],
                "route_output": "day_folder",
                "collect_mode": "move",
                "on_conflict": "version",
                "description": f"Report generator discovered in {project.name}.",
                "alternate": (
                    {"run_as": "python", "target": script.relative_to(project).as_posix()}
                    if exe is not None and script is not None
                    else None
                ),
            }
        )
    return entries


def discover_project_hubs(root: Path) -> list[Path]:
    """Immediate children of ``root`` that look like a hub of code projects."""
    if not root.is_dir():
        return []
    hubs: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        low = child.name.lower()
        if any(hint in low for hint in PROJECT_HUB_HINTS):
            hubs.append(child)
    return hubs


def discover_media_dirs(root: Path) -> dict[str, list[str]]:
    """Existing top-level media folders (``D:/Movies``, ``D:/Music``, ...)."""
    found: dict[str, list[str]] = {}
    if not root.is_dir():
        return found
    wanted = {
        "movies": ("movies", "movie", "films"),
        "series": ("series", "tv shows", "shows", "anime"),
        "music": ("music", "songs"),
        "clips": ("clips", "funny clips", "videos"),
    }
    for child in root.iterdir():
        if not child.is_dir():
            continue
        low = child.name.lower()
        for key, names in wanted.items():
            if low in names:
                found.setdefault(key, []).append(child.as_posix())
    return found


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _existing_names(entries: list[dict]) -> set[str]:
    return {str(e.get("name", "")).strip().lower() for e in entries}


def seed_from_machine(store: ConfigStore) -> SeedReport:
    """Discover real folders once and write them into the config files."""
    report = SeedReport()
    root = Path(store.value("core", "defaults.workspace_root", "."))

    # -- report generators ------------------------------------------------
    discovered: list[dict] = []
    for hub in discover_project_hubs(root):
        discovered.extend(discover_report_projects(hub))
    scripts_dir = store.value("core", "defaults.scripts_dir")
    if scripts_dir:
        discovered.extend(discover_report_projects(Path(scripts_dir)))

    if discovered:
        existing = _existing_names(store.items("reports", "reports"))
        fresh = [e for e in discovered if e["name"] not in existing]
        if fresh:
            def _add_reports(doc: dict) -> None:
                doc.setdefault("reports", []).extend(fresh)

            store.update("reports", _add_reports)
            report.reports = [e["name"] for e in fresh]

    # -- where generated reports get filed --------------------------------
    reports_root = root / "Reports"
    if reports_root.is_dir():
        store.set_value("reports", "output_root", reports_root.as_posix())
        store.set_value("core", "defaults.reports_root", reports_root.as_posix())
        report.notes.append(f"Reports will be filed under {reports_root.as_posix()}/<today>.")

    # -- projects ---------------------------------------------------------
    project_entries: list[dict] = []
    for entry in discovered:
        project_entries.append(
            {
                "name": entry["name"],
                "type": "python",
                "path": entry["working_directory"],
            }
        )
    if project_entries:
        existing = _existing_names(store.items("projects", "projects"))
        fresh_projects = [p for p in project_entries if p["name"] not in existing]
        if fresh_projects:
            def _add_projects(doc: dict) -> None:
                doc.setdefault("projects", []).extend(fresh_projects)

            store.update("projects", _add_projects)
            report.projects = [p["name"] for p in fresh_projects]

    # -- media libraries --------------------------------------------------
    found_media = discover_media_dirs(root)
    if found_media:
        def _merge_media(doc: dict) -> None:
            libraries = doc.setdefault("libraries", {})
            for key, paths in found_media.items():
                current = libraries.setdefault(key, [])
                for path in paths:
                    if path not in current:
                        current.insert(0, path)

        store.update("media", _merge_media)
        report.media = sorted(found_media)

    # -- raw data folder --------------------------------------------------
    for child in root.iterdir() if root.is_dir() else []:
        if child.is_dir() and "data extraction" in child.name.lower():
            store.set_value("core", "defaults.data_dir", child.as_posix())
            report.notes.append(f"Raw data folder set to {child.as_posix()}.")
            break

    return report
