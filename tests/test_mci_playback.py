"""_play_with_mci(): the actual Windows MCI control flow muting depends on.

This is the piece the mute feature's own tests (test_tts_mute.py) never
touch - they stub _speak_once out entirely, so the previous "play ... wait"
call, and the fact that a "stop" from another thread wasn't reliably
interrupting it, was never exercised by any test at all. That gap is why
"mute doesn't actually stop the speaking" shipped unnoticed.

No real audio device or file is needed here - winmm is faked so these run
everywhere the real one might not (CI, no sound card), while still pinning
the exact sequence of MCI commands the fix depends on: play is started
without "wait", status is polled instead of blocking, and should_stop is
checked before playback even starts.
"""
from pathlib import Path

from assistant.core import voice


class _FakeWinmm:
    """Records every command; "status" replies "playing" for a few calls
    and then "stopped", mimicking a short clip finishing on its own."""

    def __init__(self, plays_for: int = 3):
        self.calls: list[str] = []
        self.plays_for = plays_for
        self._status_calls = 0

    def mciSendStringW(self, command, buffer, buffer_size, callback):
        self.calls.append(command)
        if command.startswith("status "):
            self._status_calls += 1
            value = "playing" if self._status_calls <= self.plays_for else "stopped"
            if buffer is not None:
                buffer.value = value
        return 0

    def _command(self, prefix):
        return [c for c in self.calls if c.startswith(prefix)]


def _patch_winmm(monkeypatch, fake, sleeps=None):
    monkeypatch.setattr(voice, "_winmm", lambda: fake)
    if sleeps is not None:
        monkeypatch.setattr(voice.time, "sleep", lambda seconds: sleeps.append(seconds))


def test_plays_without_a_blocking_wait_command(monkeypatch):
    """The whole bug: "play ... wait" blocks this thread, and a "stop" sent
    from elsewhere could not reliably interrupt it. play must be issued
    without "wait" so this thread can poll and react to should_stop itself."""
    fake = _FakeWinmm(plays_for=1)
    _patch_winmm(monkeypatch, fake, sleeps=[])
    voice._play_with_mci(Path("clip.mp3"))
    play_commands = fake._command("play ")
    assert play_commands == ["play " + play_commands[0].split()[1]]
    assert "wait" not in play_commands[0]


def test_lets_a_clip_finish_naturally_when_never_asked_to_stop(monkeypatch):
    fake = _FakeWinmm(plays_for=3)
    _patch_winmm(monkeypatch, fake, sleeps=[])
    result = voice._play_with_mci(Path("clip.mp3"), should_stop=lambda: False)
    assert result is True
    assert not fake._command("stop ")
    assert len(fake._command("status ")) == 4  # 3x "playing", 1x "stopped"


def test_should_stop_becoming_true_mid_playback_sends_an_explicit_stop(monkeypatch):
    """This is the exact path muting relies on: the SAME thread that started
    playback notices should_stop() and stops it itself, rather than trusting
    an external thread's stop to land inside a blocking call."""
    fake = _FakeWinmm(plays_for=10)
    triggered = {"n": 0}

    def should_stop():
        triggered["n"] += 1
        return triggered["n"] >= 2  # true on the second poll

    _patch_winmm(monkeypatch, fake, sleeps=[])
    voice._play_with_mci(Path("clip.mp3"), should_stop=should_stop)
    assert fake._command("stop ")
    # Stopped well before the clip would have finished on its own (10
    # "playing" polls available, but only 1 status check should have run).
    assert len(fake._command("status ")) <= 1


def test_already_muted_before_playback_starts_never_issues_play_at_all(monkeypatch):
    """Muting can land while a clip is still being synthesised (edge-tts's
    network round trip); should_stop is checked once before "play" is ever
    sent, so a clip that finished synthesising after the mute never plays a
    single frame instead of a blip before catching up on the next poll."""
    fake = _FakeWinmm(plays_for=5)
    _patch_winmm(monkeypatch, fake, sleeps=[])
    voice._play_with_mci(Path("clip.mp3"), should_stop=lambda: True)
    assert not fake._command("play ")
    assert not fake._command("status ")


def test_closes_the_alias_even_when_stopped_early(monkeypatch):
    fake = _FakeWinmm(plays_for=10)
    _patch_winmm(monkeypatch, fake, sleeps=[])
    voice._play_with_mci(Path("clip.mp3"), should_stop=lambda: True)
    assert fake._command("close ")


def test_register_is_called_with_the_alias_then_cleared(monkeypatch):
    fake = _FakeWinmm(plays_for=1)
    _patch_winmm(monkeypatch, fake, sleeps=[])
    seen = []
    voice._play_with_mci(Path("clip.mp3"), register=seen.append)
    assert seen[0] is not None and seen[0].startswith("nova")
    assert seen[-1] is None
