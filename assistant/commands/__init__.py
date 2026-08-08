"""Command packs, one module per domain.

The old build put all 111 commands in a single ``core/commands.py``. That
still holds the Windows/Chrome/files/system automation (it works, and it is
genuinely one domain: "drive this PC"), but everything config-driven lives
here instead, split by domain so each file stays readable as the assistant
grows.

Two kinds of command live in these modules:

* **Static** - a normal ``@router.register`` function, e.g. "refresh".
* **Dynamic** - generated from ``config/*.json`` by a provider that the
  router re-asks after every refresh, e.g. one command per website. This is
  what makes "add a link to the JSON and it works next time" true without a
  restart.
"""
from __future__ import annotations

from assistant.core.router import CommandRouter
from assistant.store.store import ConfigStore


def register_all(router: CommandRouter, store: ConfigStore) -> CommandRouter:
    """Attach every pack to ``router``. Order only affects help output."""
    from assistant.commands import (
        chat, excel, links, media, meta, projects, reminders, reportpack,
        scriptpack, tools, vision,
    )

    meta.register(router, store)
    links.register(router, store)
    reportpack.register(router, store)
    scriptpack.register(router, store)
    projects.register(router, store)
    media.register(router, store)
    reminders.register(router, store)
    chat.register(router, store)
    excel.register(router, store)
    tools.register(router, store)
    vision.register(router, store)
    router.rebuild()
    return router
