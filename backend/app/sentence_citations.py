"""R3 · per-sentence citation attribution + drop-invalid.

Pure-stdlib (offline-testable). Splits an answer into sentences and attributes
each to the evidence passage that best supports it, by content-word overlap.
Used to:

  * **drop invalid citations** — keep only passages that actually support a sentence
    (vs the old "cite every retrieved passage" behaviour)
  * build **per-sentence citations** (each grounded sentence → its source span)
  * optionally **drop unsupported sentences** from the answer (strict mode)

This is the cheap, deterministic baseline. True semantic entailment needs an LLM
judge — that's R2 (chain-of-verification); this lexical attribution is the floor
and runs at zero LLM cost.
"""
from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]+")
# Small stopword set so attribution keys on CONTENT words, not glue.
_STOP = frozenset(
    "the a an of to in on at for and or but is are was were be been being this that "
    "these those it its as by with from into your you i we they he she his her their "
    "there here will would can could should may might do does did have has had not no "
    "yes if then else than so such which who whom whose what when where why how".split()
)


def split_sentences(text: str) -> list[str]:
    """Split into sentences, also breaking on newlines / bullet lines."""
    out: list[str] = []
    for line in (text or "").split("\n"):
        line = line.strip().lstrip("-*•").strip()
        if not line:
            continue
        out.extend(s.strip() for s in _SENT_SPLIT.split(line) if s.strip())
    return out


def _content_tokens(s: str) -> set[str]:
    return {t for t in _WORD.findall((s or "").lower()) if t not in _STOP and len(t) > 1}


def _support(sentence_toks: set[str], passage_toks: set[str]) -> float:
    """Containment: fraction of the sentence's content words found in the passage."""
    if not sentence_toks:
        return 0.0
    return len(sentence_toks & passage_toks) / len(sentence_toks)


def attribute(sentences: list[str], passages: list[dict], *,
              text_key: str = "text", min_support: float = 0.5) -> list[dict]:
    """Attribute each sentence to its best-supporting passage.

    `passages` = list of dicts (each with a `text_key`). Returns one entry per
    sentence: {sentence, supported, support, source} where `source` is the
    matched passage dict (or None when below `min_support`)."""
    prepped = [(p, _content_tokens(p.get(text_key, ""))) for p in (passages or [])]
    out: list[dict] = []
    for sent in sentences:
        st = _content_tokens(sent)
        best, best_s = None, 0.0
        for p, pt in prepped:
            sc = _support(st, pt)
            if sc > best_s:
                best, best_s = p, sc
        supported = best is not None and best_s >= min_support
        out.append({"sentence": sent, "supported": supported,
                    "support": round(best_s, 3), "source": best if supported else None})
    return out


def citations_from_attributions(attrs: list[dict], *, drop_keys: tuple = ("text",)) -> list[dict]:
    """Build the citation list from supported sentences (drop-invalid: only
    grounded sentences produce a citation). De-dupes repeated (chunk, sentence)
    and strips the bulky `text` field. Each citation carries its `sentence` +
    `support` so the UI can show which sentence each source backs."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for a in attrs:
        if not a.get("supported") or not a.get("source"):
            continue
        src = {k: v for k, v in a["source"].items() if k not in drop_keys}
        key = (src.get("chunkPk"), a["sentence"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**src, "sentence": a["sentence"], "support": a["support"], "supported": True})
    return out


def supported_answer(attrs: list[dict]) -> str:
    """Rebuild the answer from only the supported sentences (strict mode)."""
    return " ".join(a["sentence"] for a in attrs if a.get("supported")).strip()


def unsupported_count(attrs: list[dict]) -> int:
    return sum(1 for a in attrs if not a.get("supported"))
