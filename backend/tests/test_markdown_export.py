"""Offline tests for the deterministic whole-document Markdown renderer."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.markdown_export import render_markdown


@dataclass
class _Chunk:
    text: str
    page: int | None = None
    kind: str = "text"


def test_title_pages_and_paragraphs():
    md = render_markdown("Deed of Conveyance", [
        _Chunk("First paragraph on page one.", page=1),
        _Chunk("Second block, same page.", page=1),
        _Chunk("Now on page two.", page=2),
    ])
    assert md.startswith("# Deed of Conveyance")
    assert "## Page 1" in md and "## Page 2" in md
    assert "First paragraph on page one." in md and "Now on page two." in md
    # one page heading per page, not per chunk
    assert md.count("## Page 1") == 1


def test_blank_chunks_skipped_and_no_page_heading_when_missing():
    md = render_markdown("Sheet", [_Chunk("   ", page=None), _Chunk("Only content.", page=None)])
    assert "Only content." in md and "## Page" not in md


def test_empty_document_returns_empty():
    assert render_markdown("Doc", []) == ""
    assert render_markdown("Doc", [_Chunk("", page=1)]) == ""
