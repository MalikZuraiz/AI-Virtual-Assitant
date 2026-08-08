"""Speech input: three ways to talk, all transcribed locally.

* **Tap** the mic button (or the hotkey) - records until you stop talking.
* **Hold** the hotkey - records for exactly as long as you hold it, which is
  the reliable option in a noisy room where silence detection guesses wrong.
* **Live mode** - no hotkey at all. A cheap energy gate watches the mic, and
  only when it actually hears speech does it wake Whisper up.

That gate is what makes live mode viable on a CPU-only laptop: between
utterances the only work is an RMS calculation on a 100ms buffer, so the
machine is essentially idle instead of running a model continuously.

Model size is capped at tiny/base with int8 quantisation for the same
reason - larger models are several times slower on this CPU for very little
gain on short command phrases.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

from assistant.store.paths import models_dir

logger = logging.getLogger("assistant.listening")

#: What Whisper expects. Audio is captured at the microphone's own rate and
#: resampled to this - see SpeechInput.device_rate for why.
SAMPLE_RATE = 16000
CHUNK_MS = 100
#: Floor for the silence threshold. The real threshold is measured from the
#: room at the start of every recording; this is only the minimum.
SILENCE_RMS = 0.008
#: How far above the measured noise floor counts as speech.
NOISE_MARGIN = 3.0
#: How long to listen to the room before deciding what silence sounds like.
NOISE_SAMPLE_MS = 300
SILENCE_TO_STOP_MS = 1000
MAX_RECORD_S = 15
MIN_SPEECH_MS = 350
#: Live mode waits this long for speech to begin before looping again.
LIVE_IDLE_POLL_S = 0.3

ALLOWED_MODELS = ("tiny", "tiny.en", "base", "base.en")

#: Transcripts this short or this generic are Whisper hallucinating on noise.
NOISE_TRANSCRIPTS = {
    "", ".", "you", "thank you", "thanks", "bye", "thank you.", "you.",
    "thanks for watching", "thanks for watching!", "[blank_audio]", "so",
    "um", "uh", "hmm", "mm",
}


def resample(audio: np.ndarray, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Convert mono float32 audio to ``target_rate``.

    Prefers PyAV's libswresample (already installed as a faster-whisper
    dependency, and properly band-limited). Falls back to linear
    interpolation, which is rougher but still far better for Whisper than
    feeding it audio the sound card mangled by capturing at the wrong rate.
    """
    if source_rate == target_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)

    audio = np.ascontiguousarray(audio, dtype=np.float32)
    try:
        import av
        from av.audio.resampler import AudioResampler

        frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = source_rate
        resampler = AudioResampler(format="flt", layout="mono", rate=target_rate)
        pieces = [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        flushed = resampler.resample(None)
        pieces.extend(f.to_ndarray().reshape(-1) for f in flushed)
        out = np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float32)
        if out.size:
            return out.astype(np.float32, copy=False)
    except Exception:  # noqa: BLE001 - fall through to the simple path
        logger.debug("PyAV resample unavailable; using linear interpolation", exc_info=True)

    count = int(round(audio.size * target_rate / source_rate))
    if count <= 1:
        return audio
    source_x = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    target_x = np.linspace(0.0, 1.0, count, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


class SpeechInput:
    """Records and transcribes speech, on demand or continuously."""

    def __init__(
        self,
        on_text: Callable[[str], None],
        on_state: Optional[Callable[[bool], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        model_size: str = "base.en",
        language: str = "en",
        wake_word: str = "",
    ) -> None:
        self.on_text = on_text
        self.on_state = on_state or (lambda _listening: None)
        self.on_error = on_error or (lambda _message: None)
        self.model_size = model_size if model_size in ALLOWED_MODELS else "base.en"
        self.language = language
        #: Optional gate for live mode - only act on speech containing this.
        self.wake_word = wake_word.strip().lower()

        self._model = None
        self._model_lock = threading.Lock()
        self._busy = threading.Event()
        self._holding = threading.Event()
        self._live = threading.Event()
        self._live_thread: threading.Thread | None = None
        self._device_rate = 0
        #: Last measured silence threshold, for the 'mic test' command.
        self.last_threshold = 0.0

    # -- state ------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    @property
    def live(self) -> bool:
        return self._live.is_set()

    # -- tap to talk ------------------------------------------------------
    def listen_once(self) -> None:
        """Record until you stop speaking, then transcribe. Non-blocking."""
        if self._busy.is_set() or self._live.is_set():
            return
        self._busy.set()
        threading.Thread(target=self._run_once, name="stt", daemon=True).start()

    # -- hold to talk -----------------------------------------------------
    def hold_start(self) -> None:
        """Begin recording; keeps going until :meth:`hold_stop`."""
        if self._busy.is_set() or self._live.is_set():
            return
        self._holding.set()
        self._busy.set()
        threading.Thread(target=self._run_hold, name="stt-hold", daemon=True).start()

    def hold_stop(self) -> None:
        self._holding.clear()

    # -- live mode --------------------------------------------------------
    def start_live(self) -> str:
        if self._live.is_set():
            return "Live listening is already on."
        self._live.set()
        self._live_thread = threading.Thread(target=self._run_live, name="stt-live", daemon=True)
        self._live_thread.start()
        extra = f" I'll only act when I hear '{self.wake_word}'." if self.wake_word else ""
        return f"Live listening on - just talk, no hotkey needed.{extra}"

    def stop_live(self) -> str:
        if not self._live.is_set():
            return "Live listening wasn't on."
        self._live.clear()
        self.on_state(False)
        return "Live listening off."

    def shutdown(self) -> None:
        self._live.clear()
        self._holding.clear()

    # -- workers ----------------------------------------------------------
    def _run_once(self) -> None:
        try:
            self.on_state(True)
            audio = self._record_until_silence()
            self.on_state(False)
            self._emit(audio)
        except Exception as exc:  # noqa: BLE001 - surfaced in chat, never fatal
            logger.exception("Speech input failed")
            self.on_state(False)
            self.on_error(f"Voice input failed: {exc}")
            self.on_text("")
        finally:
            self._busy.clear()

    def _run_hold(self) -> None:
        try:
            self.on_state(True)
            audio = self._record_while_held()
            self.on_state(False)
            self._emit(audio)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Hold-to-talk failed")
            self.on_state(False)
            self.on_error(f"Voice input failed: {exc}")
        finally:
            self._holding.clear()
            self._busy.clear()

    def _run_live(self) -> None:
        try:
            while self._live.is_set():
                audio = self._record_until_silence(wait_for_speech=True)
                if not self._live.is_set():
                    break
                if audio is None:
                    continue
                self.on_state(True)
                text = self._transcribe(audio)
                self.on_state(False)
                text = self._clean(text)
                if not text:
                    continue
                if self.wake_word:
                    lowered = text.lower()
                    if self.wake_word not in lowered:
                        continue
                    # Strip the wake word so "nova what's the time" routes as
                    # "what's the time" rather than being sent to the model.
                    index = lowered.index(self.wake_word) + len(self.wake_word)
                    text = text[index:].strip(" ,.:") or text
                self.on_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Live listening stopped")
            self.on_error(f"Live listening stopped: {exc}")
        finally:
            self._live.clear()
            self.on_state(False)

    def _emit(self, audio) -> None:
        if audio is None or len(audio) < SAMPLE_RATE * (MIN_SPEECH_MS / 1000):
            self.on_text("")
            return
        self.on_text(self._clean(self._transcribe(audio)))

    # -- audio ------------------------------------------------------------
    def device_rate(self) -> int:
        """The mic's own sample rate.

        Recording at 16 kHz because that is what Whisper wants is a trap.
        This laptop's Realtek mic reports 44.1 kHz and, asked for 16 kHz,
        hands back clipped garbage - measured: ambient RMS 0.017 with peaks
        at 0.96, versus 0.005/0.27 at its native rate. Silence detection then
        thinks every moment is speech, so recording never stops and Whisper
        only ever sees noise. Capture natively, resample afterwards.
        """
        if self._device_rate:
            return self._device_rate
        rate = SAMPLE_RATE
        try:
            import sounddevice as sd

            info = sd.query_devices(kind="input")
            rate = int(info.get("default_samplerate") or SAMPLE_RATE)
        except Exception:  # noqa: BLE001 - fall back to Whisper's rate
            logger.debug("Could not read the input device rate", exc_info=True)
        self._device_rate = max(8000, rate)
        return self._device_rate

    def _open_stream(self):
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            self.on_error(f"No audio input available: {exc}")
            return None
        rate = self.device_rate()
        try:
            return sd.InputStream(samplerate=rate, channels=1, dtype="float32")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not open the mic at %d Hz (%s); trying 16 kHz", rate, exc)
            self._device_rate = SAMPLE_RATE
            try:
                return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
            except Exception as inner:  # noqa: BLE001
                self.on_error(f"Couldn't open the microphone: {inner}")
                return None

    def _noise_floor(self, stream, rate: int) -> float:
        """Measure the room for a moment and set the silence threshold from it.

        A fixed threshold cannot work across a quiet room and a noisy one, or
        across two microphones with different gain. Sampling the actual floor
        and sitting a few times above it adapts to both.
        """
        frames = int(rate * CHUNK_MS / 1000)
        levels: list[float] = []
        for _ in range(int(NOISE_SAMPLE_MS / CHUNK_MS)):
            chunk, _overflow = stream.read(frames)
            mono = chunk[:, 0]
            if mono.size:
                levels.append(float(np.sqrt(np.mean(np.square(mono)))))
        if not levels:
            return SILENCE_RMS
        floor = float(np.median(levels))
        return max(SILENCE_RMS, floor * NOISE_MARGIN)

    def _record_until_silence(self, wait_for_speech: bool = False):
        """Record one utterance. Returns None if nothing was said."""
        stream = self._open_stream()
        if stream is None:
            return None

        rate = self.device_rate()
        frames = int(rate * CHUNK_MS / 1000)
        collected: list[np.ndarray] = []
        silent_ms = 0
        spoken = False

        with stream:
            threshold = self._noise_floor(stream, rate)
            self.last_threshold = threshold
            for _ in range(int(MAX_RECORD_S * 1000 / CHUNK_MS)):
                if wait_for_speech and not self._live.is_set():
                    return None
                chunk, _overflow = stream.read(frames)
                mono = chunk[:, 0]
                rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
                if rms >= threshold:
                    spoken = True
                    silent_ms = 0
                elif spoken:
                    silent_ms += CHUNK_MS
                # In live mode, don't buffer the silence before speech starts.
                if spoken or not wait_for_speech:
                    collected.append(mono)
                if spoken and silent_ms >= SILENCE_TO_STOP_MS:
                    break
        if not collected or not spoken:
            return None
        return resample(np.concatenate(collected), rate, SAMPLE_RATE)

    def _record_while_held(self):
        """Record for exactly as long as the hotkey is down."""
        stream = self._open_stream()
        if stream is None:
            return None
        rate = self.device_rate()
        frames = int(rate * CHUNK_MS / 1000)
        collected: list[np.ndarray] = []
        with stream:
            elapsed = 0
            while self._holding.is_set() and elapsed < MAX_RECORD_S * 2 * 1000:
                chunk, _overflow = stream.read(frames)
                collected.append(chunk[:, 0])
                elapsed += CHUNK_MS
        if not collected:
            return None
        return resample(np.concatenate(collected), rate, SAMPLE_RATE)

    # -- transcription ----------------------------------------------------
    def _load_model(self):
        with self._model_lock:
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

    def preload(self) -> None:
        """Load the model ahead of first use, so the first phrase isn't slow."""
        threading.Thread(target=self._load_model, name="stt-preload", daemon=True).start()

    @staticmethod
    def _clean(text: str) -> str:
        """Drop Whisper's classic hallucinations on near-silent input."""
        stripped = (text or "").strip()
        if stripped.lower().strip(" .!?") in NOISE_TRANSCRIPTS:
            return ""
        return stripped

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
            self.on_error("No speech engine installed. Run: pip install faster-whisper")
            return ""
        recogniser = sr.Recognizer()
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        try:
            return recogniser.recognize_google(sr.AudioData(pcm, SAMPLE_RATE, 2))
        except Exception as exc:  # noqa: BLE001
            self.on_error(f"Couldn't transcribe that: {exc}")
            return ""
