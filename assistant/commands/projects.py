"""Creating, registering and opening code projects.

Two things this pack is careful about:

* **Auto-registration.** A project the assistant just created needs no
  questions asked - it knows the name, type and path, so it writes them into
  ``projects.json`` itself (brief §4.2). Only projects it *didn't* create
  need "register project X at Y".
* **Never clobber.** ``create python project foo`` into an existing non-empty
  folder stops and says so rather than scribbling a ``main.py`` over
  someone's work.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from assistant.core.router import CommandRouter, make_command
from assistant.core.runner import execute, Runnable
from assistant.core.selection import Choice, offer
from assistant.store.store import ConfigStore

PY_MAIN = '''"""{name}."""


def main() -> None:
    print("hello from {name}")


if __name__ == "__main__":
    main()
'''

PY_GITIGNORE = """.venv/
venv/
__pycache__/
*.pyc
.pytest_cache/
dist/
build/
"""

#: Scripts that are never the thing you meant by "run this project".
_NOT_ENTRY_POINTS = {"setup.py", "conftest.py", "config.py", "__init__.py"}


def _report_entry_for(path: Path, store: ConfigStore) -> dict | None:
    """The reports.json entry whose working_directory is ``path``, if any."""
    wanted = path.resolve()
    for entry in store.items("reports", "reports"):
        raw = entry.get("working_directory")
        if not raw:
            continue
        try:
            if Path(raw).resolve() == wanted:
                return entry
        except OSError:
            continue
    return None


def _runnables_in(path: Path) -> list[tuple[str, Path]]:
    """What could be run inside a project, best first: exe, then scripts."""
    found: list[tuple[str, Path]] = []
    dist = path / "dist"
    if dist.is_dir():
        for exe in sorted(dist.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True):
            found.append(("exe", exe))
    for base in (path, path / "scripts", path / "src"):
        if not base.is_dir():
            continue
        scripts = [
            p for p in base.glob("*.py")
            if p.is_file() and p.name not in _NOT_ENTRY_POINTS and not p.name.startswith("_")
        ]
        found.extend(("python", p) for p in sorted(scripts, key=lambda p: p.stat().st_size, reverse=True))
    return found


def run_project_at(path: Path, ctx) -> str:
    """Run what's inside a project folder - the exe if it was built.

    "run 3" on a listing should *run* the thing, not drop you at a prompt in
    its folder. A registered report goes through the full report pipeline
    (input file, then output filed into today's folder); anything else runs
    its newest ``dist/*.exe``, falling back to its main script under the
    project's own virtualenv.
    """
    if not path.is_dir():
        return f"{path} is gone - say 'rescan projects' to clean that up."

    entry = _report_entry_for(path, ctx.store)
    if entry is not None:
        from assistant.commands.reportpack import run_with_input

        return run_with_input(entry, ctx)

    options = _runnables_in(path)
    if not options:
        return (
            f"There's nothing obvious to run in {path.name} - no dist\\*.exe and no "
            f"top-level script. Say 'terminal' and the number for a shell there instead."
        )

    if len(options) > 1:
        def _pick(choice, inner_ctx) -> str:
            kind, target = choice.payload
            return _execute(path, kind, Path(target), inner_ctx)

        return offer(
            ctx,
            f"What should I run in {path.name}?",
            [
                Choice(target.name, (kind, str(target)), "built exe" if kind == "exe" else "script")
                for kind, target in options
            ],
            actions={"run": _pick, "open": _pick, "use": _pick},
            default_action="run",
            hint="Say a number to run it.",
        )

    kind, target = options[0]
    return _execute(path, kind, target, ctx)


def _execute(project: Path, kind: str, target: Path, ctx) -> str:
    ctx.progress(f"Running {target.name} in {project.name}...")
    result = execute(
        Runnable(
            name=target.name,
            run_as=kind,
            target=str(target),
            working_directory=str(project),
            timeout=3600,
            console=bool(ctx.store.value("core", "behaviour.show_terminals", True)),
        ),
        on_output=ctx.progress,
    )
    head = (
        f"{target.name} finished in {result.duration:.0f}s."
        if result.ok
        else f"{target.name} exited with code {result.returncode}."
    )
    tail = result.tail()
    return f"{head}\n{tail}" if tail else head


def _dir_for(store: ConfigStore, kind: str) -> Path:
    key = "flutter_projects_dir" if kind == "flutter" else "python_projects_dir"
    return Path(store.value("core", f"defaults.{key}") or (Path.home() / "projects" / kind))


def _register(store: ConfigStore, name: str, kind: str, path: Path) -> None:
    def _mutate(doc: dict) -> None:
        projects = doc.setdefault("projects", [])
        entry = {"name": name.lower(), "type": kind, "path": path.as_posix()}
        for existing in projects:
            if str(existing.get("name", "")).lower() == name.lower():
                existing.update(entry)
                return
        projects.append(entry)

    store.update("projects", _mutate)


def _open_in_editor(path: Path) -> str:
    editor = shutil.which("code") or shutil.which("code.cmd")
    if editor:
        subprocess.Popen([editor, str(path)], shell=True)  # noqa: S603 - resolved from PATH
        return f"Opened {path} in VS Code."
    import os

    os.startfile(str(path))  # noqa: S606
    return f"VS Code isn't on PATH, so I opened {path} in Explorer instead."


def register(router: CommandRouter, store: ConfigStore) -> None:
    # -- one "open <project>" command per registered project ---------------
    def provider():
        commands = []
        for entry in store.items("projects", "projects"):
            name = str(entry.get("name") or "").strip()
            path = entry.get("path")
            if not name or not path:
                continue
            kind = str(entry.get("type") or "project")

            def handler(text, ctx, _path=path, _name=name, _kind=kind):
                target = Path(_path)
                if not target.exists():
                    return (
                        f"'{_name}' is registered at {target}, but that folder is gone. "
                        f"Fix or remove it in config/projects.json."
                    )
                ctx.store.set_value("core", "last_active_path", target.as_posix())
                return _open_in_editor(target)

            commands.append(
                make_command(
                    name=f"open project {name}",
                    triggers=[f"open {name}", f"open project {name}", f"edit {name}", f"work on {name}"],
                    handler=handler,
                    help=f"Opens the {kind} project at {path}.",
                    category="projects",
                    payload={"path": path, "type": kind},
                )
            )
        return commands

    router.add_provider(provider)

    # -- creating ---------------------------------------------------------
    @router.register(
        "create flutter app",
        pattern=r"^\s*create\s+(?:a\s+)?flutter\s+(?:app|project)\s+(?:called\s+|named\s+)?([\w-]+)\s*$",
        help="create flutter app myapp  -  runs 'flutter create' and registers it.",
        category="projects",
        priority=8,
    )
    def cmd_create_flutter(text, ctx):
        name = re.search(r"flutter\s+(?:app|project)\s+(?:called\s+|named\s+)?([\w-]+)", text, re.IGNORECASE).group(1)
        base = _dir_for(ctx.store, "flutter")
        base.mkdir(parents=True, exist_ok=True)
        target = base / name
        if target.exists() and any(target.iterdir()):
            return f"{target} already exists and isn't empty - I won't write over it."
        if not shutil.which("flutter") and not shutil.which("flutter.bat"):
            return "Flutter isn't on PATH, so I can't run 'flutter create'. Install it or add it to PATH."

        ctx.progress(f"Running flutter create {name} in {base}...")
        result = execute(
            Runnable(
                name=f"flutter create {name}",
                run_as="shell",
                target=f"flutter create {name}",
                working_directory=str(base),
                timeout=600,
            ),
            on_output=ctx.progress,
        )
        if not result.ok:
            return f"flutter create failed (exit {result.returncode}):\n{result.tail()}"
        _register(ctx.store, name, "flutter", target)
        ctx.store.set_value("core", "last_active_path", target.as_posix())
        ctx.router.rebuild()
        return f"Created {target} and registered it. Say 'open {name}' any time."

    @router.register(
        "create python project",
        pattern=r"^\s*create\s+(?:a\s+)?python\s+(?:project|app|script folder)\s+(?:called\s+|named\s+)?([\w-]+)(\s+with\s+venv)?\s*$",
        help="create python project myapp [with venv]  -  scaffolds it and registers it.",
        category="projects",
        priority=8,
    )
    def cmd_create_python(text, ctx):
        match = re.search(
            r"python\s+(?:project|app|script folder)\s+(?:called\s+|named\s+)?([\w-]+)(\s+with\s+venv)?",
            text,
            re.IGNORECASE,
        )
        name, wants_venv = match.group(1), bool(match.group(2))
        base = _dir_for(ctx.store, "python")
        base.mkdir(parents=True, exist_ok=True)
        target = base / name
        if target.exists() and any(target.iterdir()):
            return f"{target} already exists and isn't empty - I won't write over it."

        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text(PY_MAIN.format(name=name), encoding="utf-8")
        (target / "requirements.txt").write_text("", encoding="utf-8")
        (target / ".gitignore").write_text(PY_GITIGNORE, encoding="utf-8")
        (target / "README.md").write_text(f"# {name}\n", encoding="utf-8")

        note = ""
        if wants_venv:
            ctx.progress("Creating the virtualenv (this takes a few seconds)...")
            result = execute(
                Runnable(
                    name="venv",
                    run_as="shell",
                    target=f'"{sys.executable}" -m venv venv',
                    working_directory=str(target),
                    timeout=300,
                ),
                on_output=ctx.progress,
            )
            note = "\nVirtualenv ready at venv/." if result.ok else f"\n(venv creation failed: {result.tail(3)})"

        _register(ctx.store, name, "python", target)
        ctx.store.set_value("core", "last_active_path", target.as_posix())
        ctx.router.rebuild()
        return f"Created {target} with main.py, requirements.txt and .gitignore, and registered it.{note}\nSay 'open {name}' to jump in."

    # -- registering / listing --------------------------------------------
    @router.register(
        "register project",
        pattern=r"^\s*register\s+(?:the\s+)?(?:(\w+)\s+)?project\s+(.+?)\s+(?:at|in)\s+(.+?)\s*$",
        help="register python project reportgen at D:/scripts/reportgen",
        category="projects",
    )
    def cmd_register_project(text, ctx):
        match = re.search(r"register\s+(?:the\s+)?(?:(\w+)\s+)?project\s+(.+?)\s+(?:at|in)\s+(.+?)\s*$", text, re.IGNORECASE)
        kind = (match.group(1) or "python").lower()
        name = match.group(2).strip().lower()
        path = Path(match.group(3).strip().strip("\"'"))
        if not path.is_dir():
            return f"{path} isn't a folder I can see."
        _register(ctx.store, name, kind, path)
        ctx.router.rebuild()
        return f"Registered '{name}' ({kind}) at {path}. Say 'open {name}'."

    @router.register(
        "list projects",
        pattern=r"^\s*(?:list|show|what)\s+(?:my\s+)?(python|flutter|wordpress|all)?\s*projects\s*$",
        keywords=(
            "list projects", "my projects", "what projects", "show projects",
            "list python projects", "list flutter projects", "list wordpress projects",
        ),
        help="list projects / list python projects  -  numbered, then say '3' or 'vs code 3'.",
        category="projects",
        instant=True,
    )
    def cmd_list_projects(text, ctx):
        kind_match = re.search(r"(python|flutter|wordpress)", text, re.IGNORECASE)
        wanted = kind_match.group(1).lower() if kind_match else None

        entries = [
            e for e in ctx.store.items("projects", "projects")
            if not wanted or str(e.get("type", "")).lower() == wanted
        ]
        if not entries:
            which = f"{wanted} " if wanted else ""
            return (
                f"No {which}projects registered. Try 'rescan projects', or "
                "'register python project x at D:/path'."
            )

        def _open(choice, inner_ctx) -> str:
            path = Path(choice.payload)
            if not path.is_dir():
                return f"{path} is gone - say 'rescan projects' to clean that up."
            inner_ctx.store.set_value("core", "last_active_path", path.as_posix())
            return _open_in_editor(path)

        def _folder(choice, inner_ctx) -> str:
            path = Path(choice.payload)
            os.startfile(str(path))  # noqa: S606
            return f"Opened {path} in Explorer."

        def _run(choice, inner_ctx) -> str:
            return run_project_at(Path(choice.payload), inner_ctx)

        def _terminal(choice, inner_ctx) -> str:
            path = Path(choice.payload)
            subprocess.Popen(["cmd.exe", "/c", "start", "cmd.exe", "/k", f"cd /d {path}"], shell=False)  # noqa: S603
            return f"Opened a terminal in {path.name}."

        choices = [
            Choice(
                str(entry.get("name")),
                str(entry.get("path")),
                f"({entry.get('type')})" + ("" if Path(str(entry.get("path"))).is_dir() else "  [missing!]"),
            )
            for entry in sorted(entries, key=lambda e: str(e.get("name", "")))
        ]
        label = f"{len(choices)} {wanted or 'registered'} project(s):"
        return offer(
            ctx,
            label,
            choices,
            actions={
                "open": _open, "code": _open, "use": _open,
                "folder": _folder, "run": _run, "terminal": _terminal,
            },
            default_action="open",
            hint=(
                "Say a number to open it in VS Code, 'run 3' to actually run it, "
                "'folder 3' for Explorer, 'terminal 3' for a shell there."
            ),
        )

    @router.register(
        "rescan projects",
        keywords=("rescan projects", "refresh projects", "find my projects", "scan projects"),
        help="Re-scans your project folders and drops anything that moved.",
        category="projects",
    )
    def cmd_rescan_projects(text, ctx):
        from assistant.store.bootstrap import seed_from_machine

        summary = seed_from_machine(ctx.store).summary()
        ctx.router.rebuild()
        return summary

    @router.register(
        "run project",
        pattern=r"^\s*(?:flutter\s+run|run\s+flutter)\s+(.+?)\s*$",
        help="flutter run myapp  -  runs a registered Flutter project.",
        category="projects",
        priority=8,
    )
    def cmd_flutter_run(text, ctx):
        name = re.search(r"(?:flutter\s+run|run\s+flutter)\s+(.+?)\s*$", text, re.IGNORECASE).group(1).strip().lower()
        for entry in ctx.store.items("projects", "projects"):
            if str(entry.get("name", "")).lower() == name:
                ctx.progress(f"flutter run in {entry['path']}...")
                result = execute(
                    Runnable(
                        name=f"flutter run {name}",
                        run_as="shell",
                        target="flutter run",
                        working_directory=str(entry["path"]),
                        timeout=1800,
                    ),
                    on_output=ctx.progress,
                )
                return f"flutter run exited with {result.returncode}.\n{result.tail()}"
        return f"I don't have a project called '{name}'. Say 'list projects'."
