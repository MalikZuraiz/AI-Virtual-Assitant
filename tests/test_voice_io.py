"""Cover for the speech fixes.

Both bugs here were "it just doesn't work" from the user's side, and both had
concrete measurable causes: audio captured at the wrong sample rate, and
replies that were summarised away before reaching the speaker.
"""
import numpy as np
import pytest

from assistant.core.listening import SAMPLE_RATE, resample
from assistant.core.voice import MAX_SPOKEN_CHARS, TextToSpeech


# -- resampling --------------------------------------------------------------


def test_resample_preserves_duration():
    """44.1 kHz in, 16 kHz out, same number of seconds."""
    seconds = 2.0
    source_rate = 44100
    audio = np.sin(2 * np.pi * 440 * np.arange(int(source_rate * seconds)) / source_rate)
    out = resample(audio.astype(np.float32), source_rate, SAMPLE_RATE)
    assert abs(len(out) / SAMPLE_RATE - seconds) < 0.02


def test_resample_is_a_no_op_at_the_target_rate():
    audio = np.zeros(1000, dtype=np.float32)
    assert resample(audio, SAMPLE_RATE, SAMPLE_RATE) is audio


def test_resample_handles_empty_audio():
    assert resample(np.empty(0, dtype=np.float32), 44100, SAMPLE_RATE).size == 0


def test_resample_keeps_the_signal_not_just_the_length():
    """A tone must still be a tone afterwards, not noise."""
    source_rate = 48000
    audio = (0.5 * np.sin(2 * np.pi * 440 * np.arange(source_rate) / source_rate)).astype(np.float32)
    out = resample(audio, source_rate, SAMPLE_RATE)
    # Energy per sample should be roughly preserved for a pure tone.
    assert 0.25 < float(np.sqrt(np.mean(out**2))) < 0.45
    assert float(np.max(np.abs(out))) < 1.01  # nothing clipped


@pytest.mark.parametrize("rate", [22050, 44100, 48000])
def test_resample_from_common_mic_rates(rate):
    audio = np.zeros(rate, dtype=np.float32)
    out = resample(audio, rate, SAMPLE_RATE)
    assert abs(len(out) - SAMPLE_RATE) < SAMPLE_RATE * 0.02


# -- speech output -----------------------------------------------------------


def test_every_line_is_spoken_not_summarised():
    """The old code spoke line one and said "plus N more lines in the chat"."""
    reply = "4 files today:\n  - alpha.xlsx\n  - beta.xlsx\n  - gamma.xlsx"
    spoken = TextToSpeech._for_speech(reply)
    for word in ("alpha", "beta", "gamma"):
        assert word in spoken
    assert "more line" not in spoken


def test_bullets_become_pauses():
    spoken = TextToSpeech._for_speech("Totals:\n  - one\n  - two")
    assert "-" not in spoken
    assert "one. two" in spoken


def test_numbered_items_read_as_numbers():
    spoken = TextToSpeech._for_speech("1. first\n2. second")
    assert "1: first" in spoken
    assert "2: second" in spoken


def test_blank_lines_do_not_become_pauses():
    spoken = TextToSpeech._for_speech("one\n\n\ntwo")
    assert spoken == "one. two"


def test_very_long_text_is_capped_at_a_sentence_boundary():
    long_text = ". ".join(f"Sentence number {i}" for i in range(400))
    spoken = TextToSpeech._for_speech(long_text)
    assert len(spoken) <= MAX_SPOKEN_CHARS + 40
    assert spoken.endswith("The rest is in the chat.")
    # Cut cleanly, not mid-word.
    assert not spoken.removesuffix(" The rest is in the chat.").endswith("Sentenc")


def test_short_text_is_untouched():
    assert TextToSpeech._for_speech("Done.") == "Done."


def test_empty_text_speaks_nothing():
    assert TextToSpeech._for_speech("") == ""
    assert TextToSpeech._for_speech("   \n  ") == ""
