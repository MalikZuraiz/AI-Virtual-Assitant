"""Text-to-speech and speech-to-text wrappers.

Both classes are defensive by design: if the underlying hardware/driver
(SAPI voices, a microphone, PyAudio) isn't available, ``.available`` is
False and calls become safe no-ops instead of crashing the app. The GUI
uses ``.available`` to grey out the relevant button and show a status hint.
"""
from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger("assistant.speech")


class TextToSpeech:
    """pyttsx3-backed speaker running on its own worker thread.

    Two real reliability issues, found by actually timing playback rather
    than trusting "it didn't raise an exception":

    1. pyttsx3's sapi5 driver wraps a COM object, and COM objects are
       apartment-threaded - creating one on one thread and calling it from
       another is unreliable. So every call into pyttsx3 happens on this
       one worker thread for the lifetime of the app.
    2. The one that actually explained "only the first reply gets spoken":
       ``pyttsx3.init()`` caches engines in a module-level dict keyed by
       driver name and returns the *same* instance on every call - so even
       code that looks like it's creating a fresh engine per utterance is
       silently reusing the first one. Measured directly: the 2nd/3rd calls
       on a cached engine return almost instantly with no audio, while
       bypassing the cache (constructing ``pyttsx3.engine.Engine`` directly
       instead of calling ``pyttsx3.init()``) plays every single utterance
       at its correct duration, every time. So each utterance gets its own
       ``Engine`` instance, bypassing ``pyttsx3.init()``'s cache entirely.
    """

    def __init__(self, rate: int = 190, voice_index: int = 1) -> None:
        self.available = False
        self._rate = rate
        self._voice_index = voice_index
        self._voice_id: str | None = None
        self._engine_cls = None
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._ready = threading.Event()

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self._ready.wait(5)  # let the probe below succeed/fail before .available is read

    def _run(self) -> None:
        try:
            from pyttsx3.engine import Engine

            self._engine_cls = Engine
            probe = Engine("sapi5", debug=False)
            voices = probe.getProperty("voices")
            if voices:
                index = self._voice_index if self._voice_index < len(voices) else 0
                self._voice_id = voices[index].id
            probe.stop()
            del probe
            self.available = True
        except Exception:
            logger.warning("Text-to-speech unavailable (no SAPI voices found)", exc_info=True)
            self._ready.set()
            return

        self._ready.set()
        while True:
            text = self._queue.get()
            if text is None:  # sentinel for shutdown
                break
            try:
                engine = self._engine_cls("sapi5", debug=False)
                if self._voice_id:
                    engine.setProperty("voice", self._voice_id)
                engine.setProperty("rate", self._rate)
                engine.say(text)
                engine.runAndWait()
                engine.stop()
                del engine
            except Exception:
                logger.exception("Speech playback failed")

    def say(self, text: str) -> None:
        if not self.available or not text:
            return
        self._queue.put(text)

    def stop(self) -> None:
        if self.available:
            self._queue.put(None)


class SpeechListener:
    """Push-to-talk microphone capture via SpeechRecognition + PyAudio."""

    def __init__(self) -> None:
        self.available = False
        self._recognizer = None
        self._mic = None

        try:
            import speech_recognition as sr

            self._sr = sr
            self._recognizer = sr.Recognizer()
            self._mic = sr.Microphone()
            with self._mic as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
            self.available = True
        except Exception:
            logger.warning("Microphone/speech recognition unavailable", exc_info=True)

    def listen_once(self, phrase_time_limit: int = 8) -> str | None:
        """Record one phrase and transcribe it. Returns None on failure/timeout."""
        if not self.available:
            return None
        try:
            with self._mic as source:
                audio = self._recognizer.listen(source, timeout=5, phrase_time_limit=phrase_time_limit)
            return self._recognizer.recognize_google(audio)
        except self._sr.WaitTimeoutError:
            return None
        except self._sr.UnknownValueError:
            return None
        except Exception:
            logger.exception("Speech recognition failed")
            return None
