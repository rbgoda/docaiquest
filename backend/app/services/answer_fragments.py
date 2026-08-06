"""#3 · Assemble the cross-doc answer RULES from a BASE + only the fragments the
question's shape needs (Kezhan Shi, "Assemble each RAG prompt from a base prompt
plus the rules each question needs").

The legacy prompt sent ALL rules on every call (a "kitchen-sink" block). Here the
BASE rules always apply (grounding, attribution, no-invent) and shape-specific
fragments (table / single-line / structured-field-preference / type-filter) are
appended ONLY when the question calls for them. The included set is always a SUBSET
of the legacy block, so behaviour is same-or-cleaner (less cross-rule interference)
and cheaper (fewer tokens). `select_answer_fragments()` is pure + deterministic so
the 1088-question sweep can validate routing with zero LLM calls.

Wired behind `settings.answer_fragments_enabled` (default off) — flip to A/B.
"""
from __future__ import annotations

import re

# Always applied — the non-negotiables (verbatim from the legacy block).
BASE_RULES: list[str] = [
    "Use the STRUCTURED FIELDS and evidence excerpts below. If neither contains "
    "the answer, reply: 'Not found in the retrieved evidence.'",
    "ALWAYS name the source document when you state a fact (e.g. 'Per "
    "Insurance_Cert.pdf, …'). Facts may come from different documents — keep them "
    "attributed.",
    "Never invent. Never explain what you're doing.",
]

# Shape-specific fragments — appended only when the question needs them.
FRAGMENTS: dict[str, str] = {
    "attribute": (
        "For attribute/value questions (who is the applicant, the total, a date), "
        "PREFER the STRUCTURED FIELDS — they carry ROLE labels, so use them to pick "
        "the RIGHT value (e.g. the Applicant name, NOT an emergency-contact or other "
        "name that also appears in the text). Use evidence excerpts to confirm."
    ),
    "compare": (
        "Comparison / 'across all' questions → a short markdown table, one row per "
        "document."
    ),
    "single": (
        "Single-value question → one line with the value + its source doc."
    ),
    "of_kind": (
        "When the question asks for documents OF a kind (e.g. 'national IDs', "
        "'invoices', 'insurance policies'), include ONLY documents whose TYPE matches "
        "the kind asked for. A document that merely MENTIONS an identifier is NOT that "
        "kind — e.g. an insurance certificate that lists the holder's NRIC is NOT a "
        "national ID; do not list it as one."
    ),
}

# Detection — deterministic, ordered so the strongest shape wins.
_RE_COMPARE = re.compile(
    r"\b(compare|comparison|across all|each of|all (?:my|the)|list all|every|both|"
    r"side by side|which is (?:my|the) (?:oldest|newest|largest|highest|lowest))\b", re.I)
_RE_OF_KIND = re.compile(
    r"\b(which|list|show|how many|do (?:i|any))\b.{0,40}?\b(documents?|invoices?|"
    r"ids?|passports?|statements?|reports?|certificates?|receipts?|licen[cs]es?|"
    r"policies|contracts?|forms?|bills?)\b", re.I)
_RE_SINGLE = re.compile(
    r"\b(what(?:'s| is)|how much|how many|when(?:'s| is)|balance|total|amount|due|"
    r"date|number|value|price|rate|expir|valid (?:until|through|to))\b", re.I)
_RE_ATTRIBUTE = re.compile(
    r"\b(who(?:'s| is)?|whose|name(?:d)?|applicant|holder|owner|issued to|payee|"
    r"patient|beneficiary|employer|employee|landlord|tenant|buyer|seller|vendor|"
    r"account holder|policyholder|insured)\b", re.I)


def select_answer_fragments(question: str) -> list[str]:
    """Return the ordered list of fragment keys this question needs (BASE is
    always applied and not listed). Pure + deterministic."""
    q = question or ""
    picks: list[str] = []
    is_compare = bool(_RE_COMPARE.search(q))
    if is_compare:
        picks.append("compare")
    if _RE_OF_KIND.search(q):
        picks.append("of_kind")
    # 'single' only when it's NOT a multi-doc comparison (a comparison wants a table).
    if _RE_SINGLE.search(q) and not is_compare:
        picks.append("single")
    if _RE_ATTRIBUTE.search(q):
        picks.append("attribute")
    return picks


def build_rules_block(question: str) -> tuple[str, list[str]]:
    """Return (rules_block_text, fragments_applied) for the given question."""
    picks = select_answer_fragments(question)
    rules = list(BASE_RULES) + [FRAGMENTS[p] for p in picks]
    block = "RULES:\n" + "\n".join(f"  · {r}" for r in rules)
    return block, picks


def expected_format(question: str) -> str:
    """Human-readable expected output shape (for the eval report)."""
    picks = select_answer_fragments(question)
    if "compare" in picks:
        return "table"
    if "of_kind" in picks:
        return "filtered-list"
    if "single" in picks:
        return "one-line"
    if "attribute" in picks:
        return "attribute"
    return "free"
