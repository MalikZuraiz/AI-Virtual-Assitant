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

    pyttsx3's ``runAndWait`` blocks, so a background worker + queue keeps
    the GUI responsive while replies are spoken.
    """

    def __init__(self, rate: int = 190, voice_index: int = 1) -> None:
        self.available = False
        self._engine = None
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._worker: threading.Thread | None = None

        try:
            import pyttsx3

            engine = pyttsx3.init("sapi5")
            voices = engine.getProperty("voices")
            if voices:
                index = voice_index if voice_index < len(voices) else 0
                engine.setProperty("voice", voices[index].id)
            engine.setProperty("rate", rate)
            self._engine = engine
            self.available = True
        except Exception:
            logger.warning("Text-to-speech unavailable (no SAPI voices found)", exc_info=True)
            return

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:  # sentinel for shutdown
                break
            try:
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                logger.exception("Speech playback failed")

    def say(self, text: str) -> None:
        if not self.available or not text:
            return
        self._queue.put(text)

    def stop(self) -> None:
        if self.available and self._worker:
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
