"""Cross-document entity resolution (graph/resolve.py). Pure, offline."""
from app.graph.resolve import best_match, cluster, same_identity


def _rows(pairs):
    # (kind, text, canonical, doc_pk)
    return [{"kind": k, "text": t, "canonical": c, "document_pk": d} for k, t, c, d in pairs]


def test_person_word_order_variants_merge():
    rows = _rows([
        ("person", "Rajesh Goda", "rajesh goda", 1),
        ("person", "GODA RAJESH BALVANTRAI", "goda rajesh balvantrai", 2),
        ("person", "Balvantrai Goda", "balvantrai goda", 3),
    ])
    clusters = cluster(rows)
    assert len(clusters) == 1                      # all three are one identity
    ident = clusters[0]
    assert ident.doc_pks == {1, 2, 3}
    assert ident.name == "GODA RAJESH BALVANTRAI"  # longest surface form


def test_org_branch_variant_merges():
    rows = _rows([
        ("org", "UBS AG", "ubs ag", 1),
        ("org", "UBS AG Singapore Branch", "ubs ag singapore branch", 2),
        ("org", "Smart Audit Pte. Ltd.", "smart audit", 3),
        ("org", "Smart Audit", "smart audit", 4),
    ])
    ubs = next(c for c in cluster(rows) if "UBS" in c.name)
    assert ubs.doc_pks == {1, 2}
    smart = next(c for c in cluster(rows) if "Smart" in c.name)
    assert smart.doc_pks == {3, 4}


def test_shared_surname_does_not_false_merge():
    rows = _rows([
        ("person", "Rajesh Goda", "rajesh goda", 1),
        ("person", "Priya Goda", "priya goda", 2),   # different person, same surname
    ])
    assert len(cluster(rows)) == 2


def test_kinds_never_cross():
    rows = _rows([
        ("person", "Acme", "acme", 1),
        ("org", "Acme", "acme", 2),
    ])
    assert len(cluster(rows)) == 2


def test_same_identity_predicate():
    assert same_identity("person", "rajesh goda", "goda rajesh balvantrai")
    assert same_identity("org", "ubs ag", "ubs ag singapore branch")
    assert not same_identity("person", "rajesh goda", "priya goda")
    assert not same_identity("money", "100 sgd", "100.00 sgd")   # value kinds: exact only
    assert same_identity("person", "jon smith", "john smith")    # typo (Levenshtein)


def test_best_match_resolves_query():
    rows = _rows([
        ("person", "Rajesh Goda", "rajesh goda", 1),
        ("person", "GODA RAJESH BALVANTRAI", "goda rajesh balvantrai", 2),
        ("org", "UBS AG", "ubs ag", 3),
    ])
    m = best_match(rows, "Rajesh Goda")
    assert m is not None and m.kind == "person" and m.doc_pks == {1, 2}
    assert best_match(rows, "UBS", kind="org").name == "UBS AG"
    assert best_match(rows, "nonexistent entity") is None


def test_family_members_sharing_surname_do_not_merge():
    # two distinct 3-token names sharing exactly 2 tokens (Jaccard 0.5) must stay apart
    rows = _rows([
        ("person", "GODA RAJESH BALVANTRAI", "goda rajesh balvantrai", 1),
        ("person", "KALYANI GODA RAJESH", "kalyani goda rajesh", 2),
    ])
    assert len(cluster(rows)) == 2
    assert not same_identity("person", "goda rajesh balvantrai", "kalyani goda rajesh")


def test_best_match_prefers_person_over_org():
    rows = _rows([
        ("person", "GODA RAJESH BALVANTRAI", "goda rajesh balvantrai", 1),
        ("person", "Rajesh Goda", "rajesh goda", 2),
        ("org", "KALYANI GODA RAJESH LLC", "kalyani goda rajesh llc", 3),   # mis-extracted, shares tokens
    ])
    m = best_match(rows, "Rajesh Goda")
    assert m is not None and m.kind == "person"
    assert m.doc_pks == {1, 2}


def test_empty_canonical_falls_back_to_text():
    rows = _rows([
        ("org", "Smart Audit Pte Ltd", "", 1),   # empty canonical → normalise text
        ("org", "Smart Audit", "smart audit", 2),
    ])
    assert len(cluster(rows)) == 1
