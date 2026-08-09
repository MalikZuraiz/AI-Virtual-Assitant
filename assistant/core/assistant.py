"""The assistant core - one entry point for typed text, voice and reminders.

``Assistant.handle(text)`` is the single ``route_command`` the brief asks for
(§3.3): a typed command, a voice transcript and a tray-menu action all land
here, so there is exactly one place where intent becomes action and exactly
one place to fix when routing is wrong.

It returns immediately. Anything that touches a subprocess, the filesystem or
the network is pushed onto the worker pool and answers later through the
event callback, which is what keeps the GUI responsive while a four-minute
report generator runs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from assistant.commands import register_all
from assistant.config import AppConfig
from assistant.core.context import Context
from assistant.core.jobs import JobEvent, JobRunner, JobState
from assistant.core.reminders import Reminder, ReminderService
from assistant.core.router import CommandRouter, Reply
from assistant.core.selection import parse_pick
from assistant.core.voice import TextToSpeech
from assistant.integrations.llm import LocalLLM
from assistant.store.bootstrap import seed_from_machine
from assistant.store.store import ConfigStore
from assistant.workspace.scaffold import ensure_workspace

logger = logging.getLogger("assistant.core")


@dataclass
class AssistantEvent:
    """Something the UI should show. Emitted from any thread."""

    kind: str          # user | pending | progress | reply | error | notice | reminder
    text: str
    speak: bool = False
    job_id: Optional[int] = None

    @property
    def is_reply(self) -> bool:
        return self.kind in ("reply", "error", "reminder")


class Assistant:
    def __init__(
        self,
        config: AppConfig,
        on_event: Callable[[AssistantEvent], None],
        *,
        store: ConfigStore | None = None,
        workers: int = 2,
        enable_reminders: bool = True,
    ) -> None:
        self.config = config
        self._on_event = on_event

        self.store = store or ConfigStore()
        # core.json owns the name so it can be changed by hand-editing config
        # like everything else; AppConfig keeps the app-level toggles.
        self.config.assistant_name = str(
            self.store.value("core", "assistant_name", config.assistant_name)
        )

        self.router = CommandRouter(
            fuzzy_threshold=float(self.store.value("core", "behaviour.fuzzy_match_threshold", 82) or 82)
        )
        self._register_commands()

        self.jobs = JobRunner(workers=workers, on_event=self._on_job_event)
        self.tts = TextToSpeech(
            enabled=bool(self.store.value("core", "behaviour.speak_replies", config.voice_replies_enabled))
        )
        self.ctx = Context.build(config, self.store, self.router, self.jobs, tts=self.tts)

        self.reminders: ReminderService | None = None
        if enable_reminders:
            self.reminders = ReminderService(self.store, on_fire=self._on_reminder)
            self.ctx.reminders = self.reminders

        self.llm = LocalLLM.from_config(self.store)
        self.ctx.llm = self.llm
        # Gestures bound to ``action: command`` re-enter here, so a gesture
        # can do anything a typed command can.
        self.ctx.run_command = self.handle
        # Streaming answers bypass the job-progress channel: progress is
        # transient status, this is the reply itself arriving in pieces.
        self.ctx.on_stream = lambda text: self._emit(
            AssistantEvent("stream", text, speak=False)
        )
        # For background threads with no bound job (the gesture camera loop)
        # that still need to land in chat and be spoken - see Context.announce.
        self.ctx.on_announce = lambda text, speak=True: self._emit(
            AssistantEvent("reply", text, speak=speak)
        )

        self.store.on_refresh(self._on_config_refresh)

    def _on_config_refresh(self, _report) -> None:
        self.router.rebuild()
        # Config-driven settings that live outside the router still need
        # picking up, or "refresh" would only be half-true.
        self.llm = LocalLLM.from_config(self.store)
        self.ctx.llm = self.llm
        self.tts.enabled = bool(self.store.value("core", "behaviour.speak_replies", True))
        self.router.fuzzy_threshold = float(
            self.store.value("core", "behaviour.fuzzy_match_threshold", 82) or 82
        )

    # -- wiring -----------------------------------------------------------
    def _register_commands(self) -> None:
        from assistant.core.commands import register_builtins

        register_builtins(self.router)   # the Windows/Chrome/files/system pack
        register_all(self.router, self.store)  # config-driven domain packs
        logger.info("Router ready with %d commands", len(self.router))

    def set_confirm(self, fn: Callable[[str], bool]) -> None:
        self.ctx.confirm = fn

    def set_notifier(self, fn: Callable[[str, str], None]) -> None:
        self.ctx.notify = fn

    def set_listener_control(self, fn: Callable[[str], str]) -> None:
        """The UI owns the microphone; commands ask it to start/stop via this."""
        self.ctx.listener_control = fn

    # -- lifecycle --------------------------------------------------------
    def start(self, *, scaffold: bool = True) -> str:
        """Bring up scheduling and folders. Returns the greeting line."""
        if scaffold:
            try:
                ensure_workspace(self.store)
            except Exception:  # noqa: BLE001 - a locked drive must not block startup
                logger.exception("Workspace scaffold failed")
        scheduled = 0
        if self.reminders is not None:
            try:
                scheduled = self.reminders.start()
            except Exception:  # noqa: BLE001
                logger.exception("Reminder service failed to start")
        reports = len(self.store.items("reports", "reports"))
        bits = [f"{self.config.assistant_name} ready", f"{len(self.router)} commands"]
        if reports:
            bits.append(f"{reports} reports")
        if scheduled:
            bits.append(f"{scheduled} reminders")
        return " - ".join(bits) + "."

    def shutdown(self) -> None:
        if self.reminders is not None:
            self.reminders.shutdown()
        self.jobs.shutdown()
        self.tts.stop()

    def first_run_setup(self) -> str:
        """Discover this machine's folders and scaffold the workspace."""
        seeded = seed_from_machine(self.store)
        created = ensure_workspace(self.store)
        self.router.rebuild()
        return f"{seeded.summary()}\n{created.summary()}"

    # -- the single entry point -------------------------------------------
    def handle(self, text: str) -> None:
        """Route one command. Returns immediately; results arrive as events."""
        text = (text or "").strip()
        if not text:
            return

        # A wizard in progress owns the conversation until it finishes or is
        # cancelled - otherwise "yes" would route to some unrelated command.
        if self.ctx.conversation.active:
            reply = self.ctx.conversation.feed(text)
            self._emit(AssistantEvent("reply", reply, speak=True))
            return

        # A parked numbered list answers a bare "3" or "vs code 3". Only a
        # pure pick is intercepted, so ordinary commands still route normally
        # even while a list is on screen.
        if self.ctx.selection.active and parse_pick(text) is not None:
            # A pick can kick off real work (merging two 120MB exports, running
            # a report), so acknowledge it the same way a command is
            # acknowledged - otherwise the chat sits silent and it looks hung.
            self._emit(AssistantEvent("pending", "Working on that...", speak=False))
            self.jobs.submit("your pick", self._make_pick_job(text), priority=2)
            return

        match = self.router.match(text)
        if match is None:
            # A matched command logs "Dispatching X -> Y" from within
            # CommandRouter.run - a miss logs nothing anywhere by default,
            # which is exactly what made a garbled voice transcript look
            # like it vanished into nothing instead of like what it was: a
            # phrase that failed to match anything.
            logger.info("No command matched: %r", text)
            self._fallback(text)
            return

        command = match.command
        if command.instant:
            reply = self.router.run(command, text, self.ctx)
            self._emit(AssistantEvent("reply", reply.text, speak=reply.speak))
            return

        if command.destructive and self.store.value("core", "behaviour.confirm_destructive", True):
            if not self.ctx.confirm(f"Run '{command.name}'?"):
                self._emit(AssistantEvent("reply", f"Cancelled '{command.name}'.", speak=True))
                return

        title = command.name
        self._emit(AssistantEvent("pending", f"Running {title}...", speak=False))
        self.jobs.submit(title, self._make_job(command, text), priority=command.priority)

    def _fallback(self, text: str) -> None:
        """Nothing matched. Say so - never a silent detour into open chat.

        This used to pipe any unmatched text straight to the LLM whenever one
        was configured and reachable, with no prefix required. That directly
        contradicted the documented chat design (assistant/commands/chat.py:
        "opt-in by prefix... anything that matched no command and has no
        prefix still gets the plain 'I don't have a command for that'") and
        it is the reason a garbled voice transcript like "generate any
        penalties before." - a failed attempt at a report command - silently
        turned into a 15-20 second round trip with whatever chat persona
        happened to be active, instead of a fast, clear "I didn't catch a
        command there." A real ``nova <message>`` already matches the ``chat``
        command directly in the router and never reaches this method at all,
        so there is nothing to route here - only to report.
        """
        self._emit(AssistantEvent("reply", self.router.unknown_text(text), speak=True))

    def _make_job(self, command, text: str):
        def _run(handle) -> str:
            self.ctx.bind_job(handle)
            try:
                reply: Reply = self.router.run(command, text, self.ctx)
                self._emit(
                    AssistantEvent("reply", reply.text, speak=reply.speak, job_id=handle.id)
                )
            finally:
                self.ctx.bind_job(None)
            return ""  # the reply is already emitted; nothing to add on finish

        return _run

    def _make_pick_job(self, text: str):
        def _run(handle) -> str:
            self.ctx.bind_job(handle)
            try:
                reply = self.ctx.selection.resolve(text, self.ctx)
                if reply:
                    self._emit(AssistantEvent("reply", reply, speak=True, job_id=handle.id))
            finally:
                self.ctx.bind_job(None)
            return ""

        return _run

    # -- events -----------------------------------------------------------
    def _emit(self, event: AssistantEvent) -> None:
        if event.speak and event.text:
            self.tts.say(event.text)
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a broken UI listener must not kill a worker
            logger.exception("Assistant event listener failed")

    def _on_job_event(self, event: JobEvent) -> None:
        if event.state is JobState.PROGRESS:
            self._emit(AssistantEvent("progress", event.text, job_id=event.job_id))
        elif event.state is JobState.FAILED:
            self._emit(
                AssistantEvent("error", f"'{event.title}' failed - {event.text}", speak=True, job_id=event.job_id)
            )
        elif event.state is JobState.CANCELLED:
            self._emit(AssistantEvent("notice", f"'{event.title}' cancelled.", job_id=event.job_id))

    def _on_reminder(self, reminder: Reminder) -> None:
        self.ctx.notify(f"{self.config.assistant_name} · Reminder", reminder.text)
        self._emit(AssistantEvent("reminder", f"Reminder: {reminder.text}", speak=True))
