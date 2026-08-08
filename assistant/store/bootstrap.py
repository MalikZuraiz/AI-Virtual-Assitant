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
import re
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


def detect_input_spec(project: Path, script: Path | None) -> dict:
    """Work out how a generator wants its raw data handed to it.

    Read off the script's own ``argparse`` setup rather than maintained by
    hand, so a generator added next month is configured correctly without
    anyone editing this file. Falls back to a plain positional path, which is
    what most of these scripts take.
    """
    name = " ".join(project.name.split()).lower()
    if "bot" in name:
        # The PED bot scrapes its own data; there is nothing to feed it.
        return {"mode": "none"}

    source = ""
    if script is not None and script.is_file():
        try:
            source = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            source = ""

    spec: dict = {"mode": "arg", "extensions": [".csv"], "stage_dir": "files"}

    if re.search(r"add_argument\(\s*[\"']--csv[\"']", source):
        spec.update({"mode": "flag", "flag": "--csv"})
    elif re.search(r"add_argument\(\s*[\"']--input[\"']", source):
        spec.update({"mode": "flag", "flag": "--input"})
    elif re.search(r"nargs\s*=\s*[\"']\*[\"']", source):
        # Drag-and-drop style: several files, all positional.
        spec.update({"mode": "arg", "count": 4, "extensions": [".csv", ".xlsx"]})
    elif re.search(r"COMPLAINTS_CSV|ATTENDANCE_CSV|cfg\.\w+_CSV", source):
        # Reads fixed filenames out of its own folder - just stage into it.
        spec.update({"mode": "files_dir", "extensions": [".csv", ".xlsx"]})

    if re.search(r"add_argument\(\s*[\"']--from-date[\"']", source):
        spec["date_flags"] = {"from": "--from-date", "to": "--to-date"}

    if re.search(r"\.xlsx|read_excel", source) and ".xlsx" not in spec["extensions"]:
        spec["extensions"] = [*spec["extensions"], ".xlsx"]
    return spec


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


#: Project folders that are never report generators. The PED bot is an
#: interactive scraper the user drives himself - registering it as a report
#: only ever meant the assistant tried to run something it should not. It is
#: still discovered as a *project*, so "open python bot" works.
NOT_REPORTS_HINTS = ("bot",)


def _is_not_a_report(name: str) -> bool:
    return any(hint in name.lower() for hint in NOT_REPORTS_HINTS)


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
        if _is_not_a_report(name):
            continue
        input_spec = detect_input_spec(project, script)

        # Always run the frozen exe when one exists: it carries its own
        # dependencies, so it behaves the same whether or not the project's
        # venv is intact. Projects that were never built fall back to the
        # .py - and then via the project's own venv, not ours.
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
                "input": input_spec,
                "collect_from": ["dist", "output", "."],
                "route_output": "day_folder",
                "collect_mode": "move",
                "on_conflict": "version",
                "description": f"Report generator in {project.name}.",
                "alternate": (
                    {"run_as": "python", "target": script.relative_to(project).as_posix()}
                    if exe is not None and script is not None
                    else None
                ),
            }
        )
    return entries


def discover_project_hubs(root: Path, extra_roots: list[Path] | None = None) -> list[Path]:
    """Folders that look like a hub of code/report projects.

    Searches one level below ``root`` *and* one level below anything in
    ``extra_roots`` (typically the Office hub), because reorganising a drive
    usually means moving ``Scripts/`` inside a parent folder rather than
    leaving it at the drive root.
    """
    hubs: list[Path] = []
    for base in [root, *(extra_roots or [])]:
        if not base or not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir():
                continue
            low = child.name.lower()
            if any(hint in low for hint in PROJECT_HUB_HINTS) and child not in hubs:
                hubs.append(child)
    return hubs


def _first_existing(*candidates: Path | str | None) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir():
            return path
    return None


def relocate_roots(store: ConfigStore) -> list[str]:
    """Re-point ``core.json`` at where things actually are right now.

    The user reorganises their drive; config should follow rather than
    leaving the assistant pointing at folders that moved. Only ever writes a
    path that exists, so a missing folder never silently blanks a setting.
    """
    notes: list[str] = []
    root = Path(store.value("core", "defaults.workspace_root", "D:/"))
    office = _first_existing(
        store.value("core", "defaults.office_root"), root / "Office", root / "Work"
    )
    entertainment = _first_existing(
        store.value("core", "defaults.entertainment_root"),
        root / "Entertainment",
        root / "Media",
    )

    updates: dict[str, Path | None] = {
        "office_root": office,
        "entertainment_root": entertainment,
        "scripts_dir": _first_existing(
            office / "Scripts" if office else None,
            root / "Python Reporting",
            store.value("core", "defaults.scripts_dir"),
        ),
        "reports_root": _first_existing(
            office / "Reports" if office else None,
            root / "Reports",
            store.value("core", "defaults.reports_root"),
        ),
        "python_projects_dir": _first_existing(
            office / "Projects" / "Python" if office else None,
            store.value("core", "defaults.python_projects_dir"),
        ),
        "flutter_projects_dir": _first_existing(
            office / "Projects" / "Flutter" if office else None,
            store.value("core", "defaults.flutter_projects_dir"),
        ),
        "wordpress_projects_dir": _first_existing(
            office / "Projects" / "WordPress" if office else None,
        ),
        "documents_dir": _first_existing(
            office / "Documents" if office else None,
            store.value("core", "defaults.documents_dir"),
            Path.home() / "Documents",
        ),
    }

    data_dir = _first_existing(
        *[
            child
            for base in (office, root)
            if base and base.is_dir()
            for child in base.iterdir()
            if child.is_dir() and "data extraction" in child.name.lower()
        ],
        office / "Data" if office else None,
        store.value("core", "defaults.data_dir"),
    )
    updates["data_dir"] = data_dir

    for key, path in updates.items():
        if path is None:
            continue
        current = store.value("core", f"defaults.{key}")
        if current != path.as_posix():
            store.set_value("core", f"defaults.{key}", path.as_posix())
            notes.append(f"{key} -> {path.as_posix()}")
    return notes


