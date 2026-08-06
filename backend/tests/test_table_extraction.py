"""G8 · table-extraction helpers (borderless + multi-page).

Covers the pure logic added for G8: header detection (for multi-page header
carry-over), table normalization, and the markdown renderer's filtering +
empty-column pruning (which cleans the text-strategy's phantom columns)."""
from __future__ import annotations

from app.ingestion import _looks_like_header, _normalize_table, _table_to_markdown


# ── header detection (drives multi-page carry-over) ──────────────────────────

def test_header_row_detected():
    assert _looks_like_header(["Date", "Description", "Amount"]) is True
    assert _looks_like_header(["Card Type", "Account Number", "Balance $"]) is True


def test_data_row_not_header():
    # a continuation page's first DATA row must NOT read as a header
    assert _looks_like_header(["2026-02-15", "PARKING.SG", "0.48"]) is False
    assert _looks_like_header(["$4,250.00", "12/03/2026", ""]) is False


def test_degenerate_not_header():
    assert _looks_like_header(["", ""]) is False
    assert _looks_like_header(["Total"]) is False
    assert _looks_like_header([]) is False


# ── normalization ────────────────────────────────────────────────────────────

def test_normalize_collapses_ws_and_drops_empty_rows():
    out = _normalize_table([["  a  b ", None], ["", ""], ["c", "d"]])
    assert out == [["a b", ""], ["c", "d"]]


# ── markdown render: filtering + empty-column pruning ─────────────────────────

def test_empty_columns_pruned():
    md = _table_to_markdown([
        ["", "Date", "", "Amount"],
        ["", "2026-01-01", "", "5.00"],
        ["", "2026-01-02", "", "9.00"],
    ])
    assert md is not None
    assert md.splitlines()[0] == "| Date | Amount |"   # the 2 empty cols dropped


def test_sparse_grid_rejected():
    # mostly-empty grid = layout noise, not a table
    assert _table_to_markdown([["a", "", "", ""], ["", "", "", ""]]) is None


def test_single_column_rejected():
    assert _table_to_markdown([["only"], ["one"], ["col"]]) is None


def test_real_table_renders():
    md = _table_to_markdown([
        ["Date", "Description", "Amount"],
        ["2026-02-15", "PARKING.SG", "0.48"],
        ["2026-02-16", "ACRA", "5.50"],
    ])
    assert md is not None
    lines = md.splitlines()
    assert lines[0] == "| Date | Description | Amount |"
    assert lines[1] == "| --- | --- | --- |"
    assert "PARKING.SG" in md and "ACRA" in md


def test_pipe_escaped_in_cells():
    md = _table_to_markdown([["a", "b"], ["x|y", "z"]])
    assert "x\\|y" in md
