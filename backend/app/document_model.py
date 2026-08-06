"""Document Model — the structured intermediate representation (IR) for the
universal parsing architecture.

Every format parser (PDF, scanned PDF, DOCX, XLSX, PPTX, CSV, image, EML, …)
normalises into ONE ordered list of typed **blocks** with provenance, instead
of each format inventing its own lossy flat `(page_number, text)` linearisation.
Chunking, embeddings, extraction, bbox and PII then all consume the same faithful
model. See `docs/UNIVERSAL_PARSING_ARCHITECTURE.md` for the full design + rollout.

PHASE 0 (this module): the dataclasses + a serialiser that reproduces today's flat
`[(page, text)]` contract byte-for-byte (the parity gate). Nothing consumes the IR
yet — `Document.from_flat_pages(pages).to_pages() == pages` for any input. Richer,
structure-preserving construction (headings / key-value / tables / figures) lands
per-format in later phases, gated on the retrieval + extraction eval harness.

Pure-stdlib (no fitz / sqlalchemy / pydantic) so it unit-tests offline, matching
`chunking.py`. Two constraints the IR MUST preserve (baked into the existing
pipeline): the serialiser still yields text whose offsets feed `char_start`/
`char_end` (late chunking), and `BBox.to_field_bbox()` emits the exact
`{x0,y0,x1,y1,page_w,page_h,page}` shape `FieldOverlay` renders.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    KEY_VALUE = "key_value"   # label ↔ value bound (forms / IDs)
    TABLE = "table"
    FIGURE = "figure"


# Provenance: which parse path produced a block. Feeds the Document Trust Score
# and lets a low-confidence region be re-parsed or flagged for review.
SOURCE_NATIVE = "native"   # embedded text layer / native format structure
SOURCE_OCR = "ocr"         # word-level OCR (Tesseract / RapidOCR)
SOURCE_VISION = "vision"   # general vision-LLM cascade (Gemini / Qwen-VL / Claude)
SOURCE_PADDLE = "paddle"   # specialised doc-parser VLM (PaddleOCR-VL), if adopted


@dataclass
class BBox:
    """Axis-aligned box in the page's own pixel/point space. Serialises to the
    exact dict shape `FieldOverlay` already renders — do not rename keys."""
    x0: float
    y0: float
    x1: float
    y1: float
    page_w: float
    page_h: float
    page: int = 1

    def to_field_bbox(self) -> dict:
        return {
            "x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1,
            "page_w": self.page_w, "page_h": self.page_h, "page": self.page,
        }


def _render_md_table(rows: list[list[str]], *, header: bool = True) -> str:
    """GitHub-flavoured Markdown table from cleaned rows. Pads/trims to a uniform
    width; escapes pipes. A synthetic header row is emitted when `header` is False
    so downstream Markdown stays valid. (Kept local so this module has no imports;
    ingestion's `_render_md_table` is the same shape and the two converge later.)"""
    rows = [[("" if c is None else str(c)) for c in r] for r in rows if any(
        (c not in (None, "")) for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def _row(cells: list[str]) -> str:
        padded = (list(cells) + [""] * width)[:width]
        return "| " + " | ".join(c.replace("|", "\\|") for c in padded) + " |"

    if header:
        head, *body = rows
    else:
        head, body = [""] * width, rows
    lines = [_row(head), "| " + " | ".join(["---"] * width) + " |"]
    lines += [_row(r) for r in body]
    return "\n".join(lines)


@dataclass
class Block:
    """One typed unit of a document. Only the fields relevant to `kind` are set;
    the rest keep their empty defaults. `render()` is the single place that turns
    a block into retrieval/extraction text, so serialisation stays consistent
    across every format and every downstream stage."""
    kind: BlockKind
    page: int = 1
    source: str = SOURCE_NATIVE
    confidence: float = 1.0
    bbox: BBox | None = None

    # text-bearing kinds (heading / paragraph / list_item)
    text: str = ""
    level: int = 0                       # heading depth (1 = top)

    # key_value
    label: str = ""
    value: str = ""

    # table
    rows: list[list[str]] = field(default_factory=list)
    has_header: bool = True

    # figure
    caption: str = ""
    description: str = ""                 # VLM description of the image
    ocr_text: str = ""                    # OCR of text inside the image

    def render(self) -> str:
        """Serialise this block to text. Empty string = contributes nothing to
        the page (dropped by the joiner). This defines how each modality reads
        downstream — a key_value stays `label: value` so extraction can't
        misattribute it (the NRIC 'Race: INDIAN' class)."""
        k = self.kind
        if k in (BlockKind.PARAGRAPH, BlockKind.HEADING, BlockKind.LIST_ITEM):
            return self.text or ""
        if k == BlockKind.KEY_VALUE:
            label = (self.label or "").strip()
            value = (self.value or "").strip()
            if label and value:
                return f"{label}: {value}"
            return value or label
        if k == BlockKind.TABLE:
            return _render_md_table(self.rows, header=self.has_header)
        if k == BlockKind.FIGURE:
            parts = [p for p in (self.caption, self.description, self.ocr_text) if p and p.strip()]
            return ("[Figure] " + " ".join(parts)) if parts else ""
        return self.text or ""


@dataclass
class Document:
    """An ordered list of blocks spanning one uploaded document. `to_pages()` is
    the serialiser boundary the rest of the pipeline consumes."""
    blocks: list[Block] = field(default_factory=list)

    # ── serialise → the legacy flat contract ──────────────────────────────
    def to_pages(self) -> list[tuple[int, str]]:
        """Render to `[(page_number, text)]` — the exact shape every parser
        returns today, so the IR drops in behind the existing contract. Blocks on
        a page are joined by a blank line (the same boundary `chunking.split_blocks`
        cuts on, so each IR block becomes a chunker block). Pages with only
        empty-rendering blocks are preserved as `(page, "")` so the page index
        stays aligned (parsers keep empty pages on purpose)."""
        buckets: OrderedDict[int, list[str]] = OrderedDict()
        for b in self.blocks:
            buckets.setdefault(b.page, [])
            r = b.render()
            if r:
                buckets[b.page].append(r)
        return [(page, "\n\n".join(parts)) for page, parts in sorted(buckets.items())]

    def field_bboxes(self) -> dict[str, dict]:
        """The `{label: bbox_dict}` map for key_value blocks that carry a box —
        forward provenance straight from parse (Phase 5 wires this in). Empty for
        now on the flat path."""
        out: dict[str, dict] = {}
        for b in self.blocks:
            if b.kind == BlockKind.KEY_VALUE and b.label and b.bbox is not None:
                out[b.label] = b.bbox.to_field_bbox()
        return out

    # ── construct ← the legacy flat contract (parity gate) ────────────────
    @classmethod
    def from_flat_pages(cls, pages: list[tuple[int, str]], *, source: str = SOURCE_NATIVE) -> "Document":
        """Wrap today's `[(page, text)]` as one paragraph block per page, verbatim.
        Guarantees `from_flat_pages(p).to_pages() == p` for any `p` — the Phase-0
        parity gate. A page whose text contains blank lines stays a single block
        here (exact round-trip); real structure-aware splitting is a later phase."""
        return cls(blocks=[
            Block(kind=BlockKind.PARAGRAPH, page=page, text=text or "", source=source)
            for page, text in pages
        ])