def prune_missing(store: ConfigStore) -> list[str]:
    """Drop report/project entries whose folder no longer exists."""
    removed: list[str] = []

    def _prune(doc: dict, key: str, path_key: str) -> None:
        kept = []
        for entry in doc.get(key, []):
            location = entry.get(path_key)
            if location and not Path(location).is_dir():
                removed.append(str(entry.get("name", location)))
                continue
            kept.append(entry)
        doc[key] = kept

    store.update("reports", lambda doc: _prune(doc, "reports", "working_directory"))
    store.update("projects", lambda doc: _prune(doc, "projects", "path"))
    return removed


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


def seed_from_machine(store: ConfigStore, *, prune: bool = True) -> SeedReport:
    """Point config at the real folders, drop what moved, discover what's new.

    Safe to run any time - it is how you tell the assistant "I reorganised my
    drive". Existing entries that still resolve keep whatever you customised
    on them; only entries whose folder has vanished are removed.
    """
    report = SeedReport()
    report.notes.extend(f"Moved: {note}" for note in relocate_roots(store))

    root = Path(store.value("core", "defaults.workspace_root", "."))
    office = store.value("core", "defaults.office_root")
    scripts_dir = store.value("core", "defaults.scripts_dir")

    if prune:
        gone = prune_missing(store)
        if gone:
            report.notes.append(f"Removed {len(gone)} entry/entries whose folder is gone: {', '.join(gone)}")

    # -- report generators ------------------------------------------------
    hubs = discover_project_hubs(root, [Path(office)] if office else None)
    if scripts_dir and Path(scripts_dir) not in hubs:
        hubs.append(Path(scripts_dir))

    discovered: list[dict] = []
    for hub in hubs:
        discovered.extend(discover_report_projects(hub))

    by_name = {e["name"]: e for e in discovered}
    existing = _existing_names(store.items("reports", "reports"))
    fresh = [e for e in discovered if e["name"] not in existing]

    def _update_reports(doc: dict) -> int:
        backfilled = 0
        for entry in doc.get("reports", []):
            # Entries written by an older version have no input spec. Fill it
            # in without touching anything the user may have customised.
            if entry.get("input"):
                continue
            match = by_name.get(str(entry.get("name", "")).lower())
            if match:
                entry["input"] = match["input"]
                entry.setdefault("run_as", match["run_as"])
                if match["input"].get("mode") == "none":
                    entry["run_as"] = match["run_as"]
                    entry["target"] = match["target"]
                backfilled += 1
        if fresh:
            doc.setdefault("reports", []).extend(fresh)
        return backfilled

    backfilled = store.update("reports", _update_reports)
    report.reports = [e["name"] for e in fresh]
    if backfilled:
        report.notes.append(f"Filled in the input settings for {backfilled} existing report(s).")

    # -- where generated reports get filed --------------------------------
    reports_root = store.value("core", "defaults.reports_root")
    if reports_root:
        store.set_value("reports", "output_root", reports_root)
        report.notes.append(f"Reports file into {reports_root}/<today>.")

    # -- projects ---------------------------------------------------------
    project_entries = [
        {"name": entry["name"], "type": "python", "path": entry["working_directory"]}
        for entry in discovered
    ]
    for kind, key in (
        ("python", "python_projects_dir"),
        ("flutter", "flutter_projects_dir"),
        ("wordpress", "wordpress_projects_dir"),
    ):
        base = store.value("core", f"defaults.{key}")
        if not base or not Path(base).is_dir():
            continue
        for child in Path(base).iterdir():
            if child.is_dir() and not child.name.startswith("."):
                project_entries.append(
                    {"name": " ".join(child.name.split()).lower(), "type": kind, "path": child.as_posix()}
                )

    if project_entries:
        existing = _existing_names(store.items("projects", "projects"))
        fresh_projects = []
        for candidate in project_entries:
            if candidate["name"] in existing:
                continue
            existing.add(candidate["name"])
            fresh_projects.append(candidate)
        if fresh_projects:
            def _add_projects(doc: dict) -> None:
                doc.setdefault("projects", []).extend(fresh_projects)

            store.update("projects", _add_projects)
            report.projects = [p["name"] for p in fresh_projects]

    # -- media libraries --------------------------------------------------
    entertainment = store.value("core", "defaults.entertainment_root")
    found_media: dict[str, list[str]] = {}
    for base in (Path(entertainment) if entertainment else None, root):
        if base is None:
            continue
        for key, paths in discover_media_dirs(base).items():
            found_media.setdefault(key, []).extend(p for p in paths if p not in found_media.get(key, []))

    if found_media:
        def _merge_media(doc: dict) -> None:
            libraries = doc.setdefault("libraries", {})
            for key, paths in found_media.items():
                # Drop library folders that no longer exist, then put the
                # freshly-found ones first.
                current = [p for p in libraries.get(key, []) if Path(p).is_dir()]
                for path in reversed(paths):
                    if path in current:
                        current.remove(path)
                    current.insert(0, path)
                libraries[key] = current

        store.update("media", _merge_media)
        report.media = sorted(found_media)

    return report
