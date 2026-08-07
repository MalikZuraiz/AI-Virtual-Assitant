"""Everything a command handler might need, in one object.

Handlers stay small functions taking ``(text, ctx)`` instead of reaching into
globals - the pattern the original prototype used, importing a shared
``sophie`` instance from a module that did not exist.

Two things here are worth knowing about:

* **Lazy subsystems.** ``virtual_mouse`` imports mediapipe, which takes
  seconds and allocates real memory. It is built on first use, so the app
  starts fast on an 8GB laptop and never pays for a feature nobody invoked.
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

    _local: threading.local = field(default_factory=threading.local, repr=False)
    _virtual_mouse: object | None = field(default=None, repr=False)
    _vm_hooks: dict = field(default_factory=dict, repr=False)

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

    @property
    def cancelled(self) -> bool:
        handle = self.job
        return bool(handle and handle.cancelled)

    # -- lazily-built subsystems -----------------------------------------
    @property
    def virtual_mouse(self):
        """Built on first use - importing mediapipe is slow and memory-hungry."""
        if self._virtual_mouse is None:
            from assistant.vision.virtual_mouse import VirtualMouseController

            cfg = self.config
            self._virtual_mouse = VirtualMouseController(
                camera_index=cfg.virtual_mouse_camera_index,
                fps_limit=cfg.virtual_mouse_fps_limit,
                sensitivity=cfg.virtual_mouse_sensitivity,
                center_x=cfg.virtual_mouse_center_x,
                center_y=cfg.virtual_mouse_center_y,
                min_cutoff=cfg.virtual_mouse_min_cutoff,
                beta=cfg.virtual_mouse_beta,
                on_frame=self._vm_hooks.get("on_frame"),
                on_status=self._vm_hooks.get("on_status"),
            )
        return self._virtual_mouse

    def set_virtual_mouse_hooks(self, on_frame=None, on_status=None) -> None:
        self._vm_hooks = {"on_frame": on_frame, "on_status": on_status}

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
            apps=AppLauncher(config),
            files=FileManager(),
            shell=SafeShell(config),
            windows=WindowsController(),
            chrome=ChromeController(),
            youtube=YouTubeController(),
            system_info=SystemInfo(),
            tts=tts or TextToSpeech(enabled=config.voice_replies_enabled),
        )
