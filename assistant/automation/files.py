"""Directory and file operations."""
from __future__ import annotations

import fnmatch
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("assistant.files")


class FileManager:
    def list_dir(self, path: str) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"'{path}' does not exist."
        if not target.is_dir():
            return f"'{path}' is not a folder."
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if not entries:
            return f"'{target}' is empty."
        lines = [f"Contents of {target}:"]
        for entry in entries[:200]:
            marker = "[dir] " if entry.is_dir() else "[file]"
            lines.append(f"  {marker} {entry.name}")
        if len(entries) > 200:
            lines.append(f"  ...and {len(entries) - 200} more")
        return "\n".join(lines)

    def create_folder(self, path: str) -> str:
        target = Path(path).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        return f"Created folder {target}."

    def create_file(self, path: str) -> str:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
        return f"Created file {target}."

    def open_path(self, path: str) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"'{path}' does not exist."
        os.startfile(target)  # noqa: S606
        return f"Opening {target}."

    def rename(self, path: str, new_name: str) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"'{path}' does not exist."
        destination = target.with_name(new_name)
        target.rename(destination)
        return f"Renamed to {destination}."

    def move(self, source: str, destination: str) -> str:
        src = Path(source).expanduser()
        dst = Path(destination).expanduser()
        if not src.exists():
            return f"'{source}' does not exist."
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {src} to {dst}."

    def delete(self, path: str) -> str:
        """Caller (the command layer / GUI) is responsible for confirming
        destructive actions with the user before calling this."""
        target = Path(path).expanduser()
        if not target.exists():
            return f"'{path}' does not exist."
        try:
            from send2trash import send2trash

            send2trash(str(target))
            return f"Moved {target} to the Recycle Bin."
        except Exception:
            logger.warning("send2trash unavailable, deleting permanently", exc_info=True)
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return f"Permanently deleted {target}."

    def search_files(self, root: str, pattern: str, max_results: int = 50) -> str:
        base = Path(root).expanduser()
        if not base.exists():
            return f"'{root}' does not exist."
        if not pattern:
            pattern = "*"
        matches: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                    matches.append(os.path.join(dirpath, filename))
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        if not matches:
            return f"No files matching '{pattern}' under {base}."
        lines = [f"Found {len(matches)} match(es):"] + [f"  {m}" for m in matches]
        return "\n".join(lines)
