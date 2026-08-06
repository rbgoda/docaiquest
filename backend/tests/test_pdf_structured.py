"""Phase 1 · structured PDF parse (PyMuPDF get_text('dict') → Document Model)."""
import pytest

fitz = pytest.importorskip("fitz")

from app import ingestion  # noqa: E402
from app.document_model import BlockKind  # noqa: E402


def _make_pdf() -> bytes:
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "ANNUAL REPORT", fontsize=24)                 # heading
    p1.insert_text((72, 120), "First paragraph of the body text with several words.", fontsize=11)
    p1.insert_text((72, 160), "Second paragraph, also body text for testing purposes.", fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Section Two", fontsize=16)                    # heading (L2)
    p2.insert_text((72, 110), "More body content on the second page here.", fontsize=11)
    doc.new_page()                                                          # empty page 3
    return doc.tobytes()


def _words(pages):
    return sorted(w for _, t in pages for w in t.split())


def test_structured_preserves_pages_and_words():
    pdf = _make_pdf()
    doc = ingestion.parse_pdf_structured(pdf)
    pages = doc.to_pages()
    assert [p for p, _ in pages] == [1, 2, 3]
    assert pages[2] == (3, "")                      # empty page preserved + aligned
    # No text lost vs the flat parser — identical word multiset.
    assert _words(pages) == _words(ingestion.parse_pdf(pdf))


def test_structured_detects_headings():
    doc = ingestion.parse_pdf_structured(_make_pdf())
    headings = {b.text for b in doc.blocks if b.kind == BlockKind.HEADING}
    assert any("ANNUAL REPORT" in t for t in headings)
    assert any("Section Two" in t for t in headings)
    paras = {b.text for b in doc.blocks if b.kind == BlockKind.PARAGRAPH}
    assert any("First paragraph" in t for t in paras)


def test_structured_blocks_are_blank_line_separated():
    pages = ingestion.parse_pdf_structured(_make_pdf()).to_pages()
    # heading + 2 paragraphs → >=2 blank-line boundaries the chunker splits on.
    assert pages[0][1].count("\n\n") >= 2
    assert "ANNUAL REPORT\n\n" in pages[0][1]


def test_blocks_carry_bbox_provenance():
    doc = ingestion.parse_pdf_structured(_make_pdf())
    non_empty = [b for b in doc.blocks if b.text]
    assert non_empty and all(b.bbox is not None for b in non_empty)
    fb = non_empty[0].bbox.to_field_bbox()
    assert set(fb) == {"x0", "y0", "x1", "y1", "page_w", "page_h", "page"}


def test_flag_off_matches_flat(monkeypatch):
    pdf = _make_pdf()
    monkeypatch.setattr(ingestion.get_settings(), "doc_model", False, raising=False)
    assert ingestion._parse_pdf_pages(pdf) == ingestion.parse_pdf(pdf)


def test_flag_on_preserves_words(monkeypatch):
    pdf = _make_pdf()
    settings = ingestion.get_settings()
    monkeypatch.setattr(settings, "doc_model", True, raising=False)
    on = ingestion._parse_pdf_pages(pdf)
    monkeypatch.setattr(settings, "doc_model", False, raising=False)
    off = ingestion._parse_pdf_pages(pdf)
    assert _words(on) == _words(off)
    assert on[0][1].count("\n\n") >= 2      # structured gives block boundaries
