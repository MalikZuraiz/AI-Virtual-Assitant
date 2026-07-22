"""Bundles all subsystems a command handler might need.

Built once in ``assistant/main.py`` and threaded through every command
handler as ``ctx``, so handlers stay small, pure-ish functions instead of
reaching into globals (the pattern the legacy scripts used, importing a
shared ``s`` "sophie" instance from a module that no longer exists).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from assistant.automation.apps import AppLauncher
from assistant.automation.chrome_ctl import ChromeController, YouTubeController
from assistant.automation.files import FileManager
from assistant.automation.shell import SafeShell
from assistant.automation.system_info import SystemInfo
from assistant.automation.windows_ctl import WindowsController
from assistant.config import AppConfig
from assistant.core.speech import SpeechListener, TextToSpeech
from assistant.vision.virtual_mouse import VirtualMouseController


def _default_confirm(_prompt: str) -> bool:
    # Safe default for headless/test contexts: never perform destructive
    # actions without a real confirmation UI wired up.
    return False


@dataclass
class Context:
    config: AppConfig
    apps: AppLauncher
    files: FileManager
    shell: SafeShell
    windows: WindowsController
    chrome: ChromeController
    youtube: YouTubeController
    system_info: SystemInfo
    tts: TextToSpeech
    listener: SpeechListener
    virtual_mouse: VirtualMouseController
    confirm: Callable[[str], bool] = field(default=_default_confirm)

    @classmethod
    def build(
        cls,
        config: AppConfig,
        on_vm_frame: Optional[Callable] = None,
        on_vm_status: Optional[Callable] = None,
    ) -> "Context":
        return cls(
            config=config,
            apps=AppLauncher(config),
            files=FileManager(),
            shell=SafeShell(config),
            windows=WindowsController(),
            chrome=ChromeController(),
            youtube=YouTubeController(),
            system_info=SystemInfo(),
            tts=TextToSpeech(rate=config.tts_rate, voice_index=config.tts_voice_index),
            listener=SpeechListener(),
            virtual_mouse=VirtualMouseController(
                camera_index=config.virtual_mouse_camera_index,
                fps_limit=config.virtual_mouse_fps_limit,
                on_frame=on_vm_frame,
                on_status=on_vm_status,
            ),
        )
