"""Offline unit tests for layout-aware chunking (Reducto-parity G5).

Pure-stdlib: imports only `app.chunking` (no fitz/DB/LLM), so it runs in CI and
locally with `python -m pytest` OR directly: `python backend/tests/test_chunking.py`.
"""
from __future__ import annotations

from app import chunking


def _para(word: str, n: int) -> str:
    """A paragraph of `n` space-separated copies of `word` (no blank lines)."""
    return " ".join([word] * n)


def test_split_blocks_normalizes_and_drops_empty():
    text = "  Hello   world \n\n\n  Second\tpara  \n\n   \n\nThird"
    assert chunking.split_blocks(text) == ["Hello world", "Second para", "Third"]


def test_empty_or_blank_page_yields_nothing():
    assert chunking.chunk_page_text("", 100, 10) == []
    assert chunking.chunk_page_text("   \n\n  \t ", 100, 10) == []


def test_short_page_is_one_chunk():
    out = chunking.chunk_page_text("Alpha beta gamma.", target=100, overlap=10)
    assert len(out) == 1
    text, cs, ce = out[0]
    assert text == "Alpha beta gamma."
    assert (cs, ce) == (0, len(text))


def test_paragraphs_are_never_split():
    # 4 distinct blocks, each ~20 chars; target 50 → packs 2-ish per chunk.
    blocks = ["AAAA " * 4, "BBBB " * 4, "CCCC " * 4, "DDDD " * 4]
    page = "\n\n".join(b.strip() for b in blocks)
    norm_blocks = chunking.split_blocks(page)
    out = chunking.chunk_page_text(page, target=50, overlap=8)
    assert len(out) >= 2
    # Every original block must appear intact (uncut) inside some chunk.
    for b in norm_blocks:
        assert any(b in text for text, _, _ in out), f"block split across chunks: {b!r}"
    # No chunk exceeds target by more than one block's slack (blocks are whole).
    assert all(len(text) <= 50 + max(len(b) for b in norm_blocks) for text, _, _ in out)


def test_overlap_carries_boundary_block():
    # Small uniform blocks so a boundary block (<= overlap) is carried forward.
    blocks = ["x1 y1", "x2 y2", "x3 y3", "x4 y4", "x5 y5"]
    page = "\n\n".join(blocks)
    out = chunking.chunk_page_text(page, target=14, overlap=6)
    assert len(out) >= 2
    # Consecutive chunks should share at least one block (paragraph overlap).
    shared = 0
    for a, b in zip(out, out[1:]):
        a_blocks = set(a[0].split("  ")) if False else set(_blocks_of(a[0]))
        b_blocks = set(_blocks_of(b[0]))
        if a_blocks & b_blocks:
            shared += 1
    assert shared >= 1


def _blocks_of(joined: str) -> list[str]:
    # Reconstruct individual 2-token blocks ("xN yN") from a joined chunk.
    toks = joined.split()
    return [" ".join(toks[i:i + 2]) for i in range(0, len(toks), 2)]


def test_oversized_block_is_windowed():
    big = _para("word", 200)  # ~1000 chars, one block, no blank lines
    out = chunking.chunk_page_text(big, target=100, overlap=20)
    assert len(out) > 1  # had to window it
    # Windows cover the start of the block and stay within target.
    assert all(len(text) <= 100 for text, _, _ in out)
    assert out[0][1] == 0  # first window starts at offset 0


def test_protected_span_not_split():
    # An oversized block with a protected region in the middle.
    head = _para("a", 30)
    mrz = "MRZ<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<BLOCK"
    tail = _para("b", 30)
    block = f"{head} {mrz} {tail}"
    span_start = block.index("MRZ")
    span = (span_start, span_start + len(mrz))

    def protect(_t: str):
        return span

    out = chunking.chunk_page_text(block, target=40, overlap=8, protect_span_fn=protect)
    # The full MRZ string must appear intact in exactly one window.
    assert sum(1 for text, _, _ in out if mrz in text) == 1, "MRZ was split across windows"


def test_offsets_are_ordered_and_in_bounds():
    page = "\n\n".join([_para("z", 10), _para("q", 80), _para("k", 10)])
    page_len = len(" ".join(chunking.split_blocks(page)))
    out = chunking.chunk_page_text(page, target=60, overlap=12)
    for text, cs, ce in out:
        assert 0 <= cs < ce <= page_len
        assert len(text) > 0


def test_no_runaway_on_pathological_input():
    # Many tiny blocks; must terminate and respect max_chunks.
    page = "\n\n".join(f"b{i}" for i in range(500))
    out = chunking.chunk_page_text(page, target=20, overlap=4, max_chunks=10)
    assert len(out) <= 10


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  PASS {fn.__name__}")
    print(f"\n{passed}/{len(fns)} chunking tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
