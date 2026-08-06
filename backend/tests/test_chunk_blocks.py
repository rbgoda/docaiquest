"""Phase 4 · block-aware chunking (chunk_blocks + table header retention)."""
from app.chunking import chunk_blocks
from app.document_model import BBox, Block, BlockKind


def test_small_blocks_pack_into_one_chunk():
    blocks = [
        Block(kind=BlockKind.HEADING, page=1, text="Personal Information", level=2),
        Block(kind=BlockKind.KEY_VALUE, page=1, label="Race", value="INDIAN"),
        Block(kind=BlockKind.KEY_VALUE, page=1, label="Country of Birth", value="INDIA"),
    ]
    chunks = chunk_blocks(blocks, target=1000, overlap=150)
    assert len(chunks) == 1
    text = chunks[0][0]
    assert "Race: INDIAN" in text and "Country of Birth: INDIA" in text


def test_key_value_never_split_even_when_page_overflows():
    # many KV blocks exceeding target — each stays atomic, packed across chunks
    blocks = [Block(kind=BlockKind.KEY_VALUE, page=1, label=f"Field{i}", value="v" * 40)
              for i in range(50)]
    chunks = chunk_blocks(blocks, target=300, overlap=0)
    assert len(chunks) > 1
    # no chunk ever cuts a "FieldN: vvvv" pair in half
    joined = "\n\n".join(c[0] for c in chunks)
    for i in range(50):
        assert f"Field{i}: " + "v" * 40 in joined


def test_large_table_row_split_with_header_repeated():
    rows = [["Date", "Amount", "Desc"]]
    rows += [[f"2026-01-{i:02d}", str(i * 10), "item " + "x" * 20] for i in range(1, 60)]
    tbl = Block(kind=BlockKind.TABLE, page=1, rows=rows, has_header=True)
    chunks = chunk_blocks([tbl], target=300, overlap=0)
    assert len(chunks) > 1                                   # actually split
    for text, _s, _e, _b in chunks:
        assert "| Date | Amount | Desc |" in text            # header repeated
        assert "| --- | --- | --- |" in text                # separator repeated
    # every data row appears exactly once across the pieces
    all_text = "\n".join(c[0] for c in chunks)
    for i in range(1, 60):
        assert f"| 2026-01-{i:02d} | {i * 10} |" in all_text


def test_offsets_index_serialised_page_string():
    blocks = [
        Block(kind=BlockKind.PARAGRAPH, page=1, text="Alpha beta gamma."),
        Block(kind=BlockKind.PARAGRAPH, page=1, text="Delta epsilon zeta."),
    ]
    page = "\n\n".join(b.render() for b in blocks)
    chunks = chunk_blocks(blocks, target=1000, overlap=150)
    text, s, e, _b = chunks[0]
    assert page[s:e] == page                                # single chunk spans the page


def test_empty_blocks_yield_nothing():
    assert chunk_blocks([], 1000, 150) == []
    assert chunk_blocks([Block(kind=BlockKind.PARAGRAPH, page=1, text="")], 1000, 150) == []


def test_forward_bbox_union(monkeypatch=None):
    # Phase 5 · a chunk's bbox is the union of its composing blocks' boxes.
    b1 = Block(kind=BlockKind.PARAGRAPH, page=1, text="alpha", bbox=BBox(10, 20, 50, 30, 600, 800, 1))
    b2 = Block(kind=BlockKind.PARAGRAPH, page=1, text="beta", bbox=BBox(10, 40, 80, 55, 600, 800, 1))
    _t, _s, _e, bbox = chunk_blocks([b1, b2], 1000, 150)[0]
    assert bbox == {"page": 1, "x0": 10, "y0": 20, "x1": 80, "y1": 55}


def test_forward_bbox_none_when_absent():
    b = Block(kind=BlockKind.KEY_VALUE, page=1, label="Race", value="INDIAN")   # no bbox
    assert chunk_blocks([b], 1000, 150)[0][3] is None
