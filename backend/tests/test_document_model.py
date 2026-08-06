"""Phase-0 Document Model tests. Pure-stdlib, offline (no DB / no fitz)."""
from app.document_model import BBox, Block, BlockKind, Document


# ── Parity gate: from_flat_pages → to_pages is the identity ──────────────
def test_flat_roundtrip_identity():
    cases = [
        [(1, "hello world")],
        [(1, "page one"), (2, "page two"), (3, "page three")],
        [(1, "line a\n\nline b\n\nline c")],          # embedded blank lines preserved
        [(1, ""), (2, "non-empty"), (3, "")],          # empty pages kept + aligned
        [(1, "unicode: café — naïve — 日本語 — ①②③")],
        [(5, "sparse"), (9, "gaps in page numbers")],
    ]
    for pages in cases:
        assert Document.from_flat_pages(pages).to_pages() == pages


def test_flat_roundtrip_empty():
    assert Document.from_flat_pages([]).to_pages() == []


# ── render() per block kind ──────────────────────────────────────────────
def test_key_value_render_keeps_label_bound():
    # The NRIC 'Race: INDIAN' class — label stays bound to value.
    assert Block(kind=BlockKind.KEY_VALUE, label="Race", value="INDIAN").render() == "Race: INDIAN"
    assert Block(kind=BlockKind.KEY_VALUE, label="", value="lonely").render() == "lonely"
    assert Block(kind=BlockKind.KEY_VALUE, label="only-label", value="").render() == "only-label"


def test_table_render_markdown():
    md = Block(kind=BlockKind.TABLE, rows=[["Date", "Amount"], ["2026-01-01", "100"]]).render()
    assert "| Date | Amount |" in md
    assert "| --- | --- |" in md
    assert "| 2026-01-01 | 100 |" in md


def test_table_render_no_header():
    md = Block(kind=BlockKind.TABLE, rows=[["a", "b"], ["c", "d"]], has_header=False).render()
    # synthetic empty header, both data rows present
    assert md.count("\n") == 3
    assert "| a | b |" in md and "| c | d |" in md


def test_figure_render():
    b = Block(kind=BlockKind.FIGURE, caption="Fig 1", description="a bar chart", ocr_text="Q1 Q2")
    assert b.render() == "[Figure] Fig 1 a bar chart Q1 Q2"
    assert Block(kind=BlockKind.FIGURE).render() == ""


def test_heading_and_paragraph_render():
    assert Block(kind=BlockKind.HEADING, text="Title", level=1).render() == "Title"
    assert Block(kind=BlockKind.PARAGRAPH, text="body").render() == "body"
    assert Block(kind=BlockKind.LIST_ITEM, text="- item").render() == "- item"


# ── to_pages joins blocks with a blank line (a chunker boundary) ──────────
def test_to_pages_joins_blocks_on_blank_line():
    doc = Document(blocks=[
        Block(kind=BlockKind.HEADING, page=1, text="Personal Information", level=2),
        Block(kind=BlockKind.KEY_VALUE, page=1, label="Race", value="INDIAN"),
        Block(kind=BlockKind.KEY_VALUE, page=1, label="Country of Birth", value="INDIA"),
    ])
    assert doc.to_pages() == [(1, "Personal Information\n\nRace: INDIAN\n\nCountry of Birth: INDIA")]


def test_empty_rendering_blocks_preserve_page():
    doc = Document(blocks=[
        Block(kind=BlockKind.FIGURE, page=1),                 # renders ""
        Block(kind=BlockKind.PARAGRAPH, page=2, text="x"),
    ])
    assert doc.to_pages() == [(1, ""), (2, "x")]


# ── bbox provenance shape ────────────────────────────────────────────────
def test_field_bboxes_shape():
    doc = Document(blocks=[
        Block(kind=BlockKind.KEY_VALUE, page=1, label="Name", value="RBG",
              bbox=BBox(1, 2, 3, 4, 100, 200, 1)),
        Block(kind=BlockKind.KEY_VALUE, page=1, label="NoBox", value="v"),  # no bbox → skipped
    ])
    fb = doc.field_bboxes()
    assert set(fb) == {"Name"}
    assert fb["Name"] == {"x0": 1, "y0": 2, "x1": 3, "y1": 4,
                          "page_w": 100, "page_h": 200, "page": 1}
