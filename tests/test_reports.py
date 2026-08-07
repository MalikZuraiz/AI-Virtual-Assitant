"""End-to-end cover for the feature this whole project hangs on:

run a generator, and whatever it produced ends up in today's folder.

Uses a throwaway generator script rather than the real ones - the point is
the plumbing (discover output, create the day folder, move the files), and a
test that needs a 90-second pandas run and a live data export is a test
nobody will run.
"""
from datetime import date
from pathlib import Path

import pytest

from assistant.workspace.reports import file_outputs, run_report

GENERATOR = """
import sys, pathlib
out = pathlib.Path(__file__).parent / "dist"
out.mkdir(exist_ok=True)
(out / "Agency Wise Penalties Jul-26.xlsx").write_text("report body")
(out / "notes.txt").write_text("not a report format")
print("generated 1 report")
"""


@pytest.fixture()
def project(tmp_path):
    folder = tmp_path / "generator"
    folder.mkdir()
    (folder / "build_report.py").write_text(GENERATOR, encoding="utf-8")
    return folder


def _entry(project: Path) -> dict:
    return {
        "name": "test report",
        "working_directory": str(project),
        "run_as": "python",
        "target": "build_report.py",
        "collect_from": ["dist", "."],
        "route_output": "day_folder",
    }


def test_output_is_filed_into_todays_folder(project, tmp_path):
    archive = tmp_path / "Reports"
    result = run_report(
        _entry(project), output_root=archive, day_format="{d} {month}", when=date(2026, 8, 7)
    )

    assert result.ok, result.summary()
    assert result.day_folder == archive / "7 August"
    filed = {p.name for p in result.filed}
    assert "Agency Wise Penalties Jul-26.xlsx" in filed
    # .txt isn't a report format, so it stays where the generator put it.
    assert "notes.txt" not in filed
    assert (project / "dist" / "notes.txt").is_file()


def test_the_day_folder_is_created_when_it_does_not_exist(project, tmp_path):
    archive = tmp_path / "Reports"
    assert not archive.exists()
    run_report(_entry(project), output_root=archive, day_format="{d} {month}", when=date(2026, 8, 7))
    assert (archive / "7 August").is_dir()


def test_pre_existing_untouched_files_are_not_collected(project, tmp_path):
    stale = project / "dist"
    stale.mkdir()
    (stale / "Last Week.xlsx").write_text("old")

    result = run_report(
        _entry(project), output_root=tmp_path / "R", day_format="{d} {month}", when=date(2026, 8, 7)
    )

    assert "Last Week.xlsx" not in {p.name for p in result.filed}
    assert (stale / "Last Week.xlsx").is_file()


def test_excel_lock_files_are_never_collected(project, tmp_path):
    dist = project / "dist"
    dist.mkdir()
    (dist / "~$Open Workbook.xlsx").write_text("lock")

    result = run_report(
        _entry(project), output_root=tmp_path / "R", day_format="{d} {month}", when=date(2026, 8, 7)
    )

    assert not any(p.name.startswith("~$") for p in result.filed)


def test_a_failing_generator_reports_the_exit_code(tmp_path):
    project = tmp_path / "broken"
    project.mkdir()
    (project / "build_report.py").write_text("import sys; sys.exit(3)", encoding="utf-8")

    result = run_report(
        {
            "name": "broken report",
            "working_directory": str(project),
            "run_as": "python",
            "target": "build_report.py",
        },
        output_root=tmp_path / "R",
        day_format="{d} {month}",
    )

    assert not result.ok
    assert "exited with code 3" in result.summary()


def test_a_missing_script_is_a_clear_message_not_a_traceback(tmp_path):
    result = run_report(
        {
            "name": "ghost",
            "working_directory": str(tmp_path),
            "run_as": "python",
            "target": "nope.py",
        },
        output_root=tmp_path / "R",
        day_format="{d} {month}",
    )
    assert not result.ok
    assert "script not found" in result.summary()


def test_name_conflicts_are_versioned_not_overwritten(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    destination = tmp_path / "dest"
    destination.mkdir()
    (destination / "report.xlsx").write_text("yesterday's copy")

    produced = source_dir / "report.xlsx"
    produced.write_text("today's copy")
    filed, _skipped = file_outputs([produced], destination, mode="move")

    assert filed[0].name == "report (2).xlsx"
    assert (destination / "report.xlsx").read_text() == "yesterday's copy"


def test_copy_mode_leaves_the_original_in_place(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    produced = source_dir / "report.xlsx"
    produced.write_text("x")

    filed, _ = file_outputs([produced], tmp_path / "dest", mode="copy")

    assert filed[0].is_file()
    assert produced.is_file()
