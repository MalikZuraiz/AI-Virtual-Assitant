"""WindowSwitcher: Alt-Tab stepping, plus the "nothing is active" fallback
added for the new "L" shape gesture (index + thumb) - showing it should mean
"reopen my last window" when nothing is currently active, not "switch
windows" applied to an empty desktop.

win32gui/win32con here are the real pywin32 modules (this project only runs
on Windows) with their OS-facing functions monkeypatched out - a live test
would actually Alt-Tab and move focus around the developer's own desktop,
which a test must never do as a side effect.
"""
from types import SimpleNamespace

import win32con
import win32gui

from assistant.vision import actions
from assistant.vision.actions import WindowSwitcher


class _FakeDesktop:
    """An ordered Z-order list of top-level windows plus whichever one is
    currently foreground - the subset of the real desktop actions.py reads."""

    def __init__(self, windows, foreground):
        self.windows = windows  # dicts, Z-order top (most recent) to bottom
        self.foreground = foreground
        self.restored = []
        self.foregrounded = []

    def _find(self, hwnd):
        return next(w for w in self.windows if w["hwnd"] == hwnd)

    def GetForegroundWindow(self):
        return self.foreground

    def GetClassName(self, hwnd):
        return self._find(hwnd)["class_name"]

    def GetWindowText(self, hwnd):
        return self._find(hwnd)["title"]

    def IsWindowVisible(self, hwnd):
        return self._find(hwnd).get("visible", True)

    def GetWindowLong(self, hwnd, _index):
        return win32con.WS_EX_TOOLWINDOW if self._find(hwnd).get("toolwindow") else 0

    def IsIconic(self, hwnd):
        return self._find(hwnd).get("iconic", False)

    def ShowWindow(self, hwnd, _cmd):
        self.restored.append(hwnd)

    def SetForegroundWindow(self, hwnd):
        self.foregrounded.append(hwnd)

    def GetTopWindow(self, _ignored):
        return self.windows[0]["hwnd"] if self.windows else 0

    def GetWindow(self, hwnd, flag):
        assert flag == win32con.GW_HWNDNEXT
        ids = [w["hwnd"] for w in self.windows]
        i = ids.index(hwnd)
        return ids[i + 1] if i + 1 < len(ids) else 0


