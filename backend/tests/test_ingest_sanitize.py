"""Extracted-text sanitization — strips NUL/control bytes that would crash the
Postgres chunk INSERT (regression: a Malayalam PDF whose PyMuPDF text carried
NUL (0x00) bytes failed ingestion with psycopg.DataError)."""
from __future__ import annotations

from app.ingestion import _sanitize_text


def test_strips_nul():
    assert _sanitize_text("a\x00b") == "ab"
    assert "\x00" not in _sanitize_text("ഇ\x00വോയ്സ് 4250")


def test_strips_other_c0_controls():
    assert _sanitize_text("x\x01y\x08z\x0bw\x0cv\x1fu") == "xyzwvu"


def test_keeps_legitimate_whitespace():
    assert _sanitize_text("line1\nline2\tcol\r\n") == "line1\nline2\tcol\r\n"


def test_keeps_unicode_and_normal_text():
    s = "Invoice total: 4,250 — ACME Corp · ഇൻവോയ്സ് 請求書"
    assert _sanitize_text(s) == s


def test_empty_and_none():
    assert _sanitize_text("") == ""
    assert _sanitize_text(None) == ""
