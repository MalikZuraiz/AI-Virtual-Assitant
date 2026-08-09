"""The noise-floor calibration bug that made speech recognition silently fail.

Measured live on this machine: the old calibration (3 samples, a straight
median, no ceiling) came back with a threshold of 0.168 while genuine ambient
room noise peaked at 0.09 over the following three seconds - meaning real
speech had to exceed 0.168 RMS just to be noticed at all, and normal speech
essentially never does on a laptop mic at a comfortable distance. The single
biggest suspect for that spike: the physical click of pressing the button or
hotkey that starts recording, landing inside a 300ms window sampled only
three times.

These tests use a fake stream so they run without a real microphone, and
they pin the two properties that fix relies on: a loud instant right at the
start must not raise the bar, and the bar can never rise past a hard ceiling
regardless of what it measures.
"""
import numpy as np
import pytest

from assistant.core.listening import (
    MAX_SILENCE_THRESHOLD,
    SILENCE_RMS,
    SpeechInput,
)


class _FakeStream:
    """Feeds fixed-RMS white noise for a sequence of (duration_ms, rms) segments."""

    def __init__(self, rate: int, segments: list[tuple[float, float]]):
        self.rate = rate
        self.segments = segments
        self.pos_ms = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _rms_at(self, pos_ms: float) -> float:
        acc = 0.0
        for duration_ms, rms in self.segments:
            if pos_ms < acc + duration_ms:
                return rms
            acc += duration_ms
        return self.segments[-1][1]

    def read(self, frames: int):
        duration_ms = frames / self.rate * 1000
        rms = self._rms_at(self.pos_ms)
        rng = np.random.default_rng(int(self.pos_ms * 1000) + 1)
        chunk = (rng.standard_normal(frames).astype(np.float32) * rms)
        self.pos_ms += duration_ms
        return chunk.reshape(-1, 1), False


@pytest.fixture()
def listener():
    return SpeechInput(on_text=lambda _t: None)


def test_a_loud_click_at_the_very_start_does_not_raise_the_threshold(listener):
    """The actual bug: a button-press transient right when recording opens."""
    stream = _FakeStream(16000, [(80, 0.5), (2000, 0.005)])  # click, then quiet room
    with stream:
        threshold = listener._noise_floor(stream, 16000)
    # A real utterance is comfortably above quiet-room level; it must clear this.
    assert threshold < 0.02


def test_threshold_never_exceeds_the_hard_ceiling_no_matter_how_bad_the_input(listener):
    stream = _FakeStream(16000, [(400, 0.9)])  # sustained loud noise throughout
    with stream:
        threshold = listener._noise_floor(stream, 16000)
    assert threshold <= MAX_SILENCE_THRESHOLD


def test_a_genuinely_quiet_room_gets_a_low_threshold(listener):
    stream = _FakeStream(16000, [(400, 0.002)])
    with stream:
        threshold = listener._noise_floor(stream, 16000)
    assert threshold == pytest.approx(SILENCE_RMS, abs=1e-6)


def test_moderate_steady_noise_still_lands_well_under_speech_level(listener):
    """A quiet room with a computer fan or hum, not a click - the common case."""
    stream = _FakeStream(16000, [(400, 0.01)])
    with stream:
        threshold = listener._noise_floor(stream, 16000)
    assert threshold < 0.03  # comfortably below a normal speaking voice


def test_speech_level_audio_clears_the_calibrated_threshold(listener):
    """End-to-end: after a click-contaminated calibration, real speech still counts."""
    stream = _FakeStream(16000, [(80, 0.5), (400, 0.005)])
    with stream:
        threshold = listener._noise_floor(stream, 16000)
    speech_rms = 0.03
    assert speech_rms >= threshold


def test_an_empty_stream_falls_back_to_the_floor_constant(listener):
    class _EmptyStream(_FakeStream):
        def read(self, frames):
            self.pos_ms += frames / self.rate * 1000
            return np.empty((0, 1), dtype=np.float32), False

    stream = _EmptyStream(16000, [])
    with stream:
        threshold = listener._noise_floor(stream, 16000)
    assert threshold == SILENCE_RMS
