"""Directory and file operations."""
from __future__ import annotations

import fnmatch
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("assistant.files")

_QUICK_FOLDERS = {
    "downloads": "Downloads",
    "desktop": "Desktop",
    "documents": "Documents",
    "pictures": "Pictures",
    "videos": "Videos",
    "music": "Music",
}


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

    def copy(self, source: str, destination: str) -> str:
        src = Path(source).expanduser()
        dst = Path(destination).expanduser()
        if not src.exists():
            return f"'{source}' does not exist."
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return f"Copied {src} to {dst}."

    def compress(self, source: str, archive_path: str | None = None) -> str:
        src = Path(source).expanduser()
        if not src.exists():
            return f"'{source}' does not exist."
        if archive_path:
            archive_base = str(Path(archive_path).expanduser().with_suffix(""))
        else:
            archive_base = str(src.with_suffix("")) if src.is_file() else str(src)
        if src.is_dir():
            shutil.make_archive(archive_base, "zip", root_dir=str(src))
        else:
            shutil.make_archive(archive_base, "zip", root_dir=str(src.parent), base_dir=src.name)
        return f"Created archive {archive_base}.zip"

    def extract(self, archive_path: str, destination: str | None = None) -> str:
        archive = Path(archive_path).expanduser()
        if not archive.exists():
            return f"'{archive_path}' does not exist."
        dest = Path(destination).expanduser() if destination else archive.with_suffix("")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(archive), str(dest))
        return f"Extracted {archive} to {dest}."

    def file_info(self, path: str) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"'{path}' does not exist."
        stat = target.stat()
        size = stat.st_size
        if size < 1024:
            size_str = f"{size} bytes"
        elif size < 1024 ** 2:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / 1024 ** 2:.1f} MB"
        return (
            f"{target}\n"
            f"  Type: {'Folder' if target.is_dir() else 'File'}\n"
            f"  Size: {size_str}\n"
            f"  Modified: {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}\n"
            f"  Created: {datetime.fromtimestamp(stat.st_ctime):%Y-%m-%d %H:%M:%S}"
        )

    def quick_folder(self, name: str) -> str:
        key = name.strip().lower()
        subfolder = _QUICK_FOLDERS.get(key)
        if subfolder is None:
            known = ", ".join(sorted(_QUICK_FOLDERS))
            return f"I don't know the folder '{name}'. Try one of: {known}."
        return self.open_path(str(Path.home() / subfolder))
