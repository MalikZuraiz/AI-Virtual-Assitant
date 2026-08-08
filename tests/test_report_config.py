"""Every configured report must be runnable as written.

This guards the class of failure the user hit repeatedly: a report pointing
at a .py that has since been built, at an exe that does not exist, or at a
CLI shape the generator does not accept.
"""
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from assistant.core.runner import Runnable, build_command
from assistant.workspace.inputs import InputSpec, build_args

CONFIG = Path(__file__).resolve().parents[1] / "config" / "reports.json"
REPORTS = json.loads(CONFIG.read_text(encoding="utf-8"))["reports"] if CONFIG.is_file() else []
IDS = [r["name"] for r in REPORTS]

pytestmark = pytest.mark.skipif(not REPORTS, reason="no reports configured on this machine")


@pytest.mark.parametrize("entry", REPORTS, ids=IDS)
def test_every_report_runs_as_an_exe(entry):
    """The bot aside, everything has a build now - use it."""
    assert entry["run_as"] == "exe", f"{entry['name']} still runs as {entry['run_as']}"
    assert entry["target"].startswith("dist/")


@pytest.mark.parametrize("entry", REPORTS, ids=IDS)
def test_the_target_exists(entry):
    target = Path(entry["working_directory"]) / entry["target"]
    if not Path(entry["working_directory"]).is_dir():
        pytest.skip("project folder not present on this machine")
    assert target.is_file(), f"{entry['name']}: no exe at {target}"


@pytest.mark.parametrize("entry", REPORTS, ids=IDS)
def test_a_full_command_can_be_built(entry):
    """Catches a bad run_as/target long before a run fails in front of you."""
    if not (Path(entry["working_directory"]) / entry["target"]).is_file():
        pytest.skip("project folder not present on this machine")
    spec = InputSpec.from_entry(entry)
    fake = Path(entry["working_directory"]) / entry["target"]  # any real path
    args = build_args([fake] * spec.count, spec,
                      date_range=(date(2026, 8, 1), date(2026, 8, 7)))
    runnable = Runnable.from_entry(entry)
    runnable.args = [*runnable.args, *args]
    command, _shell = build_command(runnable)
    printable = subprocess.list2cmdline(command)
    assert entry["target"].split("/")[-1] in printable
    for _ in range(spec.count):
        assert str(fake) in printable


@pytest.mark.parametrize("entry", REPORTS, ids=IDS)
def test_input_count_matches_what_the_generator_takes(entry):
    spec = InputSpec.from_entry(entry)
    assert spec.count >= 1
    if spec.mode == "flag":
        assert spec.flag, f"{entry['name']}: flag mode with no flag set"


def test_the_ped_bot_is_not_registered_as_a_report():
    """It is an interactive scraper - it opens in VS Code, it is never run."""
    assert not any("bot" in r["name"].lower() for r in REPORTS)


def test_only_period_sensitive_reports_ask_for_dates():
    asking = [r["name"] for r in REPORTS if InputSpec.from_entry(r).ask_dates]
    assert asking == ["tmo scoring report"]
