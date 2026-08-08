"""Reminders: APScheduler for firing, SQLite for surviving a restart.

Two stores on purpose (project brief §5):

* ``config/reminders.json`` is the **human** source of truth - hand-editable,
  diffable, easy to bulk-edit.
* A SQLite table in ``%APPDATA%`` is the **runtime** store - it remembers
  which one-off reminders already fired, so restarting the assistant does not
  re-fire this morning's 9am reminder every time it starts.

They sync on load and on every edit: JSON wins for content, SQLite keeps the
firing history. Only ``sqlite3`` from the standard library is used, so this
adds no dependency beyond APScheduler itself.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from assistant.store.paths import runtime_dir
from assistant.store.store import ConfigStore

logger = logging.getLogger("assistant.reminders")

WEEKDAYS = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
    "friday": "fri", "saturday": "sat", "sunday": "sun",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    key         TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    schedule    TEXT NOT NULL,
    day         TEXT,
    day_of_month INTEGER,
    at          TEXT,
    date        TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_fired  TEXT,
    fired_count INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Reminder:
    text: str
    schedule: str = "once"          # once | daily | weekly | monthly
    day: str | None = None          # weekday name, for weekly
    day_of_month: int | None = None  # for monthly
    at: str = "09:00"               # HH:MM
    date: str | None = None         # ISO date, for one-offs
    enabled: bool = True

    @property
    def key(self) -> str:
        """Stable identity, so re-syncing JSON doesn't duplicate a reminder."""
        parts = [self.text.strip().lower(), self.schedule, str(self.day), str(self.day_of_month), self.at, str(self.date)]
        return "|".join(parts)

    def to_json(self) -> dict:
        out: dict = {"text": self.text, "schedule": self.schedule, "time": self.at}
        if self.day:
            out["day"] = self.day
        if self.day_of_month:
            out["day_of_month"] = self.day_of_month
        if self.date:
            out["date"] = self.date
        if not self.enabled:
            out["enabled"] = False
        return out

    @classmethod
    def from_json(cls, raw: dict) -> "Reminder":
        return cls(
            text=str(raw.get("text") or "").strip(),
            schedule=str(raw.get("schedule") or "once").lower(),
            day=(str(raw["day"]).lower() if raw.get("day") else None),
            day_of_month=int(raw["day_of_month"]) if raw.get("day_of_month") else None,
            at=str(raw.get("time") or raw.get("at") or "09:00"),
            date=str(raw["date"]) if raw.get("date") else None,
            enabled=bool(raw.get("enabled", True)),
        )

    def describe(self) -> str:
        when = {
            "once": f"on {self.date} at {self.at}" if self.date else f"at {self.at}",
            "daily": f"every day at {self.at}",
            "weekly": f"every {self.day or 'monday'} at {self.at}",
            "monthly": f"on day {self.day_of_month or 1} of each month at {self.at}",
        }.get(self.schedule, self.schedule)
        return f"{self.text} - {when}"

    def trigger(self):
        hour, minute = _split_time(self.at)
        if self.schedule == "daily":
            return CronTrigger(hour=hour, minute=minute)
        if self.schedule == "weekly":
            return CronTrigger(day_of_week=WEEKDAYS.get(self.day or "monday", "mon"), hour=hour, minute=minute)
        if self.schedule == "monthly":
            return CronTrigger(day=self.day_of_month or 1, hour=hour, minute=minute)
        when = datetime.combine(
            date.fromisoformat(self.date) if self.date else date.today(),
            datetime.min.time(),
        ).replace(hour=hour, minute=minute)
        return DateTrigger(run_date=when)

    @property
    def is_past(self) -> bool:
        """True for a one-off whose moment has already gone."""
        if self.schedule != "once":
            return False
        hour, minute = _split_time(self.at)
        when = datetime.combine(
            date.fromisoformat(self.date) if self.date else date.today(), datetime.min.time()
        ).replace(hour=hour, minute=minute)
        return when < datetime.now()


