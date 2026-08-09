"""Two behaviours the user hit directly: garbled voice input silently having
a conversation with the chat model instead of failing fast, and gesture
feedback going nowhere at all because it used a thread-bound channel from a
thread that was never bound.
"""
import threading
import types

import pytest

from assistant.config import AppConfig
from assistant.core.assistant import Assistant


class _StubLLM:
    """Always 'available', so a real fallback-to-chat bug would be caught."""

    def __init__(self):
        self.calls = []
        self.mode = "fun"
        self.model = "stub"

    def available(self, recheck: bool = False) -> bool:
        return True

    def chat(self, text, on_token=None):
        self.calls.append(text)
        return "a chat reply"


@pytest.fixture()
def assistant():
    a = Assistant(AppConfig.load(), lambda _e: None, enable_reminders=False)
    a.llm = _StubLLM()
    a.ctx.llm = a.llm
    yield a
    a.shutdown()


def test_unmatched_text_never_reaches_the_llm(assistant):
    """The actual bug: any failed match silently became a chat prompt."""
    events = []
    assistant._on_event = events.append
    assistant.handle("generate any penalties before.")
    import time

    time.sleep(0.3)
    assert assistant.llm.calls == []
    assert any("don't have a command" in e.text for e in events if e.kind == "reply")


def test_a_real_prefixed_message_still_reaches_chat(assistant):
    """The fix must not break the one path that IS supposed to reach chat."""
    events = []
    assistant._on_event = events.append
    assistant.handle("nova tell me something")
    import time

    time.sleep(0.3)
    assert assistant.llm.calls == ["tell me something"]


def test_a_matched_command_is_unaffected(assistant):
    events = []
    assistant._on_event = events.append
    assistant.handle("what is the time")
    import time

    time.sleep(0.3)
    assert assistant.llm.calls == []
    assert any(e.kind == "reply" for e in events)


# -- ctx.announce: feedback from a thread the job queue never bound ---------


def test_announce_reaches_the_assistant_from_an_unbound_thread(assistant):
    events = []
    assistant._on_event = events.append

    def _off_thread():
        assistant.ctx.announce("Gesture (fist): previous desktop", speak=True)

    t = threading.Thread(target=_off_thread)
    t.start()
    t.join(timeout=2)

    assert any(
        e.kind == "reply" and e.speak and "previous desktop" in e.text for e in events
    )


def test_announce_respects_speak_false(assistant):
    events = []
    assistant._on_event = events.append
    assistant.ctx.announce("Gestures: camera ready", speak=False)
    assert any(e.text == "Gestures: camera ready" and not e.speak for e in events)


def test_progress_still_no_ops_off_thread_unlike_announce():
    """Documents why announce had to exist: progress() is thread-local by
    design (so two concurrent jobs can't cross-post), and that is exactly
    what silently swallowed every gesture notification."""
    ctx = types.SimpleNamespace()
    from assistant.core.context import Context

    # A bare call with no job bound must not raise - it just goes nowhere.
    real_ctx = Context.__new__(Context)
    real_ctx._local = threading.local()
    real_ctx.progress("this goes nowhere")  # must not raise
