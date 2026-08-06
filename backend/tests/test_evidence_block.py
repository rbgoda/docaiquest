"""Behavior-preservation tests for the consolidated evidence-block builder.

`format_evidence_block` replaced five hand-copied loops (doc chat, workspace chat,
`/v1/ask` group + owner). Each assertion below reproduces the EXACT legacy string a
given call site used to emit, so the DRY consolidation can't silently change any
RAG prompt. Pure (no DB / no models)."""
from types import SimpleNamespace

from app.services.chat_pipeline import format_evidence_block


def _hit(text, page, name="Doc A"):
    # mirrors the retrieval-hit / chunk attrs the builder reads
    return SimpleNamespace(text=text, page=page, document_name=name)


def test_api_v1_owner_and_group_shape_cap_500_show_name():
    """api_v1 group_answer + _rag_answer_for_owner: `[E# · name · page N] snippet[:500]`."""
    hits = [_hit("  the   quick brown\nfox ", 3, "Passport.pdf"),
            _hit("second doc text", 7, "W2.pdf")]
    out = format_evidence_block(hits, cap=500, show_name=True)
    assert out == (
        "[E1 · Passport.pdf · page 3] the quick brown fox\n\n"
        "[E2 · W2.pdf · page 7] second doc text")


def test_cap_truncates_at_boundary():
    long = "x " * 400  # 800 chars pre-collapse; collapses to 'x x x...'
    out = format_evidence_block([_hit(long, 1, "D")], cap=500, show_name=True)
    body = out.split("] ", 1)[1]
    assert len(body) == 500


def test_workspace_chat_shape_with_type():
    """workspace_chat: `[E# · name · type=T · page N] snippet[:500]`, empty fallback."""
    hits = [_hit("alpha", 2, "Invoice.pdf")]
    out = format_evidence_block(hits, cap=500, show_name=True,
                               type_by_name={"Invoice.pdf": "invoice"},
                               empty="(no evidence retrieved)")
    assert out == "[E1 · Invoice.pdf · type=invoice · page 2] alpha"


def test_workspace_chat_type_default_document():
    hits = [_hit("beta", 5, "Unknown.pdf")]
    out = format_evidence_block(hits, cap=500, show_name=True, type_by_name={})
    assert out == "[E1 · Unknown.pdf · type=document · page 5] beta"


def test_doc_chat_shape_no_name_cap_600():
    """doc_chat: `[E# · page N] snippet[:600]`, '(no chunks retrieved)' fallback."""
    out = format_evidence_block([_hit("gamma", 9)], cap=600, empty="(no chunks retrieved)")
    assert out == "[E1 · page 9] gamma"


def test_chat_pipeline_prefix_lines_then_chunks():
    """chat_pipeline: highlight [H*] lines prepended, then [E# · page N] cap 600."""
    hl = ["[H1 · YOUR HIGHLIGHT · page 1] noted"]
    out = format_evidence_block([_hit("delta", 4)], cap=600,
                               empty="(no evidence retrieved)", prefix_lines=hl)
    assert out == "[H1 · YOUR HIGHLIGHT · page 1] noted\n\n[E1 · page 4] delta"


def test_empty_items_returns_fallback():
    assert format_evidence_block([], empty="(no evidence retrieved)") == "(no evidence retrieved)"
    assert format_evidence_block([]) == ""  # api_v1 twins used no fallback (ev always non-empty)


def test_empty_prefix_only_still_joins():
    out = format_evidence_block([], prefix_lines=["[H1 · x] y"], empty="none")
    assert out == "[H1 · x] y"
