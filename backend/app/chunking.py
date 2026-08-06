"""Layout-aware chunking (Reducto-parity G5).

Pure-stdlib (no fitz / sqlalchemy / pydantic) so it unit-tests offline.

The old chunker flattened the whole page to single spaces and cut fixed
~1000-char windows, which split sentences and sections mid-stream and hurt
retrieval. This version keeps paragraph structure: split the page into blocks
on blank lines, normalize each block's internal whitespace, then pack WHOLE
blocks into ~target-sized chunks without splitting a block. A block larger than
the target (a wall-of-text page) falls back to a sliding window, with an
optional "protected span" guard (passport/ID MRZ) so a region that must stay
intact is not cut.

Returns spans as (text, char_start, char_end); offsets index a normalized page
string (blocks joined by single spaces). Emitted text is whitespace-normalized
so PyMuPDF `page.search_for()` keeps locating bboxes.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Optional

_TOK = re.compile(r"[a-z0-9]+")

# Paragraph boundary: a blank line (optionally with trailing spaces/tabs).
_BLANKLINE = re.compile(r"\n[ \t]*\n+")

# A protected-span function maps a text → (start, end) of a region that must
# not be split (or None). Used for MRZ blocks; kept injectable so this module
# stays dependency-free.
ProtectSpanFn = Callable[[str], Optional[tuple]]


def split_blocks(text: str) -> list[str]:
    """Split into paragraph blocks on blank lines; normalize each block's
    internal whitespace to single spaces; drop empties."""
    blocks: list[str] = []
    for raw in _BLANKLINE.split(text):
        norm = " ".join(raw.split())
        if norm:
            blocks.append(norm)
    return blocks


def _window(text: str, base: int, target: int, overlap: int,
            protect: ProtectSpanFn | None, sentence_aware: bool = False) -> list[tuple[str, int, int]]:
    """Sliding-window an oversized block. `base` is the block's start offset in
    the normalized page string. A protected span (e.g. MRZ) is never split.

    RAG-roadmap #4 · when `sentence_aware`, a cut snaps back to the nearest sentence
    boundary in the window's tail so chunks don't end mid-sentence. Behavior-preserving
    when off: `end` stays at start+target and the step is unchanged."""
    n = len(text)
    span = protect(text) if protect else None
    step = max(1, target - overlap)
    out: list[tuple[str, int, int]] = []
    start = 0
    guard = 0
    while start < n and guard < 100:
        end = min(start + target, n)
        # If the natural cut lands inside a protected span, extend to its end.
        if span is not None and start <= span[0] < end < span[1]:
            end = min(span[1], n)
        elif sentence_aware and end < n:
            lo = start + max(1, int(target * 0.6))   # only snap within the tail 40%
            cut = -1
            for sep in (". ", "! ", "? ", ".\n", "; "):
                cut = max(cut, text.rfind(sep, lo, end))
            if cut > start:
                end = cut + 1
        out.append((text[start:end], base + start, base + end))
        if end >= n:
            break
        start = max(start + 1, end - overlap) if sentence_aware else start + step
        guard += 1
    return out


def chunk_page_text(text: str, target: int, overlap: int, *,
                    protect_span_fn: ProtectSpanFn | None = None,
                    max_chunks: int = 50, sentence_aware: bool = False) -> list[tuple[str, int, int]]:
    """Layout-aware chunk one page's text.

    Returns (text, char_start, char_end) spans whose offsets index the
    normalized page string (blocks joined by single spaces).
    """
    if not text or not text.strip():
        return []
    blocks = split_blocks(text)
    if not blocks:
        return []

    # Offset of each block in the normalized page string ("a b c" join).
    starts: list[int] = []
    pos = 0
    for i, b in enumerate(blocks):
        starts.append(pos)
        pos += len(b) + (1 if i < len(blocks) - 1 else 0)  # +1 for join space
    ends = [starts[i] + len(blocks[i]) for i in range(len(blocks))]
    page_len = pos

    if page_len <= target:
        return [(" ".join(blocks), 0, page_len)]

    out: list[tuple[str, int, int]] = []
    cur: list[int] = []   # block indices in the current chunk
    cur_len = 0

    def emit() -> None:
        if not cur:
            return
        seg = " ".join(blocks[j] for j in cur)
        out.append((seg, starts[cur[0]], ends[cur[-1]]))

    i = 0
    while i < len(blocks) and len(out) < max_chunks:
        blen = len(blocks[i])

        # Oversized single block → flush, then window it.
        if blen > target:
            emit()
            cur, cur_len = [], 0
            for seg in _window(blocks[i], starts[i], target, overlap, protect_span_fn, sentence_aware):
                if len(out) >= max_chunks:
                    break
                out.append(seg)
            i += 1
            continue

        add = blen + (1 if cur else 0)
        if cur and cur_len + add > target:
            # Close the chunk; seed the next with paragraph-level overlap only
            # if the boundary block is small enough AND still leaves room for b.
            emit()
            prev_last = cur[-1]
            if (overlap > 0 and len(blocks[prev_last]) <= overlap
                    and len(blocks[prev_last]) + 1 + blen <= target):
                cur, cur_len = [prev_last], len(blocks[prev_last])
            else:
                cur, cur_len = [], 0
            cur.append(i)
            cur_len += blen + (1 if len(cur) > 1 else 0)
            i += 1
            continue

        cur.append(i)
        cur_len += add
        i += 1

    if len(out) < max_chunks:
        emit()
    return out


# ---- Phase 4 · block-aware chunking over the Document Model (IR) ------------
def chunk_blocks(blocks, target: int, overlap: int, *, max_chunks: int = 400):
    """Chunk one page's IR blocks. `blocks` is an ordered iterable of objects with
    `.kind` (str: heading/paragraph/list_item/key_value/table/figure), `.render()`
    (the block's text) and, for tables, `.rows`/`.has_header`.

    Returns (text, char_start, char_end, bbox, block_indices) spans; offsets index
    the page's serialised string (blocks joined by a blank line) — same char contract
    as chunk_page_text plus a forward `bbox` (union of the composing blocks' boxes,
    None when none carry one) and `block_indices` (tuple of int — document-level block
    indices from the ``_doc_idx`` attribute set by the caller). Guarantees:
    - a key_value / heading block is NEVER split (stays one atomic unit);
    - a table larger than target is split on ROW boundaries with its header row
      REPEATED on each piece (offsets point at the body-row span);
    - other oversized blocks fall back to the sentence/window splitter.
    """
    rendered: list[tuple[str, object]] = []
    for b in blocks:
        txt = (b.render() or "").strip()
        if txt:
            rendered.append((txt, b))
    if not rendered:
        return []

    # Offset of each block in the "\n\n".join(rendered) page string.
    starts: list[int] = []
    pos = 0
    for i, (txt, _) in enumerate(rendered):
        starts.append(pos)
        pos += len(txt) + (2 if i < len(rendered) - 1 else 0)  # "\n\n" join

    out: list[tuple[str, int, int, dict | None, tuple[int, ...]]] = []
    cur: list[int] = []
    cur_len = 0

    def emit() -> None:
        if cur:
            seg = "\n\n".join(rendered[j][0] for j in cur)
            bbox = _union_bbox([rendered[j][1] for j in cur])
            # Read the caller-set _doc_idx from each composing block so chunk_document
            # can map chunks back to document-level block IDs (b_0000, b_0001, …).
            block_indices = tuple(
                getattr(rendered[j][1], "_doc_idx", j) for j in cur
            )
            out.append((seg, starts[cur[0]], starts[cur[-1]] + len(rendered[cur[-1]][0]), bbox, block_indices))

    i = 0
    n = len(rendered)
    while i < n and len(out) < max_chunks:
        txt, blk = rendered[i]
        blen = len(txt)
        kind = getattr(blk, "kind", "paragraph")
        kind = getattr(kind, "value", kind)  # enum → str
        bbox = _union_bbox([blk])

        if blen > target and kind == "table":
            emit()
            cur, cur_len = [], 0
            doc_idx = getattr(blk, "_doc_idx", i)
            for seg, s, e in _window_table(blk, txt, starts[i], target, overlap):
                if len(out) >= max_chunks:
                    break
                out.append((seg, s, e, bbox, (doc_idx,)))
            i += 1
            continue
        # Atomic blocks (key_value, heading) and oversized prose are handled without
        # ever splitting a key_value/heading: only prose falls to the char windower.
        if blen > target and kind in ("paragraph", "list_item"):
            emit()
            cur, cur_len = [], 0
            doc_idx = getattr(blk, "_doc_idx", i)
            for seg, s, e in _window(txt, starts[i], target, overlap, None):
                if len(out) >= max_chunks:
                    break
                out.append((seg, s, e, bbox, (doc_idx,)))
            i += 1
            continue

        add = blen + (2 if cur else 0)
        if cur and cur_len + add > target:
            emit()
            cur, cur_len = [], 0
        cur.append(i)
        cur_len += blen + (2 if len(cur) > 1 else 0)
        i += 1

    if len(out) < max_chunks:
        emit()
    return out


def _union_bbox(blks):
    """Union the composing blocks' bboxes into one {page,x0,y0,x1,y1} dict (the
    _bboxes_for_chunks shape). None when no block carries a box (e.g. vision blocks)."""
    boxes = [b.bbox for b in blks if getattr(b, "bbox", None) is not None]
    if not boxes:
        return None
    return {
        "page": boxes[0].page,
        "x0": min(bx.x0 for bx in boxes),
        "y0": min(bx.y0 for bx in boxes),
        "x1": max(bx.x1 for bx in boxes),
        "y1": max(bx.y1 for bx in boxes),
        "page_w": boxes[0].page_w,
        "page_h": boxes[0].page_h,
    }


def _row_md(cells, width: int) -> str:
    padded = (list(cells) + [""] * width)[:width]
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in padded) + " |"


def _window_table(blk, table_text: str, base: int, target: int, overlap: int):
    """Split an oversized table by ROWS, repeating the header on each piece. The
    emitted text carries the header for retrieval; the (start,end) offsets span the
    whole table block in the page string (the repeated header isn't a substring, and
    per-row page offsets aren't recoverable from rendered markdown)."""
    rows = getattr(blk, "rows", None) or []
    has_header = getattr(blk, "has_header", True)
    if not rows:
        return list(_window(table_text, base, target, overlap, None))
    width = max((len(r) for r in rows), default=1)
    body = rows[1:] if has_header else rows
    header_md = ""
    if has_header:
        header_md = _row_md(rows[0], width) + "\n| " + " | ".join(["---"] * width) + " |\n"

    span = (base, base + len(table_text))
    out: list[tuple[str, int, int]] = []
    cur: list = []
    cur_len = len(header_md)
    for r in body:
        rmd = _row_md(r, width)
        if cur and cur_len + len(rmd) + 1 > target:
            out.append((header_md + "\n".join(_row_md(rr, width) for rr in cur), *span))
            cur, cur_len = [], len(header_md)
        cur.append(r)
        cur_len += len(rmd) + 1
    if cur:
        out.append((header_md + "\n".join(_row_md(rr, width) for rr in cur), *span))
    return out


# ---- R6 · ingestion hardening: NFKC normalize + near-duplicate dedup -------
def normalize_text(text: str) -> str:
    """NFKC unicode normalization + strip control chars (keep \\n, \\t).

    Folds full-width/compatibility forms, ligatures, exotic spaces etc. into
    canonical equivalents so retrieval + extraction see consistent text
    (a full-width '１２３' and ASCII '123' should not be different tokens).
    Semantic-preserving and deterministic — safe to always apply."""
    if not text:
        return text
    t = unicodedata.normalize("NFKC", text)
    return "".join(
        ch for ch in t
        if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C"
    )


def _shingles(text: str, n: int = 3) -> frozenset:
    toks = _TOK.findall((text or "").lower())
    if len(toks) < n:
        return frozenset([" ".join(toks)]) if toks else frozenset()
    return frozenset(" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1))


def dedup_indices(texts: list[str], threshold: float = 0.9) -> list[int]:
    """Indices to KEEP after dropping near-duplicate chunks (repeated boilerplate
    — headers/footers/disclaimers that recur across pages and otherwise dominate
    retrieval). Keeps the first occurrence; a later chunk is dropped when its
    token-shingle Jaccard vs a kept chunk >= `threshold`.

    Conservative (default 0.9 = near-identical): near-dups carry no new
    retrievable signal, so dropping them is safe. (At per-document scale exact
    Jaccard is fine; MinHash-LSH is the 10M-corpus scale-out variant — a non-goal.)"""
    kept: list[int] = []
    kept_sh: list[frozenset] = []
    for i, t in enumerate(texts):
        sh = _shingles(t)
        dup = False
        for ks in kept_sh:
            if not sh and not ks:
                dup = True
                break
            if sh and ks:
                union = len(sh | ks)
                if union and len(sh & ks) / union >= threshold:
                    dup = True
                    break
        if not dup:
            kept.append(i)
            kept_sh.append(sh)
    return kept
