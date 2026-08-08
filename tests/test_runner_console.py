"""Console-mode execution: the fix for scripts that prompt.

The PED bot worked when launched by hand and failed through the assistant.
Cause: captured runs pass ``stdin=DEVNULL``, so the script's ``input()`` call
raised ``EOFError`` immediately. These tests pin both halves of that.
"""
import sys
from pathlib import Path

import pytest

from assistant.core.runner import Runnable, execute


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "job.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_prompting_script_fails_when_output_is_captured(tmp_path):
    """Documents the failure, so nobody 'simplifies' console mode away."""
    _script(tmp_path, "input('give me something: ')\n")
    result = execute(
        Runnable(name="prompty", run_as="python", target="job.py",
                 working_directory=str(tmp_path), timeout=30)
    )
    assert not result.ok
    assert "EOFError" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NEW_CONSOLE is Windows-only")
def test_console_mode_runs_and_is_waited_for(tmp_path):
    marker = tmp_path / "done.txt"
    _script(tmp_path, f"open(r'{marker}', 'w').write('ok')\n")
    result = execute(
        Runnable(name="job", run_as="python", target="job.py",
                 working_directory=str(tmp_path), timeout=60, console=True)
    )
    assert result.ok
    # Waited for it: the side effect is already on disk when execute returns.
    assert marker.read_text() == "ok"


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NEW_CONSOLE is Windows-only")
def test_console_mode_reports_a_failing_exit_code(tmp_path):
    _script(tmp_path, "import sys; sys.exit(4)\n")
    result = execute(
        Runnable(name="job", run_as="python", target="job.py",
                 working_directory=str(tmp_path), timeout=60, console=True)
    )
    assert result.returncode == 4
    assert not result.ok


def test_console_flag_round_trips_from_config():
    runnable = Runnable.from_entry(
        {"name": "bot", "run_as": "python", "target": "scrape.py", "console": True}
    )
    assert runnable.console is True
    assert Runnable.from_entry({"name": "x"}).console is False
