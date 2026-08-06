"""Cross-document entity resolution (query-time).

The graph stores per-document `Entity` rows, so the SAME real-world entity is
fragmented across spelling / word-order variants — e.g. a person appears as
"rajesh goda", "goda rajesh balvantrai", and "balvantrai goda", or an org as
"UBS AG" and "UBS AG Singapore Branch". This module clusters those per-doc rows
into UNIFIED identities so every cross-document consumer (related-docs, the
entity resolver, GraphRAG, and the entity profile) reasons over one entity
instead of three.

Pure-stdlib (+ canonical.py, itself pure) → unit-testable offline. Union-find
clustering; no DB, no migration. The matching reuses the signals proven in
`graph/bootstrap._find_alias` (exact canonical, subset/overlap of name tokens,
substring containment, bounded Levenshtein) — but generalised to also merge
word-order variants ("rajesh goda" ⇄ "goda rajesh balvantrai").
"""
from __future__ import annotations

import re

from app.graph.canonical import canon_name, canon_org

_TOK = re.compile(r"[a-z0-9]+")

# kinds whose variants we resolve by fuzzy name matching; others merge only on
# an exact canonical string (money/date/identifier are already value-normalised).
_NAME_KINDS = ("person", "org")


def _tokens(s: str | None) -> frozenset[str]:
    return frozenset(_TOK.findall((s or "").lower()))


def _lev_le(a: str, b: str, k: int) -> bool:
    """True iff Levenshtein(a, b) <= k. Bounded band, early-exits — cheap."""
    la, lb = len(a), len(b)
    if abs(la - lb) > k:
        return False
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        lo = max(1, i - k)
        hi = min(lb, i + k)
        if lo > 1:
            cur[lo - 1] = k + 1
        for j in range(lo, hi + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        if min(cur[lo:hi + 1] or [k + 1]) > k:
            return False
        prev = cur
    return prev[lb] <= k


def canon_for(kind: str, text: str, canonical: str | None) -> str:
    """Best canonical string for a mention. Falls back to normalising `text` when
    the stored `canonical` is empty (money/identifier rows often have none)."""
    if canonical:
        return canonical
    if kind == "org":
        return canon_org(text)
    if kind == "person":
        return canon_name(text)
    return (text or "").strip().lower()


def same_identity(kind: str, ca: str, cb: str) -> bool:
    """Do two canonicals denote the same real-world entity of this kind?"""
    if not ca or not cb:
        return ca == cb
    if ca == cb:
        return True
    if kind not in _NAME_KINDS:
        return False  # value kinds: exact canonical only
    ta, tb = _tokens(ca), _tokens(cb)
    if not ta or not tb:
        return False
    inter = ta & tb
    # word-order / middle-name / branch: one token set contains the other,
    # sharing >= 2 tokens (a single shared surname is NOT enough → no false merge
    # of "rajesh goda" with "priya goda").
    if (ta <= tb or tb <= ta) and len(inter) >= min(2, len(ta), len(tb)):
        return True
    # strong overlap even without full containment. STRICT > 0.5: two distinct
    # 3-token family names sharing exactly 2 tokens ("goda rajesh balvantrai" vs
    # "kalyani goda rajesh", Jaccard 0.5) must NOT merge.
    if len(inter) >= 2 and len(inter) / len(ta | tb) > 0.5:
        return True
    # substring containment (long-form ⊇ short-form)
    lo, hi = sorted((ca, cb), key=len)
    if lo and lo in hi and len(lo) / len(hi) >= 0.55:
        return True
    # typo tolerance on short single/near-single-token names
    if len(inter) >= 1 and _lev_le(ca, cb, 3):
        return True
    return False


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class Identity:
    """A resolved cross-document entity: its display name, kind, every mention row,
    the canonical variants folded in, and the set of documents it appears in."""

    def __init__(self, kind: str):
        self.kind = kind
        self.members: list[dict] = []
        self.canonicals: set[str] = set()
        self.doc_pks: set[int] = set()

    def add(self, row: dict, canon: str) -> None:
        self.members.append(row)
        if canon:
            self.canonicals.add(canon)
        if row.get("document_pk") is not None:
            self.doc_pks.add(row["document_pk"])

    @property
    def name(self) -> str:
        """Display name: the longest CLEAN surface form (no newline / bracket-token
        / joint-account '&/OR' noise), e.g. 'GODA RAJESH BALVANTRAI' over both
        'Rajesh Goda' and a messy 'GODA RAJESH BALVANTRAI &/OR KALYANIGODA…'."""
        texts = [(m.get("text") or "").strip() for m in self.members]
        texts = [t for t in texts if t]
        if not texts:
            return max(self.canonicals, key=len) if self.canonicals else ""
        clean = [t for t in texts
                 if "\n" not in t and "[" not in t and "/OR" not in t.upper() and len(t) <= 60]
        return max(clean or texts, key=len)

    @property
    def key(self) -> str:
        """Stable identity key = the longest canonical."""
        return max(self.canonicals, key=len) if self.canonicals else ""


def cluster(rows: list[dict]) -> list[Identity]:
    """Cluster entity rows into identities. `rows`: dicts with kind/text/canonical/
    document_pk. Resolution is per-kind (a person never merges with an org)."""
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", ""), []).append(r)

    out: list[Identity] = []
    for kind, group in by_kind.items():
        canons = [canon_for(kind, r.get("text", ""), r.get("canonical")) for r in group]
        uf = _UF(len(group))
        # O(n^2) within a kind — entity counts per user are small (~hundreds).
        for i in range(len(group)):
            if not canons[i]:
                continue
            for j in range(i + 1, len(group)):
                if canons[j] and same_identity(kind, canons[i], canons[j]):
                    uf.union(i, j)
        clusters: dict[int, Identity] = {}
        for i, r in enumerate(group):
            root = uf.find(i)
            ident = clusters.setdefault(root, Identity(kind))
            ident.add(r, canons[i])
        out.extend(clusters.values())
    return out


# A name-shaped query prefers a person, then org, over other kinds — so "Rajesh
# Goda" resolves to the person identity, not a mis-extracted org that happens to
# share the tokens.
_KIND_PRIORITY = {"person": 3, "org": 2}


def best_match(rows: list[dict], query: str, kind: str | None = None) -> Identity | None:
    """Resolve `query` to the single best identity among `rows`. Token-overlap
    match against each identity's canonicals; a 2+-word query must share >= 2
    tokens (so 'Rajesh Goda' hits 'goda rajesh balvantrai', not any lone 'goda').
    Ties break by kind (person > org > …) then document count (prominence)."""
    q = _tokens(query)
    if not q:
        return None
    best: Identity | None = None
    best_rank: tuple = (0, 0, 0)
    need = 2 if len(q) >= 2 else 1
    for ident in cluster(rows):
        if kind and ident.kind != kind:
            continue
        score = max((len(q & _tokens(c)) for c in ident.canonicals), default=0)
        if score < need:
            continue
        rank = (score, _KIND_PRIORITY.get(ident.kind, 1), len(ident.doc_pks))
        if rank > best_rank:
            best, best_rank = ident, rank
    return best