def split_time(value: str) -> tuple[int, int, bool]:
    """Parse a time. Returns ``(hour, minute, was_explicit)``.

    ``was_explicit`` is False when the user gave no am/pm and an hour of
    1-12, i.e. "2:14" could mean either. Callers resolve that ambiguity with
    :func:`disambiguate_hour`; treating it as 02:14 unconditionally is why a
    reminder set at ten-to-two in the afternoon silently scheduled itself for
    two in the morning.
    """
    match = re.match(r"^\s*(\d{1,2})[:.]?(\d{2})?\s*(am|pm)?\s*$", str(value), re.IGNORECASE)
    if not match:
        return 9, 0, True
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm":
        hour = hour % 12 + 12
        return min(hour, 23), min(minute, 59), True
    if meridiem == "am":
        hour = 0 if hour == 12 else hour
        return min(hour, 23), min(minute, 59), True
    explicit = hour == 0 or hour > 12  # 24-hour clock leaves nothing to guess
    return min(hour, 23), min(minute, 59), explicit


def _split_time(value: str) -> tuple[int, int]:
    hour, minute, _explicit = split_time(value)
    return hour, minute


def disambiguate_hour(hour: int, minute: int, now: datetime | None = None) -> int:
    """For a bare "2:14", pick whichever of 02:14 / 14:14 comes first.

    That is what someone means when they say "remind me at 2:14" - the next
    2:14, not the one that may be twelve hours away.
    """
    if hour > 12:
        return hour
    now = now or datetime.now()
    today = now.date()
    options = []
    for candidate in ({hour, (hour % 12) + 12} if hour != 0 else {0, 12}):
        when = datetime.combine(today, datetime.min.time()).replace(hour=candidate, minute=minute)
        if when <= now:
            when += timedelta(days=1)
        options.append((when, candidate))
    options.sort()
    return options[0][1]


# ---------------------------------------------------------------------------
# Natural-language parsing
# ---------------------------------------------------------------------------

_TIME = r"(?:at\s+)?(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)"


