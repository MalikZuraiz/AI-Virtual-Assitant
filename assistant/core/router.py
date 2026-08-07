"""Command routing - the single place typed and spoken text turns into action.

Grew out of the old registry (which replaced an ``if/elif`` chain) with three
things it needed to stop being "a few commands":

1. **Fuzzy matching.** Voice transcripts are never exactly the phrase you
   registered - "generate the pending penalties report" vs "generate pending
   penalties report". Exact and substring matching run first (they are
   precise and free); only if both miss does a fuzzy pass run, above a
   configurable score, so a near-miss lands on the right command instead of
   "I don't have a command for that".
2. **Config-driven commands.** Most of what this assistant can do is *data*,
   not code: report generators, websites, projects, scripts added through
   chat. Those are supplied by dynamic providers that are re-asked on every
   ``refresh``, so editing ``config/websites.json`` by hand adds a working
   command with no restart and no code change.
3. **Instant vs queued.** Anything that touches a subprocess, the network or
   the filesystem is queued onto the worker pool; only pure in-memory
   commands run inline. See :mod:`assistant.core.jobs`.

Match precedence is deliberately: regex > exact phrase > longest substring >
fuzzy. Specific always beats generic, so adding a site called "mail" can
never shadow "open my gmail".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Iterable, Optional, Sequence

logger = logging.getLogger("assistant.router")

Handler = Callable[[str, "object"], object]
DynamicProvider = Callable[[], Sequence["RegisteredCommand"]]

try:  # rapidfuzz is much better at this, but never make it a hard dependency
    from rapidfuzz import fuzz as _rf_fuzz

    def _ratio(a: str, b: str) -> float:
        return float(_rf_fuzz.token_set_ratio(a, b))
except ImportError:  # pragma: no cover - fallback path

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0


@dataclass
class Reply:
    """A handler's answer. Handlers may also just return a plain string."""

    text: str
    speak: bool = True
    detail: str = ""

    def __str__(self) -> str:  # so old code that concatenates still works
        return self.text


@dataclass
class RegisteredCommand:
    name: str
    keywords: tuple[str, ...] = ()
    pattern: Optional[re.Pattern] = None
    handler: Handler = None  # type: ignore[assignment]
    help: str = ""
    destructive: bool = False
    #: Runs on the GUI thread instead of the worker pool. Only for commands
    #: that do no I/O at all - a wizard turn, "help", reading the clock.
    instant: bool = False
    #: Lower runs sooner. Quick lookups jump ahead of long report runs.
    priority: int = 5
    category: str = "general"
    #: Set on dynamically-generated commands so they can be rebuilt/dropped.
    source: str = "builtin"
    payload: dict = field(default_factory=dict)


@dataclass
class Match:
    command: RegisteredCommand
    score: float
    how: str

    @property
    def confident(self) -> bool:
        return self.how != "fuzzy" or self.score >= 90


def normalise(text: str) -> str:
    """Lowercase, strip punctuation noise and collapse whitespace."""
    text = (text or "").lower().strip()
    text = re.sub(r"[‘’“”]", "'", text)
    text = re.sub(r"[?!.,;]+$", "", text)
    return re.sub(r"\s+", " ", text)


#: Words people put in front of a command that carry no meaning for matching.
FILLER_PREFIXES = (
    "please ", "can you ", "could you ", "would you ", "hey ", "ok ", "okay ",
    "now ", "just ",
)


