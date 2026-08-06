"""Markdown → Document Model (IR) parser.

The structured-vision path (Phase 2) asks the VLM to transcribe a scanned page as
GitHub-Flavored Markdown (headings, GFM tables, `**Label:** value` fields, lists).
This turns that Markdown into typed IR blocks so the *type* survives to extraction —
a `key_value` block keeps "Race" bound to "INDIAN" (the NRIC misattribution class),
and a `table` block stays a real table.

Pure-stdlib (regex only) so it unit-tests offline, like `document_model` / `chunking`.
Conservative by design: anything it can't confidently classify becomes a paragraph, so
the serialised text is always faithful even when the block *type* is imperfect.
"""
from __future__ import annotations

import re

from app.document_model import Block, BlockKind, Document, SOURCE_VISION

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# A GFM header separator: |---|:--:|---| (2+ dashes per cell, optional colons/pipes).
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+\S)\s*$")
# `Label: value` / `**Label:** value` / `- Label: value`. Colon MUST be followed by
# whitespace so we don't match times (10:30) or URLs (http://x).
_KV = re.compile(r"^\s*(?:[-*+]\s+)?\*{0,2}\s*([^:*|]{1,40}?)\s*\*{0,2}\s*:\s+(\S.*?)\s*$")


def _strip_inline(s: str) -> str:
    """Remove inline Markdown emphasis/link syntax so no `**`/`*`/`` ` ``/`[x](y)`
    leaks into the retrieval/extraction text (a bold label whose value wrapped to
    the next line would otherwise serialise as literal `**Address**:`)."""
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)                 # **bold**
    s = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"\1", s)  # *italic*
    s = re.sub(r"__([^_]+)__", r"\1", s)                     # __bold__
    s = re.sub(r"`([^`]+)`", r"\1", s)                       # `code`
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)           # [text](url) → text
    return s.strip()


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # split on unescaped pipes
    cells = re.split(r"(?<!\\)\|", s)
    return [_strip_inline(c.strip().replace("\\|", "|")) for c in cells]


def _looks_like_kv(label: str, value: str) -> bool:
    """Accept a form-field pair, reject prose that merely contains a colon."""
    label = label.strip()
    if not label or not value.strip():
        return False
    if len(label) > 40 or len(label.split()) > 6:
        return False
    # a label is words/spaces/a few separators, not a sentence
    return re.fullmatch(r"[\w()/&.,'\- ]+", label) is not None


def blocks_from_markdown(md: str, *, page: int = 1, source: str = SOURCE_VISION) -> list[Block]:
    """Parse one page of Markdown into IR blocks."""
    lines = (md or "").split("\n")
    blocks: list[Block] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            text = " ".join(p.strip() for p in para if p.strip()).strip()
            if text:
                blocks.append(Block(kind=BlockKind.PARAGRAPH, page=page,
                                    text=_strip_inline(text), source=source))
            para.clear()

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            flush_para()
            i += 1
            continue

        m = _HEADING.match(s)
        if m:
            flush_para()
            blocks.append(Block(kind=BlockKind.HEADING, page=page, level=len(m.group(1)),
                                text=_strip_inline(m.group(2).strip()), source=source))
            i += 1
            continue

        # GFM table: a pipe row immediately followed by a header separator.
        if s.startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1].strip()):
            flush_para()
            rows = [_split_row(s)]
            i += 2  # consume header + separator
            while i < n and lines[i].strip().startswith("|"):
                if _TABLE_SEP.match(lines[i].strip()):
                    i += 1
                    continue
                rows.append(_split_row(lines[i].strip()))
                i += 1
            blocks.append(Block(kind=BlockKind.TABLE, page=page, rows=rows, has_header=True, source=source))
            continue

        m = _KV.match(s)
        if m and _looks_like_kv(m.group(1), m.group(2)):
            flush_para()
            blocks.append(Block(kind=BlockKind.KEY_VALUE, page=page,
                                label=_strip_inline(m.group(1).strip()),
                                value=_strip_inline(m.group(2).strip()), source=source))
            i += 1
            continue

        m = _LIST.match(s)
        if m:
            flush_para()
            blocks.append(Block(kind=BlockKind.LIST_ITEM, page=page,
                                text=_strip_inline(m.group(1).strip()), source=source))
            i += 1
            continue

        para.append(s)
        i += 1

    flush_para()
    return blocks


def document_from_pages_markdown(pages_md: list[tuple[int, str]], *, source: str = SOURCE_VISION) -> Document:
    """Build a Document from `[(page_number, markdown), ...]`. Empty pages are kept as an
    empty paragraph block so the page index stays aligned (matches the flat parsers)."""
    blocks: list[Block] = []
    for page, md in pages_md:
        page_blocks = blocks_from_markdown(md, page=page, source=source)
        if not page_blocks:
            page_blocks = [Block(kind=BlockKind.PARAGRAPH, page=page, text="", source=source)]
        blocks.extend(page_blocks)
    return Document(blocks=blocks)
