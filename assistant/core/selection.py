"""Numbered choices - "here are 6 matches, say 3".

The single most common friction in a chat assistant is having to type an
exact name or a full path. This replaces that: any command that finds more
than one candidate shows a numbered list and parks it, and the next message
can just be a number.

The verb is optional and it changes the action, so one listing serves several
intentions:

    list python projects
    3                 -> the default action (open)
    vs code 3         -> open it in VS Code
    folder 3          -> open its folder in Explorer
    run 3             -> run it

A selection expires (5 minutes by default) and is cleared as soon as any
other command runs, so a stray "3" typed an hour later can never fire
something unexpected.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger("assistant.selection")

#: How long a parked list stays answerable.
DEFAULT_TTL_SECONDS = 300

#: Verbs a pick may carry, mapped to a canonical action name. Longest first
#: when matching, so "open folder 3" beats "open 3".
VERB_ALIASES: dict[str, str] = {
    "open in vs code": "code",
    "open in vscode": "code",
    "open in code": "code",
    "vs code": "code",
    "vscode": "code",
    "code": "code",
    "open folder": "folder",
    "show folder": "folder",
    "folder": "folder",
    "directory": "folder",
    "explorer": "folder",
    "reveal": "folder",
    "generate": "run",
    "run": "run",
    "start": "run",
    "execute": "run",
    "play": "play",
    "watch": "play",
    "open": "open",
    "show": "open",
    "use": "use",
    "pick": "use",
    "select": "use",
    "choose": "use",
    "delete": "delete",
    "remove": "delete",
}

_PICK_RE = re.compile(
    r"^\s*(?P<verb>[a-z][a-z ]*?)?\s*(?P<number>\d{1,3})\s*$", re.IGNORECASE
)
_NUMBER_WORDS = {
    "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "last": -1,
}


@dataclass
class Choice:
    label: str
    payload: Any = None
    detail: str = ""


@dataclass
class Selection:
    """A parked numbered list plus what each verb does to a pick."""

    title: str
    choices: list[Choice]
    #: action name -> ``(choice, ctx) -> str``
    actions: dict[str, Callable[[Choice, Any], str]] = field(default_factory=dict)
    default_action: str = "open"
    hint: str = ""
    created: float = field(default_factory=time.monotonic)
    ttl: float = DEFAULT_TTL_SECONDS

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created) > self.ttl

    def render(self, limit: int = 30) -> str:
        lines = [self.title]
        for index, choice in enumerate(self.choices[:limit], start=1):
            suffix = f"   {choice.detail}" if choice.detail else ""
            lines.append(f"  {index}. {choice.label}{suffix}")
        if len(self.choices) > limit:
            lines.append(f"  ... and {len(self.choices) - limit} more")
        verbs = sorted({v for v in self.actions if v != self.default_action})
        if self.hint:
            lines.append(f"\n{self.hint}")
        else:
            extra = f" (or '{verbs[0]} 2', '{verbs[1]} 3', ...)" if len(verbs) > 1 else ""
            lines.append(f"\nSay a number to {self.default_action} it{extra}.")
        return "\n".join(lines)


def parse_pick(text: str) -> Optional[tuple[str | None, int]]:
    """Parse "3" / "open 3" / "vs code 2" into ``(action, index)``.

    Returns ``None`` for anything that is not purely a pick, so a normal
    command is never swallowed by a parked list.
    """
    raw = (text or "").strip().strip(".!?")
    low = raw.lower()

    word_match = re.match(r"^\s*(?:the\s+)?(\w+)\s+one\s*$", low)
    if word_match and word_match.group(1) in _NUMBER_WORDS:
        return None, _NUMBER_WORDS[word_match.group(1)]
    if low in _NUMBER_WORDS:
        return None, _NUMBER_WORDS[low]

    match = _PICK_RE.match(low)
    if not match:
        return None
    number = int(match.group("number"))
    verb = (match.group("verb") or "").strip()
    if not verb:
        return None, number
    for phrase in sorted(VERB_ALIASES, key=len, reverse=True):
        if verb == phrase or verb.endswith(" " + phrase) or verb.startswith(phrase + " "):
            return VERB_ALIASES[phrase], number
    return None  # an unknown verb is a command, not a pick


class SelectionState:
    """Holds the one parked list, if any."""

    def __init__(self) -> None:
        self._selection: Optional[Selection] = None

    @property
    def active(self) -> bool:
        return self._selection is not None and not self._selection.expired

    @property
    def current(self) -> Optional[Selection]:
        return self._selection if self.active else None

    def offer(self, selection: Selection) -> str:
        self._selection = selection
        return selection.render()

    def clear(self) -> None:
        self._selection = None

    def resolve(self, text: str, ctx: Any) -> Optional[str]:
        """Handle ``text`` as a pick, or return None if it isn't one."""
        selection = self.current
        if selection is None:
            return None
        parsed = parse_pick(text)
        if parsed is None:
            return None
        action, number = parsed

        if number == -1:
            number = len(selection.choices)
        if not 1 <= number <= len(selection.choices):
            return f"There's no option {number} - pick 1 to {len(selection.choices)}."
        choice = selection.choices[number - 1]

        name = action or selection.default_action
        handler = selection.actions.get(name)
        if handler is None:
            available = ", ".join(sorted(selection.actions)) or "none"
            return f"I can't '{name}' that one. Available: {available}."

        self._selection = None
        try:
            return handler(choice, ctx)
        except Exception as exc:  # noqa: BLE001 - reported, never raised into the UI
            logger.exception("Selection action %r failed", name)
            return f"That didn't work: {exc}"


def offer(
    ctx: Any,
    title: str,
    choices: Sequence[Choice],
    actions: dict[str, Callable[[Choice, Any], str]],
    default_action: str = "open",
    hint: str = "",
) -> str:
    """Park a list on ``ctx`` and return the rendered prompt.

    With exactly one candidate there is nothing to choose, so the default
    action runs straight away - being asked "1 or nothing?" is pure friction.
    """
    choices = list(choices)
    if not choices:
        return f"{title}\n(nothing matched)"
    if len(choices) == 1:
        handler = actions.get(default_action)
        if handler is not None:
            return handler(choices[0], ctx)
    return ctx.selection.offer(
        Selection(
            title=title,
            choices=choices,
            actions=actions,
            default_action=default_action,
            hint=hint,
        )
    )
