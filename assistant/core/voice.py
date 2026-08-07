"""Speech output: edge-tts when there's internet, pyttsx3 when there isn't.

``edge-tts`` uses Microsoft Edge's free read-aloud voices - genuinely
natural-sounding, no API key, no paid tier - which is why the brief picks it
over the robotic SAPI voices. It needs internet, so ``pyttsx3`` stays as the
offline fallback and the two are behind one interface.

Playback goes through Windows' MCI (``winmm.mciSendStringW``) rather than
adding a playback library: it plays MP3 natively, ships with the OS, and
avoids pulling in an audio stack just to say "report finished".

The pyttsx3 path deliberately constructs ``Engine`` directly instead of
calling ``pyttsx3.init()``. ``init()`` caches engines in a module-level dict
and hands back the *same* instance every time; a reused engine goes silent
after the first utterance or two, which is the classic "TTS worked once and
then stopped" bug.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import queue
import tempfile
import threading
import uuid
from pathlib import Path

logger = logging.getLogger("assistant.voice")

DEFAULT_VOICE = "en-US-AriaNeural"

#: Voices worth offering in settings - all free Edge neural voices.
SUGGESTED_VOICES = (
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "en-IN-NeerjaNeural",
    "en-IN-PrabhatNeural",
)


def _play_with_mci(path: Path) -> bool:
    """Play an audio file synchronously via Windows MCI. False if unavailable."""
    try:
        winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    alias = f"nova{uuid.uuid4().hex[:8]}"
    send = winmm.mciSendStringW
    buffer = ctypes.create_unicode_buffer(128)
    try:
        if send(f'open "{path}" type mpegvideo alias {alias}', None, 0, None) != 0:
            if send(f'open "{path}" alias {alias}', None, 0, None) != 0:
                return False
        send(f"play {alias} wait", None, 0, None)
        return True
    finally:
        send(f"close {alias}", buffer, 128, None)


class TextToSpeech:
    """Speaks queued text on a single background thread, newest last.

    A queue (rather than speaking inline) matters because two commands can
    finish at nearly the same moment; without it their audio overlaps into
    noise, and the GUI thread would block for the length of the sentence.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        enabled: bool = True,
        rate: str = "+0%",
        prefer_offline: bool = False,
    ) -> None:
        self.voice = voice
        self.rate = rate
        self.enabled = enabled
        self.prefer_offline = prefer_offline
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._online_ok = True
        self._thread = threading.Thread(target=self._loop, name="tts", daemon=True)
        self._thread.start()

    # -- public -----------------------------------------------------------
    @property
    def available(self) -> bool:
        """True if any backend can actually speak on this machine."""
        for module in ("edge_tts", "pyttsx3"):
            try:
                __import__(module)
                return True
            except ImportError:
                continue
        return False

    def say(self, text: str) -> None:
        if not self.enabled or not text or not text.strip():
            return
        self._queue.put(self._shorten(text))

    def stop(self) -> None:
        self._queue.put(None)

    @staticmethod
    def _shorten(text: str, limit: int = 400) -> str:
        """Speak a readable summary, never a 200-line command listing."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        spoken = lines[0]
        if len(lines) > 1:
            spoken += f". Plus {len(lines) - 1} more line{'s' if len(lines) > 2 else ''} in the chat."
        return spoken[:limit]

    # -- worker -----------------------------------------------------------
    def _loop(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                return
            try:
                self._speak_once(text)
            except Exception:  # noqa: BLE001 - never let TTS take down the app
                logger.exception("TTS failed for %r", text[:60])

    def _speak_once(self, text: str) -> None:
        if not self.prefer_offline and self._online_ok and self._speak_edge(text):
            return
        self._speak_pyttsx3(text)

    def _speak_edge(self, text: str) -> bool:
        try:
            import edge_tts
        except ImportError:
            self._online_ok = False
            return False

        path = Path(tempfile.gettempdir()) / f"nova-tts-{uuid.uuid4().hex[:8]}.mp3"

        async def _render() -> None:
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
            await communicate.save(str(path))

        try:
            asyncio.run(_render())
        except Exception as exc:  # noqa: BLE001 - offline, DNS, throttling...
            logger.info("edge-tts unavailable (%s); using the offline voice", exc)
            self._online_ok = False
            return False

        try:
            return _play_with_mci(path)
        finally:
            path.unlink(missing_ok=True)

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            import pyttsx3
            from pyttsx3.engine import Engine
        except ImportError:
            logger.warning("No TTS backend available; staying silent")
            return
        # Engine(...) directly, NOT pyttsx3.init() - see the module docstring.
        engine = Engine(driverName=None, debug=False)
        try:
            engine.setProperty("rate", 185)
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        del pyttsx3  # keep the import meaningful for linters
