"""Offline tests for R3 per-sentence citation attribution + drop-invalid."""
from __future__ import annotations

from app import sentence_citations as sc


def test_split_sentences():
    s = sc.split_sentences("The total is 12420. It is due Nov 1.\n- Bullet line here")
    assert s == ["The total is 12420.", "It is due Nov 1.", "Bullet line here"]
    assert sc.split_sentences("") == []


PASSAGES = [
    {"chunkPk": 2, "docId": "inv", "text": "Invoice EA07 total due 12420 USD payable November"},
    {"chunkPk": 9, "docId": "pol", "text": "Insurance policy coverage one million liability"},
]


def test_supported_sentence_attributes_to_right_passage():
    attrs = sc.attribute(["The total due is 12420 USD."], PASSAGES, min_support=0.5)
    a = attrs[0]
    assert a["supported"] is True
    assert a["source"]["chunkPk"] == 2  # the invoice passage, not the policy


def test_unsupported_sentence_is_dropped_from_citations():
    attrs = sc.attribute(
        ["The total due is 12420 USD.", "The CEO lives on Mars in a golden palace."],
        PASSAGES, min_support=0.5)
    cites = sc.citations_from_attributions(attrs)
    # only the grounded sentence yields a citation (drop-invalid)
    assert len(cites) == 1
    assert cites[0]["chunkPk"] == 2
    assert cites[0]["sentence"].startswith("The total due")
    assert "text" not in cites[0]            # bulky field stripped
    assert sc.unsupported_count(attrs) == 1


def test_strict_mode_supported_answer():
    attrs = sc.attribute(
        ["Total due is 12420 USD.", "Unrelated fabricated claim about nothing."],
        PASSAGES, min_support=0.5)
    kept = sc.supported_answer(attrs)
    assert "12420" in kept and "fabricated" not in kept


def test_no_passages_grounds_nothing():
    attrs = sc.attribute(["Anything at all here."], [], min_support=0.5)
    assert attrs[0]["supported"] is False
    assert sc.citations_from_attributions(attrs) == []


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} sentence-citation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
