"""Running things the config file points at.

Both ``scripts.json`` (chat-built commands) and ``reports.json`` (report
generators) describe *something to run* in the same shape, so they share one
executor instead of each growing its own subprocess handling.

Two Windows-specific details that matter in practice:

* ``CREATE_NO_WINDOW`` - without it, every script launch flashes a console
  window over whatever the user is doing.
* ``stdin=DEVNULL`` - several of the real report generators call ``input()``
  when an argument is missing. With an inherited stdin they would block
  forever inside a worker thread with no way to answer; with DEVNULL the
  ``input()`` raises ``EOFError`` immediately and we surface a real error
  instead of a hang.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger("assistant.runner")

OutputSink = Callable[[str], None]

#: Where a project-local virtualenv usually lives, in preference order.
VENV_DIRS = ("venv", ".venv", "env", ".env")

RUN_KINDS = ("python", "venv_python", "exe", "shell", "open", "url", "folder")


class RunnableError(RuntimeError):
    """A runnable is misconfigured (missing file, unknown kind, ...)."""


@dataclass
class Runnable:
    """One executable thing, as described by a config entry."""

    name: str
    target: str = ""
    run_as: str = "python"
    working_directory: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout: int | None = 1800
    description: str = ""

    @classmethod
    def from_entry(cls, entry: dict) -> "Runnable":
        return cls(
            name=str(entry.get("name") or entry.get("target") or "command"),
            target=str(entry.get("target") or ""),
            run_as=str(entry.get("run_as") or "python").strip().lower(),
            working_directory=entry.get("working_directory") or None,
            args=[str(a) for a in (entry.get("args") or [])],
            env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            timeout=entry.get("timeout", 1800),
            description=str(entry.get("description") or ""),
        )

    # -- resolution -------------------------------------------------------
    @property
    def cwd(self) -> Path | None:
        return Path(self.working_directory) if self.working_directory else None

    def resolved_target(self) -> Path:
        """``target`` made absolute against ``working_directory``."""
        target = Path(os.path.expandvars(self.target)).expanduser()
        if target.is_absolute() or self.cwd is None:
            return target
        return (self.cwd / target).resolve()


@dataclass
class RunResult:
    runnable: str
    returncode: int
    stdout: str
    stderr: str
    duration: float
    command: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, lines: int = 12) -> str:
        """Last few lines of output - what a chat bubble can actually show."""
        blob = (self.stdout or "").strip() or (self.stderr or "").strip()
        if not blob:
            return ""
        parts = blob.splitlines()
        return "\n".join(parts[-lines:])


def find_venv_python(working_dir: Path | str | None) -> Path | None:
    """Locate a project-local virtualenv interpreter, if there is one.

    Every one of the user's report generators ships its own ``venv/`` with
    its own pinned pandas/openpyxl. Running them with the assistant's
    interpreter would either fail on a missing import or, worse, succeed
    against a different library version than the one they were tested with.
    """
    if working_dir is None:
        return None
    base = Path(working_dir)
    if not base.is_dir():
        return None
    for name in VENV_DIRS:
        candidate = base / name / "Scripts" / "python.exe"
        if candidate.is_file():
            return candidate
        posix = base / name / "bin" / "python"
        if posix.is_file():
            return posix
    return None


def build_command(runnable: Runnable) -> tuple[list[str] | str, bool]:
    """Return ``(command, use_shell)`` for a runnable.

    Raises :class:`RunnableError` for anything that cannot be run, so the
    caller reports a clear reason rather than a bare ``FileNotFoundError``.
    """
    kind = runnable.run_as
    if kind not in RUN_KINDS:
        raise RunnableError(
            f"'{runnable.name}' has run_as='{kind}', which I don't know. "
            f"Use one of: {', '.join(RUN_KINDS)}."
        )

    if kind == "shell":
        command = runnable.target
        if runnable.args:
            command = " ".join([command, *runnable.args])
        return command, True

    target = runnable.resolved_target()

    if kind == "exe":
        if not target.is_file():
            raise RunnableError(f"'{runnable.name}': executable not found at {target}")
        return [str(target), *runnable.args], False

    if kind in ("python", "venv_python"):
        if not target.is_file():
            raise RunnableError(f"'{runnable.name}': script not found at {target}")
        interpreter = find_venv_python(runnable.working_directory)
        if kind == "venv_python" and interpreter is None:
            raise RunnableError(
                f"'{runnable.name}': no virtualenv found under {runnable.working_directory}. "
                "Create one, or set run_as to 'python' to use the assistant's interpreter."
            )
        interpreter = interpreter or Path(sys.executable)
        return [str(interpreter), str(target), *runnable.args], False

    raise RunnableError(f"'{kind}' is opened, not executed - call open_target() instead.")


def open_target(runnable: Runnable) -> str:
    """Handle the non-process kinds: ``url``, ``open`` (file), ``folder``."""
    kind = runnable.run_as
    if kind == "url":
        webbrowser.open(runnable.target)
        return f"Opened {runnable.target}"
    target = runnable.resolved_target()
    if not target.exists():
        raise RunnableError(f"'{runnable.name}': {target} does not exist")
    os.startfile(str(target))  # noqa: S606 - user-configured path, desktop app
    return f"Opened {target}"


def _popen_kwargs(runnable: Runnable, use_shell: bool) -> dict:
    env = os.environ.copy()
    env.update(runnable.env)
    kwargs: dict = {
        "cwd": str(runnable.cwd) if runnable.cwd and runnable.cwd.is_dir() else None,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "shell": use_shell,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def execute(
    runnable: Runnable,
    on_output: OutputSink | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RunResult:
    """Run ``runnable`` to completion, streaming its output to ``on_output``.

    Runs on a worker thread; never call this from the GUI thread.
    """
    command, use_shell = build_command(runnable)
    printable = command if isinstance(command, str) else subprocess.list2cmdline(command)
    logger.info("Running %s: %s", runnable.name, printable)

    started = time.monotonic()
    collected: list[str] = []
    proc = subprocess.Popen(command, **_popen_kwargs(runnable, use_shell))  # noqa: S603
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            collected.append(line)
            if on_output and line.strip():
                on_output(line)
            if cancelled and cancelled():
                proc.terminate()
                collected.append("[cancelled]")
                break
        proc.wait(timeout=runnable.timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        collected.append(f"[timed out after {runnable.timeout}s]")
    finally:
        if proc.stdout is not None:
            proc.stdout.close()

    return RunResult(
        runnable=runnable.name,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout="\n".join(collected),
        stderr="",
        duration=time.monotonic() - started,
        command=printable,
    )


def split_args(text: str) -> list[str]:
    """Parse a user-typed argument string, tolerating Windows paths.

    ``shlex`` in POSIX mode eats backslashes, which mangles every Windows
    path; POSIX mode off keeps ``D:\\data\\file.csv`` intact while still
    honouring quotes around paths with spaces.
    """
    if not text.strip():
        return []
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return text.split()


def snapshot(
    dirs: Iterable[Path | str],
    extensions: Iterable[str],
    prune: Iterable[str] = (),
) -> dict[Path, float]:
    """Map of ``path -> mtime`` for matching files, used to spot new output.

    Prunes whole directory trees rather than walking and filtering them: a
    project's ``venv/`` holds tens of thousands of files, and this runs twice
    per report - once before the generator and once after - so descending
    into it would add seconds of pure waste to every single run.
    """
    wanted = {e.lower() for e in extensions}
    skip = set(prune)
    seen: dict[Path, float] = {}
    for directory in dirs:
        base = Path(directory)
        if not base.is_dir():
            continue
        for root, subdirs, files in os.walk(base):
            subdirs[:] = [d for d in subdirs if d not in skip and not d.startswith(".")]
            for name in files:
                if wanted and Path(name).suffix.lower() not in wanted:
                    continue
                path = Path(root) / name
                try:
                    seen[path.resolve()] = path.stat().st_mtime
                except OSError:
                    continue
    return seen
