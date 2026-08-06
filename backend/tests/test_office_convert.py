"""Offline tests for the office-convert path (no soffice/pptx needed).

The parse_pptx + live LibreOffice conversion are verified in-container (they need
python-pptx / soffice). Here we test the deterministic, dependency-free pieces:
the no-op-when-absent behaviour and the convert-extension set.
"""
from __future__ import annotations

import shutil

from app import ingestion


def test_office_convert_ext_set():
    for e in (".doc", ".ppt", ".xls", ".odt", ".odp", ".ods", ".rtf"):
        assert e in ingestion._OFFICE_CONVERT_EXTS
    assert ".pptx" not in ingestion._OFFICE_CONVERT_EXTS  # pptx is native, not converted
    assert ".pdf" not in ingestion._OFFICE_CONVERT_EXTS


def test_libreoffice_noop_when_absent(monkeypatch):
    # Simulate soffice not installed → returns None (no crash), the slim-image path.
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert ingestion.libreoffice_to_pdf(b"anything", ".doc") is None


def _run_all() -> int:
    # tiny shim so this runs without pytest too
    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)

        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)

    test_office_convert_ext_set()
    print("  PASS test_office_convert_ext_set")
    mp = _MP()
    try:
        test_libreoffice_noop_when_absent(mp)
        print("  PASS test_libreoffice_noop_when_absent")
    finally:
        mp.undo()
    print("\n2/2 office-convert tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
