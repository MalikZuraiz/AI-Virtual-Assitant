"""Custom commands: everything the user teaches the assistant through chat.

``config/scripts.json`` is the one config file the user is *not* expected to
hand-write (brief §4.3) - creating a command needs a directory, a thing to
run, the file inside it and trigger phrases, which is a conversation, not a
sentence. So this pack owns the "add command" wizard, and turns every entry
it writes into a live command immediately.

The wizard shape here - ask one thing at a time, show a summary, confirm,
then write - is the template for any future multi-part command type.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from assistant.core.conversation import Retry, Step, Wizard
from assistant.core.router import CommandRouter, make_command
from assistant.core.runner import Runnable, RunnableError, execute, open_target
from assistant.store.store import ConfigStore
from assistant.workspace.reports import run_report

#: What the wizard offers at "what should this do?".
ACTIONS = {
    "1": ("python", "Run a Python script"),
    "2": ("exe", "Run a program (.exe / .bat)"),
    "3": ("open", "Open a folder or file"),
    "4": ("code", "Open a project in VS Code"),
    "5": ("url", "Open a link"),
    "6": ("shell", "Run a shell command I'll type out"),
}

RUNNABLE_KINDS = {"python", "venv_python", "exe", "shell"}
EXECUTABLE_SUFFIXES = {".py", ".exe", ".bat", ".cmd", ".ps1"}


def _list_files(directory: Path, limit: int = 25) -> list[Path]:
    if not directory.is_dir():
        return []
    files = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in EXECUTABLE_SUFFIXES
    ]
    files.sort(key=lambda p: (p.suffix.lower() != ".py", p.name.lower()))
    return files[:limit]


def _run_entry(entry: dict, text: str, ctx) -> str:
    """Execute one ``scripts.json`` entry and describe what happened."""
    runnable = Runnable.from_entry(entry)

    if runnable.run_as in ("open", "url", "folder"):
        return open_target(runnable)

    if entry.get("route_output"):
        result = run_report(
            entry,
            output_root=ctx.store.value("reports", "output_root")
            or ctx.store.value("core", "defaults.reports_root"),
            day_format=ctx.store.value("core", "day_folder.format", "{d} {month}"),
            on_output=ctx.progress,
        )
        return result.summary()

    # An entry that says console:false means it - launching VS Code does not
    # want a terminal flashing up behind it.
    if "console" not in entry and ctx.store.value("core", "behaviour.show_terminals", True):
        runnable.console = True
    ctx.progress(f"Running '{runnable.name}'...")
    result = execute(runnable, on_output=ctx.progress)
    after = entry.get("after") or {}
    lines = []
    if result.ok:
        lines.append(f"'{runnable.name}' finished in {result.duration:.0f}s.")
    else:
        lines.append(f"'{runnable.name}' exited with code {result.returncode}.")
    tail = result.tail()
    if tail:
        lines.append(tail)
    target = after.get("open")
    if target and Path(target).exists():
        os.startfile(str(target))  # noqa: S606
        lines.append(f"Opened {target}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The "add command" wizard
# ---------------------------------------------------------------------------


def _build_wizard(store: ConfigStore, router: CommandRouter) -> Wizard:
    last_path = store.value("core", "last_active_path")

    def _parse_directory(answer: str, data: dict) -> str:
        low = answer.lower().strip(" .!?")
        if last_path and low in {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "that one", "use it"}:
            return str(last_path)
        candidate = Path(answer.strip().strip("\"'")).expanduser()
        if candidate.is_file():
            data["_prefilled_file"] = candidate.name
            candidate = candidate.parent
        if not candidate.is_dir():
            raise Retry(f"I can't find the folder '{answer.strip()}'.")
        return candidate.as_posix()

    def _directory_prompt(data: dict) -> str:
        if last_path:
            return (
                f"Which folder is this command for?\n"
                f"  - press enter / say 'yes' to use the one you saved: {last_path}\n"
                f"  - or paste another path"
            )
        return "Which folder is this command for? (paste the path)"

    def _parse_action(answer: str, data: dict) -> str:
        low = answer.lower().strip(" .!?")
        if low in ACTIONS:
            return ACTIONS[low][0]
        for kind, label in ACTIONS.values():
            if low == kind or low in label.lower():
                return kind
        raise Retry("Pick one of the numbers above (1-6).")

    def _action_prompt(data: dict) -> str:
        options = "\n".join(f"  {key}. {label}" for key, (_kind, label) in ACTIONS.items())
        return f"What should this command actually do?\n{options}"

    def _needs_file(data: dict) -> bool:
        return data.get("action") in ("python", "exe")

    def _file_prompt(data: dict) -> str:
        directory = Path(data["directory"])
        files = _list_files(directory)
        data["_files"] = [f.name for f in files]
        if not files:
            return f"No .py/.exe/.bat files directly inside {directory}. Type a filename or relative path:"
        listing = "\n".join(f"  {i + 1}. {f.name}" for i, f in enumerate(files))
        prefilled = data.get("_prefilled_file")
        head = f"Which file should it run?\n{listing}"
        if prefilled:
            head += f"\n(You pointed me at {prefilled} - say 'yes' to use that.)"
        return head + "\nSay a number or the filename."

    def _parse_file(answer: str, data: dict) -> str:
        answer = answer.strip().strip("\"'")
        files: list[str] = data.get("_files") or []
        prefilled = data.get("_prefilled_file")
        if prefilled and answer.lower() in {"yes", "y", "that one", "ok"}:
            return prefilled
        if answer.isdigit():
            index = int(answer) - 1
            if 0 <= index < len(files):
                return files[index]
            raise Retry(f"There's no option {answer} - pick 1 to {len(files)}.")
        candidate = Path(data["directory"]) / answer
        if candidate.is_file():
            return answer
        matches = [f for f in files if f.lower().startswith(answer.lower())]
        if len(matches) == 1:
            return matches[0]
        raise Retry(f"I can't find '{answer}' in {data['directory']}.")

    def _needs_target_text(data: dict) -> bool:
        return data.get("action") in ("shell", "url", "open", "code")

    def _target_prompt(data: dict) -> str:
        action = data.get("action")
        if action == "shell":
            return "What command should I run? (exactly as you'd type it)"
        if action == "url":
            return "Which link should it open?"
        return f"Anything more specific than {data['directory']}? Say 'no' to just use that folder."

    def _parse_target(answer: str, data: dict) -> str | None:
        answer = answer.strip().strip("\"'")
        if data.get("action") in ("open", "code") and answer.lower() in {"no", "none", "-", "that folder"}:
            return None
        return answer or None

    def _parse_triggers(answer: str, data: dict) -> list[str]:
        parts = [p.strip().lower() for p in re.split(r"[,;]|\bor\b", answer) if p.strip()]
        if not parts:
            raise Retry("Give me at least one phrase, e.g. 'run the sales report'.")
        clashes = []
        for phrase in parts:
            match = router.match(phrase)
            if match is not None and match.how in ("exact", "pattern"):
                clashes.append(f"'{phrase}' already runs {match.command.name}")
        if clashes:
            raise Retry("; ".join(clashes) + ". Pick different wording.")
        return parts

    def _parse_after(answer: str, data: dict) -> dict | None:
        low = answer.lower().strip(" .!?")
        if low in {"no", "none", "nothing", "-", "skip"}:
            return None
        after: dict = {}
        if "notif" in low:
            after["notify"] = True
        if "speak" in low or "say" in low or "tell" in low:
            after["speak"] = True
        path_match = re.search(r"open\s+(.+)$", answer, re.IGNORECASE)
        if path_match:
            after["open"] = path_match.group(1).strip().strip("\"'")
        if "file" in low and "report" in low:
            after["route_output"] = "day_folder"
        return after or None

    def _summarise(data: dict) -> str:
        action = data.get("action")
        target = data.get("file") or data.get("target") or data.get("directory")
        lines = [
            f"  name       : {data.get('name')}",
            f"  triggers   : {', '.join(data.get('triggers') or [])}",
            f"  folder     : {data.get('directory')}",
            f"  action     : {dict(ACTIONS.values()).get(action, action)}",
            f"  target     : {target}",
        ]
        if data.get("after"):
            lines.append(f"  afterwards : {data['after']}")
        return "\n".join(lines)

    def _finish(data: dict) -> str:
        action = data.get("action")
        directory = data["directory"]
        if action == "code":
            entry_run_as, target = "shell", f'code "{data.get("target") or directory}"'
        elif action == "url":
            entry_run_as, target = "url", data.get("target") or ""
        elif action == "open":
            entry_run_as, target = "open", data.get("target") or directory
        elif action == "shell":
            entry_run_as, target = "shell", data.get("target") or ""
        else:
            entry_run_as, target = action, data.get("file") or ""

        entry = {
            "name": data["name"],
            "trigger": data["triggers"],
            "working_directory": directory,
            "run_as": entry_run_as,
            "target": target,
            "args": [],
            "description": data.get("description") or f"Added from chat on {Path(directory).name}.",
        }
        after = data.get("after") or {}
        if after.pop("route_output", None):
            entry["route_output"] = "day_folder"
            entry["collect_from"] = ["dist", "output", "."]
        if after:
            entry["after"] = after

        store.append("scripts", "commands", entry)
        router.rebuild()
        phrase = data["triggers"][0]
        return (
            f"Saved '{data['name']}' to config/scripts.json.\n"
            f"It's live right now - try saying: \"{phrase}\""
        )

    steps = [
        Step("directory", _directory_prompt, _parse_directory),
        Step("action", _action_prompt, _parse_action),
        Step("file", _file_prompt, _parse_file, skip_if=lambda d: not _needs_file(d)),
        Step("target", _target_prompt, _parse_target, skip_if=lambda d: not _needs_target_text(d), optional=True),
        Step(
            "triggers",
            "What should I say (or type) to trigger this? Separate several with commas.",
            _parse_triggers,
        ),
        Step(
            "name",
            "Give it a short name for the config file (e.g. 'monthly sales report'):",
            lambda a, d: a.strip().lower(),
        ),
        Step(
            "after",
            "Anything after it runs - notify me, speak the result, open a folder, file the output into today's report folder? ('no' is fine)",
            _parse_after,
            optional=True,
        ),
    ]
    return Wizard(
        name="add command",
        steps=steps,
        on_finish=_finish,
        summarise=_summarise,
        intro="Let's set up a new command. I'll ask a few short questions.",
    )


def register(router: CommandRouter, store: ConfigStore) -> None:
    def provider():
        commands = []
        for entry in store.items("scripts", "commands"):
            name = str(entry.get("name") or "").strip()
            triggers = [t for t in (entry.get("trigger") or []) if t]
            if not name or not triggers:
                continue

            def handler(text, ctx, _entry=entry):
                try:
                    return _run_entry(_entry, text, ctx)
                except RunnableError as exc:
                    return str(exc)

            commands.append(
                make_command(
                    name=name,
                    triggers=triggers,
                    handler=handler,
                    help=str(entry.get("description") or ""),
                    category="my commands",
                    priority=7,
                    payload={"entry": entry},
                )
            )
        return commands

    router.add_provider(provider)

    @router.register(
        "add command",
        keywords=("add command", "new command", "create command", "teach you", "add a command", "make a command"),
        help="Walks you through creating a new command, step by step.",
        category="my commands",
        instant=True,
    )
    def cmd_add_command(text, ctx):
        return ctx.conversation.begin(_build_wizard(ctx.store, ctx.router))

    @router.register(
        "list my commands",
        keywords=("list my commands", "my commands", "custom commands", "what have i taught you"),
        help="Lists the commands you've added yourself.",
        category="my commands",
        instant=True,
    )
    def cmd_list_custom(text, ctx):
        entries = ctx.store.items("scripts", "commands")
        if not entries:
            return "You haven't added any custom commands yet. Say 'add command' to make one."
        lines = [f"{len(entries)} command(s) you've added:"]
        for entry in entries:
            triggers = ", ".join(entry.get("trigger") or [])
            lines.append(f"  - {entry.get('name')}  ->  {entry.get('run_as')} {entry.get('target')}")
            lines.append(f"      say: {triggers}")
        return "\n".join(lines)

    @router.register(
        "remove command",
        pattern=r"^\s*(?:remove|delete|forget)\s+(?:the\s+)?command\s+(.+?)\s*$",
        help="remove command monthly sales report",
        category="my commands",
    )
    def cmd_remove_custom(text, ctx):
        wanted = re.search(r"command\s+(.+?)\s*$", text, re.IGNORECASE).group(1).strip().lower()

        def _mutate(doc: dict) -> int:
            before = len(doc.get("commands", []))
            doc["commands"] = [
                e for e in doc.get("commands", [])
                if str(e.get("name", "")).lower() != wanted
                and wanted not in [t.lower() for t in (e.get("trigger") or [])]
            ]
            return before - len(doc["commands"])

        removed = ctx.store.update("scripts", _mutate)
        ctx.router.rebuild()
        if not removed:
            return f"No custom command called '{wanted}'. Say 'list my commands' to see them."
        return f"Removed '{wanted}'."
