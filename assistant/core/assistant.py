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

        match = self.router.match(text)
        if match is None:
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
        """Nothing matched. Try open chat if it's configured, else say so.

        Deliberately last and deliberately optional: the router stays the
        brain, and with no model configured the assistant gives the same
        honest "I don't have a command for that" it always did.
        """
        if self.llm is not None and self.llm.available():
            self._emit(AssistantEvent("pending", "Thinking...", speak=False))

            def _run(handle) -> str:
                self.ctx.bind_job(handle)
                try:
                    self._emit(
                        AssistantEvent("reply", self.llm.chat(text), speak=True, job_id=handle.id)
                    )
                finally:
                    self.ctx.bind_job(None)
                return ""

            self.jobs.submit("open chat", _run, priority=6)
            return
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
        message = f"Reminder: {reminder.text}"
        self.ctx.notify(self.config.assistant_name, reminder.text)
        self._emit(AssistantEvent("reminder", message, speak=True))
