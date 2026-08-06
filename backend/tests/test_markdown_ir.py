"""Phase 2 · Markdown → IR parser. Pure-stdlib, offline."""
from app.document_model import BlockKind
from app.markdown_ir import blocks_from_markdown, document_from_pages_markdown


def _kinds(blocks):
    return [b.kind for b in blocks]


def test_headings():
    bs = blocks_from_markdown("# Title\n\n## Section\n\n### Sub")
    assert _kinds(bs) == [BlockKind.HEADING] * 3
    assert [b.level for b in bs] == [1, 2, 3]
    assert [b.text for b in bs] == ["Title", "Section", "Sub"]


def test_key_value_forms():
    md = "- **Name**: GODA RAJESH\n- **Race**: INDIAN\n- **Country/Place of birth**: INDIA"
    bs = blocks_from_markdown(md)
    assert _kinds(bs) == [BlockKind.KEY_VALUE] * 3
    assert (bs[1].label, bs[1].value) == ("Race", "INDIAN")           # the NRIC class
    assert (bs[2].label, bs[2].value) == ("Country/Place of birth", "INDIA")
    # serialised text keeps the label bound to the value
    assert bs[1].render() == "Race: INDIAN"


def test_kv_without_bold():
    bs = blocks_from_markdown("Nationality: Singaporean")
    assert bs[0].kind == BlockKind.KEY_VALUE
    assert (bs[0].label, bs[0].value) == ("Nationality", "Singaporean")


def test_not_kv_time_and_url_and_prose():
    # colon-without-space (time / URL) and a long sentence must NOT become key_value
    for line in ("Meeting at 10:30 today in room 4",
                 "See http://example.com for details",
                 "This is a normal sentence, with a clause, that runs on and on and on here."):
        b = blocks_from_markdown(line)[0]
        assert b.kind == BlockKind.PARAGRAPH, f"{line!r} → {b.kind}"


def test_gfm_table():
    md = "| Date | Amount |\n| --- | --- |\n| 2026-01-01 | 100 |\n| 2026-01-02 | -20 |"
    bs = blocks_from_markdown(md)
    assert len(bs) == 1 and bs[0].kind == BlockKind.TABLE
    assert bs[0].rows[0] == ["Date", "Amount"]
    assert bs[0].rows[1] == ["2026-01-01", "100"]
    assert bs[0].render().startswith("| Date | Amount |")


def test_lists_and_paragraphs():
    bs = blocks_from_markdown("- first item\n- second item\n\nA plain paragraph of text here.")
    assert _kinds(bs) == [BlockKind.LIST_ITEM, BlockKind.LIST_ITEM, BlockKind.PARAGRAPH]


def test_nric_page_roundtrip():
    md = (
        "# REPUBLIC OF SINGAPORE\n\n"
        "## Personal Information\n"
        "- **Name**: GODA RAJESH BALVANTRAI\n"
        "- **Race**: INDIAN\n"
        "- **Date of birth**: 10-10-1968\n"
        "- **Country/Place of birth**: INDIA\n"
    )
    doc = document_from_pages_markdown([(1, md)])
    # key_value blocks preserved, and the serialised page keeps 'Race: INDIAN' intact
    kv = {(b.label, b.value) for b in doc.blocks if b.kind == BlockKind.KEY_VALUE}
    assert ("Race", "INDIAN") in kv
    assert ("Country/Place of birth", "INDIA") in kv
    page_text = doc.to_pages()[0][1]
    assert "Race: INDIAN" in page_text
    assert "Name: [PERSON" not in page_text        # the old linearisation bug is gone


def test_empty_page_preserved():
    doc = document_from_pages_markdown([(1, ""), (2, "# Hi")])
    assert doc.to_pages() == [(1, ""), (2, "Hi")]


def test_strips_inline_markdown_in_prose():
    b = blocks_from_markdown("This is **bold** and *italic* and `code` and a [link](http://x).")[0]
    assert b.render() == "This is bold and italic and code and a link."
    assert "*" not in b.render() and "`" not in b.render()


def test_no_bold_leak_on_multiline_value():
    # a bold label whose value wraps to the next line is NOT a same-line KV; it must
    # still not leak literal `**` into the serialised text.
    md = "**Address**:\n3 CENTRAL BOULEVARD\n#13-01 SINGAPORE 018965"
    txt = document_from_pages_markdown([(1, md)]).to_pages()[0][1]
    assert "**" not in txt
    assert "Address:" in txt and "3 CENTRAL BOULEVARD" in txt


def test_table_cells_stripped():
    b = blocks_from_markdown("| **Total** | `100` |\n| --- | --- |\n| Sum | 100 |")[0]
    assert b.rows[0] == ["Total", "100"]
