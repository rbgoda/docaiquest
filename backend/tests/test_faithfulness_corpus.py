"""Chat-faithfulness corpus — capture gating + Ragas record shaping (no DB).

Privacy-critical property: `capture_case` is a no-op unless the chat is from a
consented free user. The full capture→label→export→coverage path runs at deploy on
throwaway pg.
"""
from __future__ import annotations

import types

from app.services import faithfulness_corpus as fc


class _Ctx:
    def __init__(self, db):
        self.db = db
        self.text = "when is it due?"
        self.doc_id_external = "doc-1"


def test_capture_noop_when_ineligible(monkeypatch):
    # Not a consented free user → dropped before any DB write.
    import app.documents_scope as _ds
    monkeypatch.setattr(_ds, "get_current_owner_user_pk", lambda: 5)
    from app.services import eval_corpus
    monkeypatch.setattr(eval_corpus, "is_training_eligible", lambda db, o: False)
    msg = types.SimpleNamespace(pk=1, text="Due 12 Jul.", meta="rag_retrieval", confidence=0.9, citations=[])
    assert fc.capture_case(_Ctx(None), msg) is False   # never touches the DB


def test_to_record_ragas_shape():
    row = types.SimpleNamespace(
        question="when due?", answer="12 Jul.",
        citations=[{"quote": "Due date: 12 Jul 2026", "page": 1}, {"nope": 1}],
        suggestion="12 July 2026", abstained=False, meta="rag_retrieval · ok",
        label="down", category="incomplete", rating=3, verified=True, doc_id_external="doc-1")
    r = fc._to_record(row)
    assert r["question"] == "when due?" and r["answer"] == "12 Jul."
    assert r["contexts"] == ["Due date: 12 Jul 2026"]     # only dict quotes kept
    assert r["groundTruth"] == "12 July 2026"             # 👎 suggestion = ground truth
    assert r["label"] == "down" and r["verified"] is True


def test_to_record_abstained_no_ground_truth():
    row = types.SimpleNamespace(
        question="q", answer="INSUFFICIENT_EVIDENCE", citations=[], suggestion=None,
        abstained=True, meta="rag_retrieval · insufficient_evidence", label=None,
        category=None, rating=None, verified=False, doc_id_external=None)
    r = fc._to_record(row)
    assert r["abstained"] is True and r["contexts"] == [] and r["groundTruth"] is None
