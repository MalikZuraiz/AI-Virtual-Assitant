"""Cover for the merge, including the streaming fast path.

The fast path skips CSV parsing entirely, so these tests care most about the
ways a byte-copy can silently corrupt data: a missing trailing newline gluing
two rows together, quoted fields containing commas or newlines, and headers
that look different but describe the same columns.
"""
import csv

import pytest

from assistant.automation.spreadsheets import (
    SpreadsheetError,
    _can_stream,
    merge_files,
    to_csv,
)


def _write(path, text: str) -> None:
    # newline="" so Windows does not rewrite \n as \r\n behind our back - the
    # point of these tests is what the merge does to the bytes, not what the
    # fixture does.
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


# -- the fast path -----------------------------------------------------------


def test_identical_csvs_take_the_streaming_path(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id,name\n1,alpha\n2,beta\n")
    _write(b, "id,name\n3,gamma\n")

    result = merge_files([a, b], tmp_path / "out.csv")

    assert result.fast_path is True
    assert result.rows == 3
    assert _rows(result.output) == [
        ["id", "name"], ["1", "alpha"], ["2", "beta"], ["3", "gamma"]
    ]


def test_different_quoting_still_streams(tmp_path):
    """Two exports of the same dashboard often quote differently."""
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, 'Penalty ID,District\n1,Lahore\n')
    _write(b, '"Penalty ID",District\n2,Khushab\n')

    streamable, columns = _can_stream([a, b])
    assert streamable is True
    assert columns == ["Penalty ID", "District"]

    result = merge_files([a, b], tmp_path / "out.csv")
    assert result.fast_path is True
    assert _rows(result.output)[1:] == [["1", "Lahore"], ["2", "Khushab"]]


def test_missing_trailing_newline_does_not_glue_rows(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id,name\n1,alpha")           # no trailing newline
    _write(b, "id,name\n2,beta\n")

    result = merge_files([a, b], tmp_path / "out.csv")

    assert result.fast_path is True
    assert _rows(result.output) == [["id", "name"], ["1", "alpha"], ["2", "beta"]]


def test_quoted_commas_and_newlines_survive_the_byte_copy(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, 'id,note\n1,"Lahore, Punjab"\n')
    _write(b, 'id,note\n2,"line one\nline two"\n')

    result = merge_files([a, b], tmp_path / "out.csv")

    assert result.fast_path is True
    rows = _rows(result.output)
    assert rows[1] == ["1", "Lahore, Punjab"]
    assert rows[2] == ["2", "line one\nline two"]


def test_column_order_difference_blocks_streaming(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id,name\n1,alpha\n")
    _write(b, "name,id\nbeta,2\n")

    assert _can_stream([a, b])[0] is False


# -- the pandas path ---------------------------------------------------------


def test_mismatched_columns_align_by_name(tmp_path):
    pytest.importorskip("pandas")
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id,name\n1,alpha\n")
    _write(b, "name,id\nbeta,2\n")

    result = merge_files([a, b], tmp_path / "out.csv")

    assert result.fast_path is False
    rows = _rows(result.output)
    assert rows[0] == ["id", "name"]
    # Reordered columns must not shift values sideways.
    assert rows[1] == ["1", "alpha"]
    assert rows[2] == ["2", "beta"]


def test_extra_column_is_kept_and_left_blank(tmp_path):
    pytest.importorskip("pandas")
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id,name\n1,alpha\n")
    _write(b, "id,name,extra\n2,beta,x\n")

    result = merge_files([a, b], tmp_path / "out.csv")

    assert result.fast_path is False
    assert "extra" in _rows(result.output)[0]
    assert any("column" in w for w in result.warnings)


def test_source_column_forces_the_parsed_path(tmp_path):
    pytest.importorskip("pandas")
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id\n1\n")
    _write(b, "id\n2\n")

    result = merge_files([a, b], tmp_path / "out.csv", add_source_column=True)

    assert result.fast_path is False
    assert "Source File" in _rows(result.output)[0]


# -- output format -----------------------------------------------------------


def test_default_output_is_csv_not_xlsx(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id\n1\n")
    _write(b, "id\n2\n")

    result = merge_files([a, b])

    assert result.output.suffix == ".csv"
    assert result.output.parent == tmp_path
    assert result.output.name.startswith("Merged ")


def test_one_file_is_refused(tmp_path):
    a = tmp_path / "a.csv"
    _write(a, "id\n1\n")
    with pytest.raises(SpreadsheetError, match="at least two"):
        merge_files([a])


def test_missing_file_is_named_in_the_error(tmp_path):
    a = tmp_path / "a.csv"
    _write(a, "id\n1\n")
    with pytest.raises(SpreadsheetError, match="ghost.csv"):
        merge_files([a, tmp_path / "ghost.csv"])


def test_progress_is_reported(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(a, "id\n1\n")
    _write(b, "id\n2\n")
    seen = []
    merge_files([a, b], tmp_path / "out.csv", on_progress=seen.append)
    assert seen and any("a.csv" in line for line in seen)


def test_to_csv_conversion(tmp_path):
    pytest.importorskip("pandas")
    source = tmp_path / "in.csv"
    _write(source, "id,name\n1,alpha\n")
    result = to_csv(source, tmp_path / "out.csv")
    assert result.rows == 1
    assert _rows(result.output)[0] == ["id", "name"]