def strip_filler(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in FILLER_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                changed = True
    return text.strip()


class CommandRouter:
    """Keyword / regex / fuzzy intent router for typed or spoken text."""

    def __init__(self, fuzzy_threshold: float = 82.0) -> None:
        self._static: list[RegisteredCommand] = []
        self._providers: list[DynamicProvider] = []
        self._dynamic: list[RegisteredCommand] = []
        self.fuzzy_threshold = fuzzy_threshold

    # -- registration -----------------------------------------------------
    def register(
        self,
        name: str,
        keywords: tuple[str, ...] = (),
        pattern: str | None = None,
        help: str = "",
        destructive: bool = False,
        instant: bool = False,
        priority: int = 5,
        category: str = "general",
    ):
        compiled = re.compile(pattern, re.IGNORECASE) if pattern else None
        lowered = tuple(normalise(k) for k in keywords)

        def decorator(fn: Handler) -> Handler:
            self._static.append(
                RegisteredCommand(
                    name=name,
                    keywords=lowered,
                    pattern=compiled,
                    handler=fn,
                    help=help,
                    destructive=destructive,
                    instant=instant,
                    priority=priority,
                    category=category,
                )
            )
            return fn

        return decorator

    def add_provider(self, provider: DynamicProvider) -> None:
        """Register a source of config-driven commands, rebuilt on refresh."""
        self._providers.append(provider)
        self.rebuild()

    def rebuild(self) -> int:
        """Re-ask every dynamic provider. Called after a config refresh."""
        rebuilt: list[RegisteredCommand] = []
        for provider in self._providers:
            try:
                rebuilt.extend(provider())
            except Exception:  # noqa: BLE001 - one bad provider must not break routing
                logger.exception("Dynamic command provider failed")
        self._dynamic = rebuilt
        logger.info("Router rebuilt: %d dynamic commands", len(rebuilt))
        return len(rebuilt)

    @property
    def commands(self) -> list[RegisteredCommand]:
        return [*self._static, *self._dynamic]

    def __len__(self) -> int:
        return len(self._static) + len(self._dynamic)

    # -- matching ---------------------------------------------------------
    def match(self, text: str) -> Optional[Match]:
        raw = normalise(text)
        cleaned = strip_filler(raw)
        if not cleaned:
            return None

        commands = self.commands

        # 1. regex - the most specific thing anyone can register
        for cmd in commands:
            if cmd.pattern is not None and (cmd.pattern.search(cleaned) or cmd.pattern.search(raw)):
                return Match(cmd, 100.0, "pattern")

        # 2. exact phrase
        for cmd in commands:
            if cleaned in cmd.keywords:
                return Match(cmd, 100.0, "exact")

        # 3. substring, longest keyword wins (a longer trigger is a more
        #    specific intent - "open website x" must beat a bare "open")
        best: Optional[RegisteredCommand] = None
        best_len = -1
        for cmd in commands:
            for keyword in cmd.keywords:
                if keyword and keyword in cleaned and len(keyword) > best_len:
                    best, best_len = cmd, len(keyword)
        if best is not None:
            return Match(best, 95.0, "substring")

        # 4. fuzzy, for voice transcripts and typos
        fuzzy_best: Optional[RegisteredCommand] = None
        fuzzy_score = 0.0
        for cmd in commands:
            for keyword in cmd.keywords:
                if not keyword:
                    continue
                score = _ratio(cleaned, keyword)
                if score > fuzzy_score:
                    fuzzy_best, fuzzy_score = cmd, score
        if fuzzy_best is not None and fuzzy_score >= self.fuzzy_threshold:
            return Match(fuzzy_best, fuzzy_score, "fuzzy")
        return None

    def suggest(self, text: str, limit: int = 3) -> list[str]:
        """Closest command names, for a helpful "did you mean" on a miss."""
        cleaned = strip_filler(normalise(text))
        scored: list[tuple[float, str]] = []
        for cmd in self.commands:
            best = max((_ratio(cleaned, k) for k in cmd.keywords), default=0.0)
            if best > 45:
                scored.append((best, cmd.keywords[0] if cmd.keywords else cmd.name))
        scored.sort(reverse=True)
        seen: list[str] = []
        for _, phrase in scored:
            if phrase not in seen:
                seen.append(phrase)
            if len(seen) >= limit:
                break
        return seen

    # -- dispatch ---------------------------------------------------------
    def dispatch(self, text: str, ctx: object) -> Reply:
        """Run the matching command. Blocking - call this on a worker thread."""
        match = self.match(text)
        if match is None:
            return Reply(self.unknown_text(text), speak=True)
        return self.run(match.command, text, ctx)

    @staticmethod
    def run(command: RegisteredCommand, text: str, ctx: object) -> Reply:
        logger.info("Dispatching %r -> %r", text, command.name)
        try:
            result = command.handler(text, ctx)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a chat line
            logger.exception("Command %r failed", command.name)
            return Reply(f"'{command.name}' hit an error: {exc}")
        if isinstance(result, Reply):
            return result
        return Reply(str(result) if result is not None else "Done.")

    def unknown_text(self, text: str) -> str:
        suggestions = self.suggest(text)
        if suggestions:
            options = ", ".join(f"'{s}'" for s in suggestions)
            return (
                f"I don't have a command for that yet. Did you mean {options}?\n"
                "Say 'add command' and I'll walk you through creating it."
            )
        return (
            "I don't have a command for that yet. Say 'help' to see what I can do, "
            "or 'add command' and I'll walk you through creating it."
        )

    # -- introspection ----------------------------------------------------
    def by_category(self) -> dict[str, list[RegisteredCommand]]:
        grouped: dict[str, list[RegisteredCommand]] = {}
        for cmd in self.commands:
            grouped.setdefault(cmd.category, []).append(cmd)
        for items in grouped.values():
            items.sort(key=lambda c: c.name)
        return dict(sorted(grouped.items()))

    def help_text(self, category: str | None = None) -> str:
        grouped = self.by_category()
        if category:
            wanted = {k: v for k, v in grouped.items() if category.lower() in k.lower()}
            grouped = wanted or grouped
        lines = [f"I know {len(self)} commands right now:"]
        for name, items in grouped.items():
            lines.append(f"\n[{name}]")
            for cmd in items:
                example = cmd.keywords[0] if cmd.keywords else cmd.name
                lines.append(f"  - {example}{(' - ' + cmd.help) if cmd.help else ''}")
        return "\n".join(lines)

    def categories(self) -> list[str]:
        return list(self.by_category())


def make_command(
    name: str,
    triggers: Iterable[str],
    handler: Handler,
    *,
    help: str = "",
    category: str = "general",
    priority: int = 5,
    payload: dict | None = None,
    source: str = "config",
    instant: bool = False,
) -> RegisteredCommand:
    """Build a command outside the decorator - used by dynamic providers."""
    return RegisteredCommand(
        name=name,
        keywords=tuple(normalise(t) for t in triggers if t),
        pattern=None,
        handler=handler,
        help=help,
        instant=instant,
        priority=priority,
        category=category,
        source=source,
        payload=payload or {},
    )
