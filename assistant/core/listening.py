"""Speech input: push-to-talk, transcribed locally by faster-whisper.

Push-to-talk rather than an always-on wake word, deliberately. On a 7th-gen
i7 with no GPU, keeping a wake-word model resident burns CPU (and battery)
around the clock for a feature used a handful of times a day. A hotkey costs
nothing until pressed.

Model size is capped at ``tiny``/``base`` with int8 quantisation for the same
reason - ``small`` and up are several times slower on this CPU for very
little accuracy gain on short command phrases.

Recording stops on silence rather than on a fixed timer, so a two-word
command doesn't make you wait five seconds for the transcription to start.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

from assistant.store.paths import models_dir

logger = logging.getLogger("assistant.listening")

SAMPLE_RATE = 16000
CHUNK_MS = 100
#: Below this RMS a chunk counts as silence. Tuned for a laptop mic in a
#: normally-noisy room; raise it if recording never stops on its own.
SILENCE_RMS = 0.012
SILENCE_TO_STOP_MS = 1200
MAX_RECORD_S = 15
MIN_SPEECH_MS = 300

ALLOWED_MODELS = ("tiny", "tiny.en", "base", "base.en")


class SpeechInput:
    """One-shot listener: record until silence, transcribe, hand back text."""

    def __init__(
        self,
        on_text: Callable[[str], None],
        on_state: Optional[Callable[[bool], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        model_size: str = "base.en",
        language: str = "en",
    ) -> None:
        self.on_text = on_text
        self.on_state = on_state or (lambda _listening: None)
        self.on_error = on_error or (lambda _message: None)
        self.model_size = model_size if model_size in ALLOWED_MODELS else "base.en"
        self.language = language
        self._model = None
        self._lock = threading.Lock()
        self._busy = threading.Event()

    # -- public -----------------------------------------------------------
    def listen_once(self) -> None:
        """Start a listen/transcribe cycle on a background thread."""
        if self._busy.is_set():
            return
        self._busy.set()
        threading.Thread(target=self._run, name="stt", daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    # -- worker -----------------------------------------------------------
    def _run(self) -> None:
        try:
            self.on_state(True)
            audio = self._record()
            if audio is None or len(audio) < SAMPLE_RATE * (MIN_SPEECH_MS / 1000):
                self.on_state(False)
                self.on_text("")
                return
            self.on_state(False)
            text = self._transcribe(audio)
            self.on_text(text)
        except Exception as exc:  # noqa: BLE001 - surfaced in chat, never fatal
            logger.exception("Speech input failed")
            self.on_state(False)
            self.on_error(f"Voice input failed: {exc}")
            self.on_text("")
        finally:
            self._busy.clear()

    def _record(self) -> Optional[np.ndarray]:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            self.on_error(f"No audio input available: {exc}")
            return None

        frames_per_chunk = int(SAMPLE_RATE * CHUNK_MS / 1000)
        collected: list[np.ndarray] = []
        silent_ms = 0
        spoken = False

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            for _ in range(int(MAX_RECORD_S * 1000 / CHUNK_MS)):
                chunk, _overflowed = stream.read(frames_per_chunk)
                mono = chunk[:, 0]
                collected.append(mono)
                rms = float(np.sqrt(np.mean(np.square(mono))) if mono.size else 0.0)
                if rms >= SILENCE_RMS:
                    spoken = True
                    silent_ms = 0
                elif spoken:
                    silent_ms += CHUNK_MS
                    if silent_ms >= SILENCE_TO_STOP_MS:
                        break
        if not collected or not spoken:
            return None
        return np.concatenate(collected)

    def _load_model(self):
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                return None
            logger.info("Loading faster-whisper '%s' (int8, cpu)", self.model_size)
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                download_root=str(models_dir()),
            )
            return self._model

    def _transcribe(self, audio: np.ndarray) -> str:
        model = self._load_model()
        if model is None:
            return self._transcribe_fallback(audio)
        segments, _info = model.transcribe(
            audio,
            language=self.language,
            beam_size=1,        # greedy: markedly faster, fine for short commands
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def _transcribe_fallback(self, audio: np.ndarray) -> str:
        """SpeechRecognition's free Google endpoint, if whisper isn't installed."""
        try:
            import speech_recognition as sr
        except ImportError:
            self.on_error(
                "No speech engine installed. Run: pip install faster-whisper"
            )
            return ""
        recogniser = sr.Recognizer()
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        try:
            return recogniser.recognize_google(sr.AudioData(pcm, SAMPLE_RATE, 2))
        except Exception as exc:  # noqa: BLE001
            self.on_error(f"Couldn't transcribe that: {exc}")
            return ""
