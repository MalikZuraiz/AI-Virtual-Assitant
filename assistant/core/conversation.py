"""Multi-turn chat flows ("wizards").

Some things genuinely cannot be said in one sentence. Registering a new
command needs a directory, a thing to run, the file inside it, one or more
trigger phrases - the project brief (§4.3) is explicit that this is always an
interactive back-and-forth, never a single line and never hand-written JSON.

So: a tiny state machine. A wizard is a list of :class:`Step`s; the router
hands it every message while it is active, it validates each answer, and on
the final confirmation it writes to config. Every wizard gets ``cancel``,
``back`` and ``skip`` for free, because getting stuck three questions into a
form with no way out is the fastest way to make someone stop using a tool.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger("assistant.conversation")

CANCEL_WORDS = {"cancel", "stop", "abort", "never mind", "nevermind", "quit", "forget it"}
BACK_WORDS = {"back", "go back", "previous"}
SKIP_WORDS = {"skip", "none", "no", "nothing", "n/a", "-"}
YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "confirm", "save", "do it", "correct"}
NO_WORDS = {"no", "n", "nope", "cancel", "don't", "dont"}


class Retry(Exception):
    """Raised by a step's parser to re-ask with an explanation."""


@dataclass
class Step:
    key: str
    prompt: str | Callable[[dict], str]
    parse: Optional[Callable[[str, dict], Any]] = None
    #: Return True to skip this question entirely (e.g. we already know the answer).
    skip_if: Optional[Callable[[dict], bool]] = None
    optional: bool = False

    def prompt_text(self, data: dict) -> str:
        return self.prompt(data) if callable(self.prompt) else self.prompt


@dataclass
class Turn:
    """What the wizard wants said back, and whether it is finished."""

    text: str
    done: bool = False
    cancelled: bool = False


@dataclass
class Wizard:
    """A linear question-and-confirm flow that writes something on completion."""

    name: str
    steps: Sequence[Step]
    #: ``(data) -> str`` - performs the write, returns the chat confirmation.
    on_finish: Callable[[dict], str]
    #: ``(data) -> str`` - the "here's what I'll save" summary before writing.
    summarise: Optional[Callable[[dict], str]] = None
    intro: str = ""
    data: dict = field(default_factory=dict)
    _index: int = 0
    _confirming: bool = False

    # -- lifecycle --------------------------------------------------------
    def start(self) -> Turn:
        self._index = 0
        self._confirming = False
        prompt = self._advance_to_next_question()
        head = f"{self.intro}\n" if self.intro else ""
        tail = "\n(Say 'cancel' any time to stop.)"
        if prompt is None:
            return self._begin_confirmation(head)
        return Turn(f"{head}{prompt}{tail}")

    def handle(self, text: str) -> Turn:
        answer = (text or "").strip()
        low = answer.lower().strip(" .!?")

        if low in CANCEL_WORDS:
            return Turn(f"Cancelled - nothing was saved.", done=True, cancelled=True)

        if self._confirming:
            return self._handle_confirmation(low)

        if low in BACK_WORDS:
            return self._go_back()

        step = self.steps[self._index]
        if low in SKIP_WORDS and step.optional:
            self.data[step.key] = None
        else:
            try:
                value = step.parse(answer, self.data) if step.parse else answer
            except Retry as exc:
                return Turn(f"{exc}\n\n{step.prompt_text(self.data)}")
            if value in (None, "") and not step.optional:
                return Turn(f"I need an answer for that one.\n\n{step.prompt_text(self.data)}")
            self.data[step.key] = value

        self._index += 1
        prompt = self._advance_to_next_question()
        if prompt is None:
            return self._begin_confirmation("")
        return Turn(prompt)

    # -- internals --------------------------------------------------------
    def _advance_to_next_question(self) -> Optional[str]:
        """Move past any steps whose ``skip_if`` says we already know the answer."""
        while self._index < len(self.steps):
            step = self.steps[self._index]
            if step.skip_if is not None and step.skip_if(self.data):
                self.data.setdefault(step.key, None)
                self._index += 1
                continue
            return step.prompt_text(self.data)
        return None

    def _go_back(self) -> Turn:
        self._index = max(0, self._index - 1)
        while self._index > 0:
            step = self.steps[self._index]
            if step.skip_if is None or not step.skip_if(self.data):
                break
            self._index -= 1
        self.data.pop(self.steps[self._index].key, None)
        return Turn(self.steps[self._index].prompt_text(self.data))

    def _begin_confirmation(self, head: str) -> Turn:
        self._confirming = True
        summary = self.summarise(self.data) if self.summarise else str(self.data)
        return Turn(f"{head}Here's what I'll save:\n{summary}\n\nSave it? (yes / no)")

    def _handle_confirmation(self, low: str) -> Turn:
        if low in YES_WORDS:
            try:
                message = self.on_finish(self.data)
            except Exception as exc:  # noqa: BLE001 - report, don't crash the chat
                logger.exception("Wizard %s failed to save", self.name)
                return Turn(f"Couldn't save that: {exc}", done=True)
            return Turn(message, done=True)
        if low in NO_WORDS:
            return Turn("Dropped it - nothing was saved.", done=True, cancelled=True)
        return Turn("Just 'yes' to save it or 'no' to throw it away.")


class ConversationState:
    """Holds the single in-flight wizard, if any.

    One at a time on purpose: interleaving two half-finished forms in one
    chat window is confusing for a person and ambiguous for the router.
    """

    def __init__(self) -> None:
        self._wizard: Optional[Wizard] = None

    @property
    def active(self) -> bool:
        return self._wizard is not None

    @property
    def name(self) -> str | None:
        return self._wizard.name if self._wizard else None

    def begin(self, wizard: Wizard) -> str:
        self._wizard = wizard
        turn = wizard.start()
        if turn.done:
            self._wizard = None
        return turn.text

    def feed(self, text: str) -> str:
        if self._wizard is None:
            return ""
        turn = self._wizard.handle(text)
        if turn.done:
            self._wizard = None
        return turn.text

    def cancel(self) -> str:
        if self._wizard is None:
            return "Nothing in progress."
        name = self._wizard.name
        self._wizard = None
        return f"Cancelled '{name}' - nothing was saved."