def parse_reminder(text: str) -> Optional[Reminder]:
    """Turn "remind me to post on linkedin every monday at 9am" into a Reminder."""
    body = re.sub(r"^\s*(?:please\s+)?remind\s+me\s+(?:to\s+)?", "", text.strip(), flags=re.IGNORECASE)
    if not body:
        return None

    at = "09:00"
    ambiguous_hour: tuple[int, int] | None = None
    time_match = re.search(rf"\bat\s+(\d{{1,2}}(?:[:.]\d{{2}})?\s*(?:am|pm)?)", body, re.IGNORECASE)
    if time_match:
        hour, minute, explicit = split_time(time_match.group(1))
        if not explicit:
            # Only *one-off* reminders get next-occurrence inference. For a
            # recurring one, "every month at 10:00" plainly means 10am - there
            # is no "next occurrence" to reason about, and silently turning it
            # into 22:00 would be worse than the bug this fixes.
            ambiguous_hour = (hour, minute)
        at = f"{hour:02d}:{minute:02d}"
        body = body[: time_match.start()] + body[time_match.end():]

    def once_at() -> str:
        """The time string for a one-off, with am/pm resolved to the next one."""
        if ambiguous_hour is None:
            return at
        hour, minute = ambiguous_hour
        return f"{disambiguate_hour(hour, minute):02d}:{minute:02d}"

    # "in 20 minutes" / "in 2 hours"
    relative = re.search(r"\bin\s+(\d+)\s*(minute|min|hour|hr|day)s?\b", body, re.IGNORECASE)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2).lower()
        delta = {
            "minute": timedelta(minutes=amount), "min": timedelta(minutes=amount),
            "hour": timedelta(hours=amount), "hr": timedelta(hours=amount),
            "day": timedelta(days=amount),
        }[unit]
        when = datetime.now() + delta
        body = (body[: relative.start()] + body[relative.end():]).strip(" ,")
        return Reminder(text=_tidy(body), schedule="once", date=when.date().isoformat(), at=f"{when:%H:%M}")

    # "every monday" / "on mondays"
    weekly = re.search(r"\b(?:every|each|on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", body, re.IGNORECASE)
    if weekly:
        day = weekly.group(1).lower()
        body = (body[: weekly.start()] + body[weekly.end():]).strip(" ,")
        return Reminder(text=_tidy(body), schedule="weekly", day=day, at=at)

    if re.search(r"\bevery\s+day\b|\bdaily\b|\beach\s+day\b", body, re.IGNORECASE):
        body = re.sub(r"\bevery\s+day\b|\bdaily\b|\beach\s+day\b", "", body, flags=re.IGNORECASE)
        return Reminder(text=_tidy(body), schedule="daily", at=at)

    monthly = re.search(r"\b(?:every\s+month|monthly)\b(?:\s+on\s+(?:the\s+)?(\d{1,2}))?", body, re.IGNORECASE)
    if monthly:
        day_of_month = int(monthly.group(1)) if monthly.group(1) else 1
        body = (body[: monthly.start()] + body[monthly.end():]).strip(" ,")
        return Reminder(text=_tidy(body), schedule="monthly", day_of_month=day_of_month, at=at)

    # "tomorrow" / "today" / "on 12 August"
    if re.search(r"\btomorrow\b", body, re.IGNORECASE):
        body = re.sub(r"\btomorrow\b", "", body, flags=re.IGNORECASE)
        return Reminder(text=_tidy(body), schedule="once", date=(date.today() + timedelta(days=1)).isoformat(), at=once_at())
    if re.search(r"\btoday\b|\btonight\b", body, re.IGNORECASE):
        body = re.sub(r"\btoday\b|\btonight\b", "", body, flags=re.IGNORECASE)
        return Reminder(text=_tidy(body), schedule="once", date=date.today().isoformat(), at=once_at())

    dated = re.search(r"\bon\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)(?:\s+(\d{4}))?", body, re.IGNORECASE)
    if dated:
        try:
            day_num = int(dated.group(1))
            month = datetime.strptime(dated.group(2)[:3], "%b").month
            year = int(dated.group(3)) if dated.group(3) else date.today().year
            when = date(year, month, day_num)
            if when < date.today() and not dated.group(3):
                when = date(year + 1, month, day_num)
            body = (body[: dated.start()] + body[dated.end():]).strip(" ,")
            return Reminder(text=_tidy(body), schedule="once", date=when.isoformat(), at=once_at())
        except ValueError:
            pass

    if time_match:  # a bare time means "today, or tomorrow if that's passed"
        resolved = once_at()
        hour, minute = _split_time(resolved)
        when = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when < datetime.now():
            when += timedelta(days=1)
        return Reminder(text=_tidy(body), schedule="once", date=when.date().isoformat(), at=resolved)
    return None


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ,.")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReminderService:
    """Owns the scheduler, the SQLite runtime store and JSON synchronisation."""

    def __init__(
        self,
        store: ConfigStore,
        on_fire: Callable[[Reminder], None],
        db_path: Path | None = None,
    ) -> None:
        self.store = store
        self.on_fire = on_fire
        self.db_path = Path(db_path or (runtime_dir() / "reminders.db"))
        self._lock = threading.RLock()
        self._scheduler = BackgroundScheduler(daemon=True)
        self._init_db()

    # -- lifecycle --------------------------------------------------------
    def start(self) -> int:
        if not self._scheduler.running:
            self._scheduler.start()
        return self.sync()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    # -- syncing ----------------------------------------------------------
    def load(self) -> list[Reminder]:
        return [
            Reminder.from_json(raw)
            for raw in self.store.items("reminders", "reminders")
            if str(raw.get("text") or "").strip()
        ]

    def sync(self) -> int:
        """Rebuild every scheduled job from JSON. Safe to call repeatedly."""
        with self._lock:
            reminders = self.load()
            self._scheduler.remove_all_jobs()
            fired = self._already_fired()
            scheduled = 0
            for reminder in reminders:
                if not reminder.enabled:
                    continue
                if reminder.schedule == "once" and (reminder.is_past or reminder.key in fired):
                    # A one-off that already happened must not fire again on
                    # the next app start - this is exactly what the SQLite
                    # history is for.
                    continue
                self._remember(reminder)
                try:
                    self._scheduler.add_job(
                        self._fire,
                        trigger=reminder.trigger(),
                        args=[reminder.key],
                        id=f"rem-{abs(hash(reminder.key))}",
                        replace_existing=True,
                        # Two minutes, not an hour. With a long grace window,
                        # restarting the app fires every reminder whose time
                        # passed while it was closed - which is why a 14:14
                        # reminder arrived at 15:02. A short window still
                        # absorbs a busy CPU without resurrecting stale ones.
                        misfire_grace_time=120,
                        coalesce=True,
                    )
                    scheduled += 1
                except Exception:  # noqa: BLE001 - one bad entry must not stop the rest
                    logger.exception("Could not schedule reminder %r", reminder.text)
            logger.info("Scheduled %d/%d reminder(s)", scheduled, len(reminders))
            return scheduled

    def add(self, reminder: Reminder) -> Reminder:
        self.store.append("reminders", "reminders", reminder.to_json())
        self.sync()
        return reminder

    def next_run_for(self, reminder: Reminder) -> datetime | None:
        """When this reminder actually fires next, straight from the scheduler.

        Reported back when a reminder is created so a mis-parsed time is
        obvious immediately, instead of being discovered by it not going off.
        """
        for job in self._scheduler.get_jobs():
            if job.args and job.args[0] == reminder.key:
                return job.next_run_time
        return None

    def remove(self, phrase: str) -> int:
        wanted = phrase.strip().lower()

        def _mutate(doc: dict) -> int:
            before = len(doc.get("reminders", []))
            doc["reminders"] = [
                r for r in doc.get("reminders", [])
                if wanted not in str(r.get("text", "")).lower()
            ]
            return before - len(doc["reminders"])

        removed = self.store.update("reminders", _mutate)
        self.sync()
        return removed

    def upcoming(self, limit: int = 20) -> list[tuple[Reminder, datetime | None]]:
        by_key = {r.key: r for r in self.load()}
        rows: list[tuple[Reminder, datetime | None]] = []
        for job in self._scheduler.get_jobs():
            key = job.args[0] if job.args else None
            reminder = by_key.get(key)
            if reminder is not None:
                rows.append((reminder, job.next_run_time))
        rows.sort(key=lambda t: (t[1] is None, t[1]))
        return rows[:limit]

    # -- SQLite runtime store ---------------------------------------------
    def _remember(self, reminder: Reminder) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO reminders (key, text, schedule, day, day_of_month, at, date, enabled)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET text=excluded.text, enabled=excluded.enabled""",
                (
                    reminder.key, reminder.text, reminder.schedule, reminder.day,
                    reminder.day_of_month, reminder.at, reminder.date, int(reminder.enabled),
                ),
            )

    def _already_fired(self) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT key FROM reminders WHERE fired_count > 0 AND schedule = 'once'"
            ).fetchall()
        return {row[0] for row in rows}

    def _fire(self, key: str) -> None:
        reminder = next((r for r in self.load() if r.key == key), None)
        if reminder is None:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE reminders SET last_fired = ?, fired_count = fired_count + 1 WHERE key = ?",
                (datetime.now().isoformat(timespec="seconds"), key),
            )
        try:
            self.on_fire(reminder)
        except Exception:  # noqa: BLE001
            logger.exception("Reminder callback failed for %r", reminder.text)
