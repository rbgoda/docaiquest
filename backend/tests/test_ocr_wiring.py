"""Offline tests for G3 wiring — summarize_pages aggregate + dashboard alert rule.

Pure-stdlib: imports only `app.ocr_quality` and `app.intelligence.alerts`
(no fitz/DB/LLM). `python -m pytest` OR `python backend/tests/test_ocr_wiring.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

from app import ocr_quality as oq
from app.intelligence import alerts

CLEAN = (
    "This Certificate of Insurance confirms general liability coverage of one "
    "million dollars, effective 1 January 2026 through 31 December 2026."
)
GARBAGE = "@#$%^&*()_+{}|<>?~`@#$%^&*()_+{}|<>?~`@#$%^&*()_+{}|<>?~`"


def test_summarize_only_scores_ocr_pages():
    pages = [(1, CLEAN), (2, GARBAGE), (3, CLEAN)]
    # Only page 2 went through OCR.
    s = oq.summarize_pages(pages, {2})
    assert s["pagesScored"] == 1
    assert s["lowConfidencePages"] == 1
    assert s["flagged"] is True
    assert s["pages"][0]["page"] == 2


def test_summarize_clean_not_flagged():
    pages = [(1, CLEAN), (2, CLEAN)]
    s = oq.summarize_pages(pages, {1, 2})
    assert s["pagesScored"] == 2
    assert s["lowConfidencePages"] == 0
    assert s["flagged"] is False


def test_summarize_none_when_no_ocr_pages():
    assert oq.summarize_pages([(1, CLEAN)], set()) is None
    assert oq.summarize_pages([], {1}) is None


def _doc(**kw):
    base = dict(id_external="doc-1", name="scan.pdf", ingestion_status="ready",
               extracted_fields=None, doc_type="invoice", doc_type_confidence=0.99,
               ocr_quality=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_alert_fires_on_low_ocr_confidence():
    doc = _doc(ocr_quality={"flagged": True, "lowConfidencePages": 2})
    out = alerts.alerts_for_document(doc)
    ocr_alerts = [a for a in out if a["type"] == "low_ocr_confidence"]
    assert len(ocr_alerts) == 1
    assert ocr_alerts[0]["severity"] == "review"
    assert "2" in ocr_alerts[0]["detail"]


def test_no_alert_when_ocr_clean_or_absent():
    assert not [a for a in alerts.alerts_for_document(_doc(ocr_quality=None))
                if a["type"] == "low_ocr_confidence"]
    assert not [a for a in alerts.alerts_for_document(
        _doc(ocr_quality={"flagged": False, "lowConfidencePages": 0}))
        if a["type"] == "low_ocr_confidence"]


def test_failed_doc_short_circuits_before_ocr_alert():
    # A failed ingest returns only the ingestion_failed alert.
    doc = _doc(ingestion_status="failed", ocr_quality={"flagged": True, "lowConfidencePages": 1})
    types = {a["type"] for a in alerts.alerts_for_document(doc)}
    assert types == {"ingestion_failed"}


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} ocr-wiring tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
