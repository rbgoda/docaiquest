"""Phase 3 · DOCX/XLSX/PPTX native structure → Document Model IR."""
from io import BytesIO

import pytest

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")

from app import ingestion  # noqa: E402
from app.document_model import BlockKind  # noqa: E402


def _content_words(pages):
    out = []
    for _, t in pages:
        for w in t.split():
            s = w.strip("#|*->:")
            if s:
                out.append(s)
    return sorted(out)


def _make_docx():
    d = docx.Document()
    d.add_heading("Report Title", level=1)
    d.add_paragraph("First body paragraph with words.")
    d.add_heading("Section A", level=2)
    tbl = d.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = "Name"
    tbl.rows[0].cells[1].text = "Value"
    tbl.rows[1].cells[0].text = "Alpha"
    tbl.rows[1].cells[1].text = "42"
    b = BytesIO()
    d.save(b)
    return b.getvalue()


def test_docx_ir_structure_and_no_text_loss(monkeypatch):
    raw = _make_docx()
    monkeypatch.setattr(ingestion.get_settings(), "doc_model", False, raising=False)
    flat = ingestion.parse_docx(raw)
    monkeypatch.setattr(ingestion.get_settings(), "doc_model", True, raising=False)
    struct = ingestion.parse_docx(raw)
    assert _content_words(flat) == _content_words(struct)   # no content lost

    doc = ingestion._docx_to_document(docx.Document(BytesIO(raw)), [])
    kinds = {b.kind for b in doc.blocks}
    assert BlockKind.HEADING in kinds and BlockKind.TABLE in kinds
    table = next(b for b in doc.blocks if b.kind == BlockKind.TABLE)
    assert table.rows[0] == ["Name", "Value"]


def test_xlsx_ir_one_page_per_sheet(monkeypatch):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Date", "Amount"])
    ws.append(["2026-01-01", 100])
    wb.create_sheet("Notes").append(["memo", "hello"])
    b = BytesIO()
    wb.save(b)
    raw = b.getvalue()

    monkeypatch.setattr(ingestion.get_settings(), "doc_model", False, raising=False)
    flat = ingestion.parse_xlsx(raw)
    monkeypatch.setattr(ingestion.get_settings(), "doc_model", True, raising=False)
    struct = ingestion.parse_xlsx(raw)
    assert [p for p, _ in struct] == [1, 2]
    assert _content_words(flat) == _content_words(struct)
    assert "| Date | Amount |" in struct[0][1]
    assert "Sheet: Data" in struct[0][1]


def test_flag_off_unchanged(monkeypatch):
    raw = _make_docx()
    monkeypatch.setattr(ingestion.get_settings(), "doc_model", False, raising=False)
    a = ingestion.parse_docx(raw)
    b = ingestion.parse_docx(raw)
    assert a == b
