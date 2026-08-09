"""The hold-to-mute mechanism: engage instantly, interrupt whatever is
playing, hold new lines until released - without ever risking the "stuck
muted forever" failure a true MCI pause/resume across threads would risk.

No real network synthesis or audio playback here - ``_speak_once`` is
stubbed so these run in milliseconds and never depend on an internet
connection or a sound device being present.
"""
import threading
import time

import pytest

from assistant.core.voice import TextToSpeech


@pytest.fixture()
def tts():
    engine = TextToSpeech(enabled=True)
    calls = []

    def _fake_speak_once(text):
        calls.append(text)
        time.sleep(0.05)  # long enough to observe "currently speaking"

    engine._speak_once = _fake_speak_once
    engine.calls = calls
    yield engine
    engine.stop()


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_starts_unmuted(tts):
    assert tts.muted is False


def test_set_muted_toggles_the_property(tts):
    tts.set_muted(True)
    assert tts.muted is True
    tts.set_muted(False)
    assert tts.muted is False


def test_a_line_queued_while_muted_does_not_speak_until_released(tts):
    tts.set_muted(True)
    tts.say("hello")
    time.sleep(0.15)
    assert tts.calls == []  # blocked, not dropped

    tts.set_muted(False)
    assert _wait_until(lambda: tts.calls == ["hello"])


def test_muting_interrupts_whatever_is_currently_playing(tts):
    """Muting mid-utterance must stop it, not just block what comes next."""
    stopped = []

    def _slow_speak(text):
        # Simulate a long clip; set_muted's _interrupt_current should cut
        # this off rather than letting it run to completion.
        for _ in range(50):
            time.sleep(0.01)
            if tts.muted:
                stopped.append(text)
                return

    tts._speak_once = _slow_speak
    tts.say("a long reply")
    assert _wait_until(lambda: tts.calls == [] and True)  # give the thread a moment to start
    time.sleep(0.05)
    tts.set_muted(True)
    assert _wait_until(lambda: stopped == ["a long reply"])


def test_unmuting_lets_the_next_queued_line_speak_not_the_interrupted_one(tts):
    """Deliberate design choice: no mid-sentence resume - the next line in
    the queue speaks fresh, rather than trying to continue a cut-off clip."""
    tts.set_muted(True)
    tts.say("first")
    tts.say("second")
    time.sleep(0.1)
    assert tts.calls == []

    tts.set_muted(False)
    assert _wait_until(lambda: tts.calls == ["first", "second"], timeout=2.0)


def test_muting_does_not_drop_the_queue_unlike_silence(tts):
    tts.set_muted(True)
    tts.say("keep me")
    time.sleep(0.1)
    tts.set_muted(False)
    assert _wait_until(lambda: tts.calls == ["keep me"])


def test_shutdown_is_never_blocked_by_mute(tts):
    """The shutdown sentinel must reach _loop even while muted, or stop()
    would hang the thread forever - the exact 'stuck' failure mode this
    whole feature exists to avoid elsewhere."""
    tts.set_muted(True)
    tts.stop()
    # join with a short timeout - if the sentinel were gated behind the
    # mute Event, this thread would never finish.
    tts._thread.join(timeout=1.0)
    assert not tts._thread.is_alive()


def test_interrupt_current_is_a_noop_when_nothing_is_playing(tts):
    assert tts._interrupt_current() is False


def test_repeated_mute_calls_are_harmless(tts):
    tts.set_muted(True)
    tts.set_muted(True)
    tts.set_muted(False)
    tts.set_muted(False)
    assert tts.muted is False


def test_muting_from_another_thread_is_observed_promptly(tts):
    """Mirrors how the gesture camera thread actually calls this."""
    tts.set_muted(True)
    tts.say("queued while muted")

    def _unmute_soon():
        time.sleep(0.05)
        tts.set_muted(False)

    threading.Thread(target=_unmute_soon, daemon=True).start()
    assert _wait_until(lambda: tts.calls == ["queued while muted"], timeout=2.0)
