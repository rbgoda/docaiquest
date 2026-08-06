"""QA for text/eml parsers + chunk-cap logging (input-pipeline robustness fixes).

Pure (no DB): parse_text, parse_eml, _html_to_text, chunk_pages are import-time safe.
Covers the fixes:
  · parse_text now decodes UTF-16 (Windows "Unicode" .txt) instead of garbling it.
  · parse_eml/_html_to_text drop <script>/<style> and decode HTML entities.
  · chunk_pages no longer silently caps a big single "page" at 50 chunks.
"""
from app.ingestion import _html_to_text, chunk_pages, parse_eml, parse_text


def test_parse_text_utf16le_decodes_clean():
    raw = "Total: €1,234.56 — café façade\nLine two".encode("utf-16-le")
    pages = parse_text(raw)
    assert len(pages) == 1
    txt = pages[0][1]
    assert "1,234.56" in txt and "café" in txt and "façade" in txt
    assert "\x00" not in txt              # no interleaved NULs
    assert "ÿþ" not in txt and "﻿" not in txt  # no stray BOM


def test_parse_text_utf8_still_works():
    pages = parse_text("plain ascii and é".encode("utf-8"))
    assert pages[0][1] == "plain ascii and é"


def test_html_to_text_drops_script_style_and_decodes_entities():
    html = ("<html><head><style>.x{color:red}</style></head>"
            "<body><script>alert(1)</script>"
            "<p>Hello&nbsp;World &amp; welcome &#39;home&#39;</p></body></html>")
    out = _html_to_text(html)
    assert "color:red" not in out and "alert(1)" not in out   # script/style gone
    assert "Hello World & welcome 'home'" in out              # entities decoded
    assert "<" not in out and ">" not in out                  # tags stripped


def test_parse_eml_html_only_email_is_clean():
    eml = (b"From: a@x.io\r\nTo: b@x.io\r\nSubject: Hi\r\n"
           b"Content-Type: text/html; charset=utf-8\r\n\r\n"
           b"<style>.p{font:1px}</style><p>Balance is &pound;500 &amp; rising</p>")
    pages = parse_eml(eml)
    txt = pages[0][1]
    assert "font:1px" not in txt
    assert "£500 & rising" in txt
    assert "Subject: Hi" in txt


def test_chunk_pages_big_single_page_not_capped_at_50():
    # ~120k chars as ONE page → old code capped at 50 chunks (~50k) and dropped the
    # rest. Now it should chunk the whole page (well past 50 chunks) with no loss of
    # the distinctive tail marker.
    body = ("paragraph number {} with some filler words here.\n\n".format)
    page_text = "".join(body(i) for i in range(1800)) + "\n\nUNIQUE_TAIL_MARKER_XYZ end."
    chunks = chunk_pages([(1, page_text)])
    assert len(chunks) > 50
    joined = " ".join(c.text for c in chunks)
    assert "UNIQUE_TAIL_MARKER_XYZ" in joined   # tail survived (was dropped before)