def _patch_desktop(monkeypatch, desktop):
    for name in (
        "GetForegroundWindow", "GetClassName", "GetWindowText", "IsWindowVisible",
        "GetWindowLong", "IsIconic", "ShowWindow", "SetForegroundWindow",
        "GetTopWindow", "GetWindow",
    ):
        monkeypatch.setattr(win32gui, name, getattr(desktop, name))
    # A real Alt tap/press must never actually fire during a test.
    fake_keyboard = SimpleNamespace(
        press=lambda *_a, **_k: None,
        release=lambda *_a, **_k: None,
        press_and_release=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(actions, "_keyboard", lambda: fake_keyboard)
    return fake_keyboard


DESKTOP_HWND = 1
BROWSER_HWND = 2
EDITOR_HWND = 3
TOOLTIP_HWND = 4


def test_foreground_is_real_window_true_for_a_normal_app(monkeypatch):
    desktop = _FakeDesktop(
        windows=[{"hwnd": BROWSER_HWND, "title": "Browser", "class_name": "Chrome_WidgetWin_1"}],
        foreground=BROWSER_HWND,
    )
    _patch_desktop(monkeypatch, desktop)
    assert actions._foreground_is_real_window() is True


def test_foreground_is_real_window_false_when_desktop_is_focused(monkeypatch):
    desktop = _FakeDesktop(
        windows=[{"hwnd": DESKTOP_HWND, "title": "Program Manager", "class_name": "Progman"}],
        foreground=DESKTOP_HWND,
    )
    _patch_desktop(monkeypatch, desktop)
    assert actions._foreground_is_real_window() is False


def test_step_alt_tabs_normally_when_a_real_window_is_active(monkeypatch):
    desktop = _FakeDesktop(
        windows=[{"hwnd": BROWSER_HWND, "title": "Browser", "class_name": "Chrome_WidgetWin_1"}],
        foreground=BROWSER_HWND,
    )
    keyboard = _patch_desktop(monkeypatch, desktop)
    pressed = []
    keyboard.press = lambda key: pressed.append(("press", key))
    keyboard.press_and_release = lambda key: pressed.append(("tap", key))

    switcher = WindowSwitcher(hold_seconds=0.05)
    result = switcher.step()

    assert result == "Switching window"
    assert ("press", "alt") in pressed
    assert ("tap", "tab") in pressed
    assert desktop.foregrounded == []  # the normal path never calls this itself


def test_step_restores_the_last_window_when_nothing_is_active(monkeypatch):
    """The desktop itself has focus (everything minimised) - Alt-Tab would
    only open the switcher over emptiness, so the gesture should bring back
    whatever was last in use instead."""
    desktop = _FakeDesktop(
        windows=[
            {"hwnd": DESKTOP_HWND, "title": "Program Manager", "class_name": "Progman"},
            {"hwnd": BROWSER_HWND, "title": "Browser", "class_name": "Chrome_WidgetWin_1", "iconic": True},
        ],
        foreground=DESKTOP_HWND,
    )
    keyboard = _patch_desktop(monkeypatch, desktop)
    pressed = []
    keyboard.press = lambda key: pressed.append(("press", key))

    switcher = WindowSwitcher(hold_seconds=0.05)
    result = switcher.step()

    assert result == "Reopened Browser."
    assert BROWSER_HWND in desktop.restored  # un-minimised
    assert desktop.foregrounded == [BROWSER_HWND]
    assert ("press", "alt") not in pressed  # never entered the Alt-Tab path


def test_restore_last_window_skips_tool_windows_and_the_shell(monkeypatch):
    desktop = _FakeDesktop(
        windows=[
            {"hwnd": DESKTOP_HWND, "title": "Program Manager", "class_name": "Progman"},
            {"hwnd": TOOLTIP_HWND, "title": "Tooltip", "class_name": "tooltips_class32", "toolwindow": True},
            {"hwnd": EDITOR_HWND, "title": "Editor", "class_name": "Notepad"},
        ],
        foreground=DESKTOP_HWND,
    )
    _patch_desktop(monkeypatch, desktop)

    result = actions._restore_last_window()

    assert result == "Reopened Editor."
    assert desktop.foregrounded == [EDITOR_HWND]


def test_restore_last_window_reports_when_there_is_nothing_to_reopen(monkeypatch):
    desktop = _FakeDesktop(
        windows=[{"hwnd": DESKTOP_HWND, "title": "Program Manager", "class_name": "Progman"}],
        foreground=DESKTOP_HWND,
    )
    _patch_desktop(monkeypatch, desktop)
    assert actions._restore_last_window() == "No previous window to reopen."


def test_mid_cycle_step_never_re_checks_the_fallback(monkeypatch):
    """Once the switcher is already open (Alt held from a previous step),
    repeating the gesture must keep tabbing through it - re-running the
    "is anything active" check mid-cycle would derail an in-progress cycle
    the moment the switcher UI itself changes what looks foregrounded."""
    desktop = _FakeDesktop(
        windows=[{"hwnd": DESKTOP_HWND, "title": "Program Manager", "class_name": "Progman"}],
        foreground=DESKTOP_HWND,
    )
    keyboard = _patch_desktop(monkeypatch, desktop)
    taps = []
    keyboard.press_and_release = lambda key: taps.append(key)

    switcher = WindowSwitcher(hold_seconds=0.05)
    switcher._alt_down = True  # pretend a cycle is already in progress
    result = switcher.step()

    assert result == "Switching window"
    assert taps == ["tab"]
