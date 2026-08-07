"""Split, hand-editable JSON configuration for the assistant.

Everything actionable the assistant knows about - projects, runnable
scripts, report generators, websites, media libraries, reminders - lives in
``<repo>/config/*.json``, one file per domain, never one giant blob.

``ConfigStore`` is the single reader/writer for those files. It creates any
that are missing (seeded from :mod:`assistant.store.defaults`), rereads them
on demand (the ``refresh`` command), and writes atomically so a crash mid-
write can never leave a half-written config behind.
"""
from assistant.store.paths import (
    app_data_dir,
    config_dir,
    repo_root,
    runtime_dir,
)
from assistant.store.store import ConfigStore, RefreshReport

__all__ = [
    "ConfigStore",
    "RefreshReport",
    "app_data_dir",
    "config_dir",
    "repo_root",
    "runtime_dir",
]
