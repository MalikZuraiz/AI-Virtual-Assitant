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
import time
import uuid
from pathlib import Path
from typing import Callable

logger = logging.getLogger("assistant.voice")

DEFAULT_VOICE = "en-US-AriaNeural"

#: Safety valve on spoken length. Replies are read in full up to this; beyond
#: it, speech stops at a sentence boundary and says the rest is in the chat.
MAX_SPOKEN_CHARS = 1200

#: A line that looks like "- foo", "* foo", "1. foo" or "3) foo" - a list
#: item, not a sentence. Matched against the *raw* line, before the
#: bullet-stripping pass that makes list items readable when they ARE spoken.
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.):])\s+")

#: A reply with at least this many list-shaped lines is summarised instead
#: of read item by item - three is enough to tell "a couple of related facts"
#: (fine to read) apart from "a picker/listing" (not fine - see _for_speech).
LIST_ITEM_THRESHOLD = 3

#: Matches a URL for shortening in speech - see TextToSpeech._shorten_urls.
_URL_RE = re.compile(r"https?://(?:www\.)?([^\s/]+)(?:/\S*)?")

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


def _play_with_mci(
    path: Path,
    register: Callable[[str | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Play an audio file via Windows MCI, polling rather than blocking on
    "play ... wait". False if unavailable.

    A blocking "wait" call, cut short by an MCI "stop" sent from a *different*
    thread, turned out not to be reliable enough to build muting on: the
    interrupting thread has no way to confirm the blocked call ever actually
    woke up, and in practice it often did not - speech kept going straight
    through a mute. Starting playback without "wait" and polling this same
    thread's own status instead means the thread that owns the MCI device is
    also the one deciding when to stop it, rather than depending on a
    cross-thread interrupt of a call it is blocked inside.

    ``register`` is handed the MCI alias while playback runs, so
    :meth:`TextToSpeech._interrupt_current` can also issue a direct MCI stop
    (used by ``silence()``) - a stop against a non-blocking play is the
    well-supported case; it is only interrupting a *blocking* wait that is
    unreliable. ``should_stop`` is polled a few times a second and, if it is
    ever true, ends playback immediately - checked once before playback even
    starts too, so muting during the synthesis that happens before this is
    called never starts the clip at all.
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
        if should_stop is not None and should_stop():
            return True
        send(f"play {alias}", None, 0, None)
        status = ctypes.create_unicode_buffer(32)
        while True:
            if should_stop is not None and should_stop():
                send(f"stop {alias}", None, 0, None)
                break
            send(f"status {alias} mode", status, 32, None)
            if status.value.strip().lower() != "playing":
                break
            time.sleep(0.05)
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
        #: The pyttsx3 Engine mid-utterance, if that backend is in use - the
        #: offline fallback needs its own interrupt handle, since it has no
        #: MCI alias to stop.
        self._active_engine = None
        self._play_lock = threading.Lock()
        #: Set means "may speak"; cleared means "hold new lines until
        #: unmuted" - see set_muted(). A held-finger gesture toggles this.
        self._unmuted = threading.Event()
        self._unmuted.set()
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

        stopped = self._interrupt_current()
        if stopped:
            return "Stopped talking."
        if dropped:
            return f"Dropped {dropped} queued line(s)."
        return "I wasn't saying anything."

    def set_muted(self, muted: bool) -> None:
        """Mute (cut off whatever is playing, hold new lines) or release.

        This deliberately does not try to pause and resume the interrupted
        clip mid-word - cross-thread MCI pause/resume is not reliable across
        every device/driver, and getting speech permanently stuck (silent
        forever, or stuck fighting a half-resumed clip) would be far worse
        than restarting at the next sentence. Muting cuts the current line
        outright; unmuting lets the *next* queued line play normally. Unlike
        :meth:`silence`, the queue itself is never touched - muting is meant
        to be momentary and reversible, so nothing queued while muted is
        lost.
        """
        if muted:
            self._unmuted.clear()
            self._interrupt_current()
        else:
            self._unmuted.set()

    @property
    def muted(self) -> bool:
        return not self._unmuted.is_set()

    def _interrupt_current(self) -> bool:
        """Stop whatever is playing right now, on either backend. Returns
        True if anything was actually interrupted."""
        with self._play_lock:
            alias, engine = self._playing, self._active_engine
        winmm = _winmm()
        stopped = False
        if alias and winmm is not None:
            # MCI 'stop' ends playback; the playing thread then falls through
            # its finally block and closes the alias as usual.
            winmm.mciSendStringW(f"stop {alias}", None, 0, None)
            stopped = True
        if engine is not None:
            try:
                engine.stop()
                stopped = True
            except Exception:  # noqa: BLE001 - best-effort interrupt
                logger.debug("Could not stop the offline TTS engine", exc_info=True)
        return stopped

    @staticmethod
    def _shorten_urls(text: str) -> str:
        """"Opened https://www.youtube.com" -> "Opened youtube.com" to say.

        A URL is exactly the kind of thing that is useful to *see* (you might
        click it) and tedious to *hear* spelled out. Only touches what gets
        spoken - the chat text keeps the full address.
        """
        return _URL_RE.sub(lambda m: m.group(1), text)

    @classmethod
    def _for_speech(cls, text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
        """Prepare a reply to be read aloud - in full, unless it's a list.

        Two failure modes, fixed in opposite directions of the same mistake:
        speaking *nothing* of a reply (the old "plus 6 more lines" summary)
        and speaking *everything* of one (reading a 13-file picker's every
        filename, size and date aloud, one by one). Both are wrong for the
        same reason - the spoken reply should match how a person would
        actually say it out loud, not mechanically transcribe the chat text.

        A short confirmation ("Opened Chrome") is read in full - that IS how
        you'd say it. A numbered list is not read as a list - nobody reads
        "one: sheet three, two: sheet two, three: sheet one..." aloud; they
        say "I've listed a few files, take a look" and let you glance at the
        screen. So a reply with several list-shaped lines is reduced to its
        first non-list line (the question/header) instead of enumerated.
        """
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        item_lines = [line for line in lines if _LIST_ITEM_RE.match(line)]
        if len(item_lines) >= LIST_ITEM_THRESHOLD:
            header = next((line for line in lines if not _LIST_ITEM_RE.match(line)), "")
            header = cls._shorten_urls(header).rstrip(" .!?") or "Here's a list"
            return f"{header}. Check the chat for the full list."

        cleaned: list[str] = []
        for line in lines:
            # Leading list markers read badly out loud.
            stripped = re.sub(r"^\s*[-*•]\s+", "", line)
            stripped = re.sub(r"^\s*(\d+)\.\s+", r"\1: ", stripped)
            cleaned.append(stripped)
        spoken = cls._shorten_urls(". ".join(cleaned))
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
            # Checked *after* the shutdown-sentinel check above, never
            # before - stop() must always be able to end this thread even
            # while muted, or a stuck mute would leak the thread on exit too.
            self._unmuted.wait()
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
            return _play_with_mci(path, self._register_playing, should_stop=lambda: self.muted)
        finally:
            path.unlink(missing_ok=True)

    def _speak_pyttsx3(self, text: str) -> None:
        if self.muted:
            # Muted while still on the way to this backend (e.g. edge-tts's
            # synthesis step failed right as the hold engaged) - don't start
            # a clip nothing is meant to hear.
            return
        try:
            import pyttsx3
            from pyttsx3.engine import Engine
        except ImportError:
            logger.warning("No TTS backend available; staying silent")
            return
        # Engine(...) directly, NOT pyttsx3.init() - see the module docstring.
        engine = Engine(driverName=None, debug=False)
        with self._play_lock:
            self._active_engine = engine
        try:
            engine.setProperty("rate", 185)
            engine.say(text)
            engine.runAndWait()
        finally:
            with self._play_lock:
                self._active_engine = None
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        del pyttsx3  # keep the import meaningful for linters
