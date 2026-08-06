"""CSV/spreadsheet formula-injection guard for exported tables.

Document names + extracted field values are user-controlled and were written verbatim
into CSV/xlsx cells; a value like =HYPERLINK(...) or =cmd|... executes when the file is
opened in Excel/Sheets. `_formula_safe` prefixes such cells with an apostrophe so they
render as text. Pure (no DB)."""
from app.agents.workspace_agent import _formula_safe, _rows_to_csv


def test_formula_prefixes_are_neutralized():
    for danger in ("=1+1", "+1", "-1", "@SUM(A1)", '=HYPERLINK("http://evil","x")',
                   "=cmd|'/c calc'!A1"):
        out = _formula_safe(danger)
        assert out.startswith("'"), f"{danger!r} not neutralized -> {out!r}"
        assert out[1:] == danger


def test_safe_values_unchanged():
    for ok in ("Invoice 2026", "1234.56", "Acme Corp", "", "a=b (not leading)"):
        assert _formula_safe(ok) == ok


def test_none_becomes_empty():
    assert _formula_safe(None) == ""


def test_rows_to_csv_neutralizes_cells_and_headers():
    csv = _rows_to_csv(["name", "amount"],
                       [{"name": "=HYPERLINK(1)", "amount": "100"},
                        {"name": "Acme", "amount": "-5"}])
    lines = csv.strip().splitlines()
    # the dangerous doc name + the -5 amount are apostrophe-guarded
    assert "'=HYPERLINK(1)" in lines[1]
    assert "'-5" in lines[2]
    assert "Acme" in lines[2]
