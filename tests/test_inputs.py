"""The raw-data pipeline: find the download, stage it, build the arguments."""
from datetime import date
from pathlib import Path

import pytest

from assistant.workspace.inputs import InputSpec, build_args, resolve_input, stage


@pytest.fixture()
def project(tmp_path):
    folder = tmp_path / "Pending Penalties report"
    (folder / "files").mkdir(parents=True)
    (folder / "dist").mkdir()
    return folder


@pytest.fixture()
def downloads(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


def _entry(project: Path, **input_spec) -> dict:
    return {
        "name": "pending penalties report",
        "working_directory": str(project),
        "run_as": "exe",
        "target": "dist/report.exe",
        "input": {"mode": "arg", "extensions": [".csv"], "stage_dir": "files", **input_spec},
    }


# -- finding the input -------------------------------------------------------


def test_a_file_named_data_in_downloads_wins(project, downloads):
    (downloads / "data.csv").write_text("a,b", encoding="utf-8")
    (downloads / "export-penalties-01-08.csv").write_text("a,b", encoding="utf-8")

    resolution = resolve_input(_entry(project), downloads_dir=downloads)

    assert resolution.ready
    assert resolution.chosen[0].name == "data.csv"
    assert "data.csv" in resolution.message


def test_raw_data_naming_variants_are_recognised(project, downloads):
    (downloads / "raw data.csv").write_text("a", encoding="utf-8")
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert resolution.chosen[0].name == "raw data.csv"


def test_a_numbered_duplicate_still_counts_as_data(project, downloads):
    (downloads / "data (2).csv").write_text("a", encoding="utf-8")
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert resolution.chosen[0].name == "data (2).csv"


def test_an_explicit_path_beats_everything(project, downloads, tmp_path):
    (downloads / "data.csv").write_text("a", encoding="utf-8")
    chosen = tmp_path / "somewhere else.csv"
    chosen.write_text("b", encoding="utf-8")

    resolution = resolve_input(_entry(project), downloads_dir=downloads, explicit=[chosen])

    assert resolution.chosen == [chosen]


def test_an_explicit_path_that_is_missing_says_so(project, downloads):
    resolution = resolve_input(_entry(project), downloads_dir=downloads, explicit=["Z:/nope.csv"])
    assert not resolution.ready
    assert "can't find" in resolution.message


def test_several_candidates_are_offered_rather_than_guessed(project, downloads):
    """Picking the wrong export silently produces a wrong report."""
    for name in ("export-a.csv", "export-b.csv", "export-c.csv"):
        (downloads / name).write_text("a", encoding="utf-8")

    resolution = resolve_input(_entry(project), downloads_dir=downloads)

    assert resolution.ambiguous
    assert not resolution.chosen
    assert len(resolution.candidates) == 3


def test_a_single_candidate_is_used_without_asking(project, downloads):
    (downloads / "export-only.csv").write_text("a", encoding="utf-8")
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert resolution.ready
    assert resolution.chosen[0].name == "export-only.csv"


def test_the_staged_folder_is_the_fallback(project, downloads):
    (project / "files" / "data.csv").write_text("a", encoding="utf-8")
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert resolution.chosen[0].name == "data.csv"
    assert "files/" in resolution.message


def test_nothing_found_explains_the_convention(project, downloads):
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert not resolution.ready
    assert "rename it to 'data.csv'" in resolution.message


def test_extensions_are_respected(project, downloads):
    (downloads / "data.pdf").write_text("a", encoding="utf-8")
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert not resolution.ready


def test_a_generator_that_needs_no_input_is_ready_immediately(project, downloads):
    entry = _entry(project)
    entry["input"] = {"mode": "none"}
    resolution = resolve_input(entry, downloads_dir=downloads)
    assert resolution.ready
    assert resolution.chosen == []


def test_lock_files_are_never_picked_up(project, downloads):
    (downloads / "~$data.csv").write_text("lock", encoding="utf-8")
    resolution = resolve_input(_entry(project), downloads_dir=downloads)
    assert not resolution.ready


# -- staging -----------------------------------------------------------------


def test_staging_copies_rather_than_moves(project, downloads):
    source = downloads / "data.csv"
    source.write_text("rows", encoding="utf-8")

    staged = stage([source], _entry(project), InputSpec.from_entry(_entry(project)))

    assert staged[0] == project / "files" / "data.csv"
    assert staged[0].read_text() == "rows"
    # A failed run must never cost you the download.
    assert source.is_file()


def test_staging_a_file_already_in_place_is_a_no_op(project):
    target = project / "files" / "data.csv"
    target.write_text("rows", encoding="utf-8")
    staged = stage([target], _entry(project), InputSpec.from_entry(_entry(project)))
    assert staged == [target]


# -- argument building -------------------------------------------------------


def test_positional_mode_passes_the_path():
    spec = InputSpec(mode="arg")
    assert build_args([Path("D:/x/data.csv")], spec) == ["D:\\x\\data.csv"]


def test_flag_mode_prefixes_the_option():
    spec = InputSpec(mode="flag", flag="--csv")
    assert build_args([Path("data.csv")], spec) == ["--csv", "data.csv"]


def test_files_dir_mode_passes_nothing():
    """The script reads its own folder; an argument would be wrong."""
    assert build_args([Path("data.csv")], InputSpec(mode="files_dir")) == []


def test_date_flags_default_to_this_month_to_date():
    spec = InputSpec(mode="flag", flag="--csv", date_flags={"from": "--from-date", "to": "--to-date"})
    args = build_args([Path("d.csv")], spec, when=date(2026, 8, 7))
    assert args == ["--csv", "d.csv", "--from-date", "2026-08-01", "--to-date", "2026-08-07"]


def test_multiple_files_are_all_passed_positionally():
    spec = InputSpec(mode="arg", count=4)
    args = build_args([Path("a.csv"), Path("b.csv")], spec)
    assert args == ["a.csv", "b.csv"]


def test_spec_defaults_survive_a_missing_input_block():
    spec = InputSpec.from_entry({"name": "x"})
    assert spec.mode == "arg"
    assert spec.needs_input is True


def test_an_unknown_mode_falls_back_to_positional():
    spec = InputSpec.from_entry({"input": {"mode": "nonsense"}})
    assert spec.mode == "arg"
