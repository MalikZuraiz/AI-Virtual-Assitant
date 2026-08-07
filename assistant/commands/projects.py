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

import re
import shutil
import subprocess
import sys
from pathlib import Path

from assistant.core.router import CommandRouter, make_command
from assistant.core.runner import execute, Runnable
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
        keywords=("list projects", "my projects", "what projects", "show projects"),
        help="Lists every registered project.",
        category="projects",
        instant=True,
    )
    def cmd_list_projects(text, ctx):
        entries = ctx.store.items("projects", "projects")
        if not entries:
            return "No projects registered yet. Try 'register python project x at D:/path'."
        lines = [f"{len(entries)} registered project(s):"]
        for entry in sorted(entries, key=lambda e: str(e.get("name", ""))):
            exists = "" if Path(str(entry.get("path"))).exists() else "   [missing!]"
            lines.append(f"  - {entry.get('name')} ({entry.get('type')}): {entry.get('path')}{exists}")
        return "\n".join(lines)

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
