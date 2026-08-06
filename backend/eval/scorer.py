"""Pure-function scorers for the DocAIQ extraction / OCR / retrieval eval harness.

Stdlib only — no DB, no LLM, no third-party deps — so the harness runs in CI and
offline. Each function compares predicted vs expected and returns plain
dicts/floats. String comparison is lenient (see `normalize`) because extraction
and OCR legitimately vary in punctuation/whitespace/casing.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalize(s: Any) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Lenient equality: 'ACME, Inc.' == 'acme inc'. Note this also makes
    '12,420.00' -> '12 420 00' and '12420.00' -> '12420 00' (NOT equal) — i.e.
    digit grouping differences are caught, which is intentional for money.
    """
    if s is None:
        return ""
    s = _PUNCT.sub(" ", str(s).lower())
    return _WS.sub(" ", s).strip()


def field_prf(predicted: dict, expected: dict) -> dict:
    """Field-level precision / recall / F1.

    A field is correct when present in both with matching normalized values.
    Precision is over fields the model emitted (non-empty); recall is over
    fields the gold set expects. Spurious + missed lists aid error analysis.
    """
    pred = {k: v for k, v in (predicted or {}).items() if normalize(v)}
    gold = {k: v for k, v in (expected or {}).items() if normalize(v)}
    correct = sum(
        1 for k, v in gold.items() if k in pred and normalize(pred[k]) == normalize(v)
    )
    p = correct / len(pred) if pred else 0.0
    r = correct / len(gold) if gold else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {
        "precision": p,
        "recall": r,
        "f1": f1,
        "correct": correct,
        "n_pred": len(pred),
        "n_gold": len(gold),
        "missed": [k for k in gold if k not in pred or normalize(pred[k]) != normalize(gold[k])],
        "spurious": [k for k in pred if k not in gold],
    }


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insert/delete/substitute = 1). Two-row DP, O(len(a)*len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(hypothesis: str, reference: str) -> float:
    """Character Error Rate = edit_distance(hyp, ref) / len(ref). 0.0 is perfect."""
    ref = reference or ""
    if not ref:
        return 0.0 if not (hypothesis or "") else 1.0
    return levenshtein(hypothesis or "", ref) / len(ref)


def _flatten_cells(table: Iterable[Iterable[Any]]) -> list[str]:
    return [normalize(c) for row in (table or []) for c in row if normalize(c)]


def table_cell_f1(predicted: list, expected: list) -> dict:
    """Multiset cell-level F1 over flattened, normalized table cells.

    Order-insensitive: rewards getting the right set of cell values without
    penalising row/column reshuffles the model may introduce.
    """
    pred = Counter(_flatten_cells(predicted))
    gold = Counter(_flatten_cells(expected))
    inter = sum((pred & gold).values())
    p = inter / sum(pred.values()) if pred else 0.0
    r = inter / sum(gold.values()) if gold else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {
        "precision": p,
        "recall": r,
        "f1": f1,
        "matched": inter,
        "n_pred": sum(pred.values()),
        "n_gold": sum(gold.values()),
    }


def hit_at_k(ranked: list, relevant: Iterable, k: int = 5) -> float:
    """1.0 if any relevant id appears in the top-k of `ranked`, else 0.0."""
    rel = set(relevant or [])
    if not rel:
        return 0.0
    return 1.0 if any(r in rel for r in list(ranked or [])[:k]) else 0.0


def reciprocal_rank(ranked: list, relevant: Iterable) -> float:
    """1/rank of the first relevant id (MRR contribution); 0.0 if none found."""
    rel = set(relevant or [])
    for i, doc in enumerate(ranked or [], 1):
        if doc in rel:
            return 1.0 / i
    return 0.0


# ---- R4 · QA / faithfulness / abstention ----------------------------------
# The chat layer prefixes a safe refusal with this sentinel (app.abstention).
# Duplicated here as a literal so the eval harness stays dependency-free.
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


def is_abstention(answer: str | None) -> bool:
    """True if a chat answer is an INSUFFICIENT_EVIDENCE refusal."""
    return bool(answer) and answer.lstrip().startswith(INSUFFICIENT)


def answer_correctness(predicted: str, expected_keys) -> dict:
    """Lenient QA correctness: every expected key-fact must appear (normalized
    substring) in the answer. `expected_keys` = str or list[str]. Returns
    {matched, expected, ratio, correct}."""
    keys = [expected_keys] if isinstance(expected_keys, str) else list(expected_keys or [])
    keys = [k for k in keys if normalize(k)]
    npred = normalize(predicted)
    matched = sum(1 for k in keys if normalize(k) in npred)
    total = len(keys)
    return {"matched": matched, "expected": total,
            "ratio": (matched / total if total else 0.0),
            "correct": bool(total and matched == total)}


def citation_recall(predicted_ids: Iterable, required_ids: Iterable) -> dict:
    """Did the answer cite the spans it was supposed to? Recall over required ids."""
    req = {str(x) for x in (required_ids or [])}
    pred = {str(x) for x in (predicted_ids or [])}
    if not req:
        return {"recall": 1.0, "matched": 0, "required": 0}
    matched = len(req & pred)
    return {"recall": matched / len(req), "matched": matched, "required": len(req)}


def faithfulness_proxy(evidence: str, expected_facts) -> dict:
    """Offline faithfulness proxy: fraction of expected key-facts actually present
    in the cited EVIDENCE (if a fact isn't in the evidence, an answer asserting it
    is unsupported). True faithfulness needs an LLM judge (live mode); this is the
    deterministic, CI-safe lower bound. Returns {supported, facts} (supported=None
    when no facts to check)."""
    facts = [f for f in (expected_facts or []) if normalize(f)]
    if not facts:
        return {"supported": None, "facts": 0}
    ev = normalize(evidence)
    sup = sum(1 for f in facts if normalize(f) in ev)
    return {"supported": sup / len(facts), "facts": len(facts)}


def abstention_outcome(should_abstain: bool, did_abstain: bool) -> str:
    """Confusion-matrix cell for one QA case:
      correct_abstain — should refuse and did (good)
      missed_abstain  — should refuse but answered (BAD: hallucination risk)
      false_abstain   — answerable but refused (BAD: over-refusal)
      answered        — answerable and answered (good; check correctness)
    """
    if should_abstain:
        return "correct_abstain" if did_abstain else "missed_abstain"
    return "false_abstain" if did_abstain else "answered"
