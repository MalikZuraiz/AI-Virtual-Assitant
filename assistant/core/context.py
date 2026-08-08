"""Everything a command handler might need, in one object.

Handlers stay small functions taking ``(text, ctx)`` instead of reaching into
globals - the pattern the original prototype used, importing a shared
``sophie`` instance from a module that did not exist.

Two things here are worth knowing about:

* **Lazy subsystems.** ``gestures`` imports mediapipe, which takes seconds
  and allocates real memory. It is built on first use, so the app starts fast
  on an 8GB laptop and never pays for a feature nobody invoked.
* **Per-thread job handle.** Handlers run on worker threads and want to
  stream progress ("still running..."), but ``Context`` is shared by all of
  them. ``ctx.progress(...)`` routes to *the calling thread's* job via
  thread-local storage, so two reports running at once cannot cross-post
  their output into each other's chat bubbles.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from assistant.automation.apps import AppLauncher
from assistant.automation.chrome_ctl import ChromeController, YouTubeController
from assistant.automation.files import FileManager
from assistant.automation.shell import SafeShell
from assistant.automation.system_info import SystemInfo
from assistant.automation.windows_ctl import WindowsController
from assistant.config import AppConfig
from assistant.core.conversation import ConversationState
from assistant.core.jobs import JobHandle, JobRunner
from assistant.core.router import CommandRouter
from assistant.core.selection import SelectionState
from assistant.core.voice import TextToSpeech
from assistant.store.store import ConfigStore

logger = logging.getLogger("assistant.context")


def _default_confirm(_prompt: str) -> bool:
    # Safe default for headless/test contexts: never perform a destructive
    # action without a real confirmation UI wired up.
    return False


def _noop(*_args, **_kwargs) -> None:
    return None


@dataclass
class Context:
    config: AppConfig
    store: ConfigStore
    router: CommandRouter
    jobs: JobRunner
    conversation: ConversationState
    selection: SelectionState

    apps: AppLauncher
    files: FileManager
    shell: SafeShell
    windows: WindowsController
    chrome: ChromeController
    youtube: YouTubeController
    system_info: SystemInfo
    tts: TextToSpeech

    reminders: object | None = None
    listener: object | None = None
    llm: object | None = None
    confirm: Callable[[str], bool] = field(default=_default_confirm)
    notify: Callable[[str, str], None] = field(default=_noop)
    #: Set by the Assistant: receives the answer-so-far during streaming.
    on_stream: Optional[Callable[[str], None]] = None
    #: ``("start" | "stop" | "status") -> str``. Supplied by the UI, which
    #: owns the microphone; commands never touch audio devices directly.
    listener_control: Callable[[str], str] | None = None
    #: Lets a gesture bound to ``action: command`` route text back through
    #: the assistant, so gestures can trigger anything commands can.
    run_command: Callable[[str], None] | None = None
    gestures: object | None = None

    _local: threading.local = field(default_factory=threading.local, repr=False)
    #: Built on first use by the gesture pack - importing mediapipe is slow
    #: and memory-hungry, so an assistant that is never asked for gestures
    #: never pays for it.
    gestures: object | None = None

    # -- per-thread job plumbing -----------------------------------------
    def bind_job(self, handle: JobHandle | None) -> None:
        """Called by the worker right before/after a handler runs."""
        self._local.job = handle

    @property
    def job(self) -> Optional[JobHandle]:
        return getattr(self._local, "job", None)

    def progress(self, text: str) -> None:
        """Stream a line of progress into the chat for the running command."""
        handle = self.job
        if handle is not None:
            handle.progress(text)
        else:
            logger.debug("progress (no job bound): %s", text)

    def stream(self, text: str) -> None:
        """Push a growing reply into the chat bubble as it is produced.

        Distinct from :meth:`progress`, which is transient status. This is the
        answer itself arriving piece by piece - what makes a slow local model
        feel like it is writing rather than hanging.
        """
        if self.on_stream is not None:
            self.on_stream(text)

    @property
    def cancelled(self) -> bool:
        handle = self.job
        return bool(handle and handle.cancelled)

    # -- construction -----------------------------------------------------
    @classmethod
    def build(
        cls,
        config: AppConfig,
        store: ConfigStore,
        router: CommandRouter,
        jobs: JobRunner,
        tts: TextToSpeech | None = None,
    ) -> "Context":
        return cls(
            config=config,
            store=store,
            router=router,
            jobs=jobs,
            conversation=ConversationState(),
            selection=SelectionState(),
            apps=AppLauncher(config),
            files=FileManager(),
            shell=SafeShell(config),
            windows=WindowsController(),
            chrome=ChromeController(),
            youtube=YouTubeController(),
            system_info=SystemInfo(),
            tts=tts or TextToSpeech(enabled=config.voice_replies_enabled),
        )
