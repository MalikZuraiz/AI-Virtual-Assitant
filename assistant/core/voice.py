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
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable

logger = logging.getLogger("assistant.voice")

DEFAULT_VOICE = "en-US-AriaNeural"

#: Safety valve on spoken length. Replies are read in full up to this; beyond
#: it, speech stops at a sentence boundary and says the rest is in the chat.
#: Generous on purpose - a 200-line `help` listing is the only realistic thing
#: that hits it.
MAX_SPOKEN_CHARS = 1200

#: Voices worth offering in settings - all free Edge neural voices.
SUGGESTED_VOICES = (
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "en-IN-NeerjaNeural",
    "en-IN-PrabhatNeural",
)


def _winmm():
    try:
        return ctypes.windll.winmm  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def _play_with_mci(path: Path, register: Callable[[str | None], None] | None = None) -> bool:
    """Play an audio file synchronously via Windows MCI. False if unavailable.

    ``register`` is handed the MCI alias while playback runs, so another
    thread can stop it mid-sentence - which is what the palm-out "stop"
    gesture needs. Without it the only way to interrupt speech is to wait for
    the sentence to end, which rather defeats the point.
    """
    winmm = _winmm()
    if winmm is None:
        return False
    alias = f"nova{uuid.uuid4().hex[:8]}"
    send = winmm.mciSendStringW
    buffer = ctypes.create_unicode_buffer(128)
    try:
        if send(f'open "{path}" type mpegvideo alias {alias}', None, 0, None) != 0:
            if send(f'open "{path}" alias {alias}', None, 0, None) != 0:
                return False
        if register:
            register(alias)
        send(f"play {alias} wait", None, 0, None)
        return True
    finally:
        if register:
            register(None)
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
        #: MCI alias of whatever is playing right now, so another thread
        #: can cut it off. None when nothing is being spoken.
        self._playing: str | None = None
        self._play_lock = threading.Lock()
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
        spoken = self._for_speech(text)
        if spoken:
            self._queue.put(spoken)

    def stop(self) -> None:
        self._queue.put(None)

    def _register_playing(self, alias: str | None) -> None:
        with self._play_lock:
            self._playing = alias

    @property
    def speaking(self) -> bool:
        with self._play_lock:
            return self._playing is not None

    def silence(self) -> str:
        """Stop talking immediately and drop anything still queued.

        Both halves matter: killing the current clip alone would let the next
        queued sentence start a moment later, which is not what "stop" means
        to anyone making the gesture.
        """
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
                dropped += 1
            except queue.Empty:
                break

        with self._play_lock:
            alias = self._playing
        winmm = _winmm()
        if alias and winmm is not None:
            # MCI 'stop' ends playback; the playing thread then falls through
            # its finally block and closes the alias as usual.
            winmm.mciSendStringW(f"stop {alias}", None, 0, None)
            return "Stopped talking."
        if dropped:
            return f"Dropped {dropped} queued line(s)."
        return "I wasn't saying anything."

    @staticmethod
    def _for_speech(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
        """Prepare a reply to be read aloud in full.

        This used to speak only the first line and then say "plus 6 more
        lines in the chat", which meant most answers were never actually
        heard. Everything is read now; the only edits are ones that make
        speech *sound* right rather than shorter:

        * list bullets become pauses instead of "dash dash dash"
        * paths and file names are left alone (they are often the answer)
        * a hard character cap stops a runaway wall of text from occupying
          the speaker for five minutes - it is a safety valve, not a summary
        """
        lines = [line.rstrip() for line in text.splitlines()]
        cleaned: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Leading list markers read badly out loud.
            stripped = re.sub(r"^\s*[-*•]\s+", "", stripped)
            stripped = re.sub(r"^\s*(\d+)\.\s+", r"\1: ", stripped)
            cleaned.append(stripped)
        spoken = ". ".join(cleaned)
        spoken = re.sub(r"\s+", " ", spoken).strip()
        if len(spoken) <= limit:
            return spoken
        # Cut at a sentence boundary so it does not stop mid-word.
        cut = spoken[:limit]
        boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if boundary > limit * 0.5:
            cut = cut[: boundary + 1]
        return cut + " The rest is in the chat."

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
            return _play_with_mci(path, self._register_playing)
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
