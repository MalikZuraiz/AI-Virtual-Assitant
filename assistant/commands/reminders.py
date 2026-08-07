"""Chat commands for reminders.

Both paths the brief asks for stay in sync because they share one store:
saying "remind me to X every monday at 9am" writes the same
``config/reminders.json`` entry you could have typed by hand, and a hand-typed
entry becomes a live schedule on the next ``refresh``.
"""
from __future__ import annotations

import re

from assistant.core.reminders import Reminder, parse_reminder
from assistant.core.router import CommandRouter
from assistant.store.store import ConfigStore


def register(router: CommandRouter, store: ConfigStore) -> None:
    @router.register(
        "add reminder",
        pattern=r"^\s*(?:please\s+)?remind\s+me\b.+$",
        help="remind me to post on linkedin every monday at 9am  /  remind me to call ali in 20 minutes",
        category="reminders",
        instant=True,
    )
    def cmd_add_reminder(text, ctx):
        reminder = parse_reminder(text)
        if reminder is None or not reminder.text:
            return (
                "I couldn't work out the timing. Try one of these shapes:\n"
                "  remind me to X every monday at 9am\n"
                "  remind me to X daily at 18:00\n"
                "  remind me to X on 12 August at 10:00\n"
                "  remind me to X in 30 minutes"
            )
        service = getattr(ctx, "reminders", None)
        if service is None:
            ctx.store.append("reminders", "reminders", reminder.to_json())
            return f"Saved: {reminder.describe()} (scheduler isn't running, so it'll start on next launch)."
        service.add(reminder)
        return f"Got it - {reminder.describe()}."

    @router.register(
        "list reminders",
        keywords=("list reminders", "my reminders", "what reminders", "show reminders", "upcoming reminders"),
        help="Shows every reminder and when it next fires.",
        category="reminders",
        instant=True,
    )
    def cmd_list_reminders(text, ctx):
        service = getattr(ctx, "reminders", None)
        if service is None:
            entries = ctx.store.items("reminders", "reminders")
            if not entries:
                return "No reminders set."
            return "\n".join(f"  - {Reminder.from_json(e).describe()}" for e in entries)
        rows = service.upcoming()
        if not rows:
            entries = ctx.store.items("reminders", "reminders")
            if entries:
                return (
                    f"{len(entries)} reminder(s) in config, but none are scheduled - "
                    "they may all be one-offs that already fired. Say 'refresh' to re-sync."
                )
            return "No reminders set. Try: remind me to stretch every day at 15:00"
        lines = [f"{len(rows)} reminder(s):"]
        for reminder, next_run in rows:
            when = next_run.strftime("%a %d %b, %H:%M") if next_run else "not scheduled"
            lines.append(f"  - {reminder.text}  ({reminder.describe().split(' - ', 1)[-1]}; next: {when})")
        return "\n".join(lines)

    @router.register(
        "remove reminder",
        pattern=r"^\s*(?:remove|delete|cancel|forget)\s+(?:the\s+)?reminder\s+(?:to\s+|about\s+)?(.+?)\s*$",
        help="remove reminder post on linkedin",
        category="reminders",
        instant=True,
    )
    def cmd_remove_reminder(text, ctx):
        phrase = re.search(r"reminder\s+(?:to\s+|about\s+)?(.+?)\s*$", text, re.IGNORECASE).group(1).strip()
        service = getattr(ctx, "reminders", None)
        if service is None:
            return "The reminder scheduler isn't running right now."
        removed = service.remove(phrase)
        if not removed:
            return f"No reminder matching '{phrase}'. Say 'list reminders' to see them."
        return f"Removed {removed} reminder(s) matching '{phrase}'."
