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


def test_a_short_reply_is_still_spoken_in_full():
    """The earlier bug: only line one was ever spoken, with "plus N more
    lines in the chat" instead of the actual content. A reply with too few
    list items to count as a listing (see the threshold tests below) must
    still be read in full, not summarised away."""
    reply = "Totals:\n  - one\n  - two"
    spoken = TextToSpeech._for_speech(reply)
    assert "one" in spoken and "two" in spoken
    assert "more line" not in spoken
    assert "Check the chat" not in spoken


def test_bullets_become_pauses():
    spoken = TextToSpeech._for_speech("Totals:\n  - one\n  - two")
    assert "-" not in spoken
    assert "one. two" in spoken


def test_numbered_items_read_as_numbers():
    spoken = TextToSpeech._for_speech("1. first\n2. second")
    assert "1: first" in spoken
    assert "2: second" in spoken


def test_a_real_listing_is_summarised_not_read_item_by_item():
    """The other half of the same bug, in the opposite direction: reading a
    13-file picker's every filename, size and date aloud is exactly as
    wrong as reading nothing. Three or more list-shaped lines means this
    is a picker/listing, not a short reply - speak the question, not the
    options."""
    reply = (
        "Which file(s) should 'pending penalties report' use?\n"
        "  1. sheet3.xlsx   Downloads · 37 KB · 08 Aug\n"
        "  2. sheet2.xlsx   Downloads · 495 KB · 08 Aug\n"
        "  3. sheet1.xlsx   Downloads · 343 KB · 08 Aug\n"
        "  4. sheet4.xlsx   Downloads · 101 KB · 08 Aug\n"
        "Say a number - or several ('1 2 3')."
    )
    spoken = TextToSpeech._for_speech(reply)
    assert "sheet3.xlsx" not in spoken
    assert "sheet2.xlsx" not in spoken
    assert "Which file" in spoken
    assert "Check the chat" in spoken


def test_help_style_output_is_summarised():
    reply = "I know 211 commands right now:\n" + "\n".join(
        f"  - command {i} - does a thing" for i in range(20)
    )
    spoken = TextToSpeech._for_speech(reply)
    assert "command 5" not in spoken
    assert "211 commands" in spoken


def test_two_list_items_is_not_enough_to_summarise():
    """The threshold matters: don't over-trigger on an ordinary two-item
    answer that happens to use dashes."""
    reply = "Two things:\n  - the first thing\n  - the second thing"
    spoken = TextToSpeech._for_speech(reply)
    assert "first thing" in spoken
    assert "Check the chat" not in spoken


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


# -- URLs read naturally, not spelled out ------------------------------------


def test_a_url_is_shortened_to_its_domain():
    """The other complaint: 'Opened https://www.youtube.com' used to be
    read out character-by-character-ish by the TTS engine. The chat text
    keeps the full URL; only speech gets the short form."""
    spoken = TextToSpeech._for_speech("Opened https://www.youtube.com")
    assert spoken == "Opened youtube.com"


def test_a_url_with_a_path_only_speaks_the_domain():
    spoken = TextToSpeech._for_speech("Opened https://github.com/anthropics/claude-code")
    assert spoken == "Opened github.com"


def test_a_bare_domain_without_www_is_also_shortened():
    spoken = TextToSpeech._for_speech("Saved https://example.com as example")
    assert "https://" not in spoken
    assert "example.com" in spoken


def test_a_url_inside_a_summarised_list_header_is_still_shortened():
    reply = (
        "See https://example.com/dashboard for details:\n"
        "  1. one\n  2. two\n  3. three"
    )
    spoken = TextToSpeech._for_speech(reply)
    assert "https://" not in spoken
    assert "example.com" in spoken
