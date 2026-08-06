/**
 * Field ↔ Block spatial linking for three-pane sync.
 *
 * fieldBboxes shape (from extractedFields.field_bboxes):
 *   { fieldName: { page, x0, y0, x1, y1, page_w, page_h } }
 *   Coords are absolute PyMuPDF points; origin top-left.
 *
 * blockMap shape (from GET /markdown/full):
 *   { blockId: { kind, page, x0_pct, y0_pct, x1_pct, y1_pct, page_w, page_h, text } }
 *   Coords are 0..1 percentages of page_w / page_h; origin top-left.
 */

import { useEffect, useState } from "react";
import { exportFullMarkdown } from "../api";

// ---------------------------------------------------------------------------
// Spatial overlap
// ---------------------------------------------------------------------------

/**
 * Normalise a field bbox entry to percentage coords.
 * Field bboxes use absolute PDF coords (x0, y0, x1, y1, page_w, page_h).
 */
function _fieldToPct(bb) {
  const pw = bb.page_w || 1;
  const ph = bb.page_h || 1;
  return {
    page: bb.page || 1,
    x0_pct: (bb.x0 || 0) / pw,
    y0_pct: (bb.y0 || 0) / ph,
    x1_pct: (bb.x1 || 0) / pw,
    y1_pct: (bb.y1 || 0) / ph,
  };
}

/**
 * Intersection-over-minimum: area(intersection) / min(area1, area2).
 * Returns 0..1. Requires same page; returns 0 otherwise.
 */
export function rectsOverlap(r1, r2, threshold = 0.05) {
  if ((r1.page || 1) !== (r2.page || 1)) return false;

  const x0 = Math.max(r1.x0_pct, r2.x0_pct);
  const y0 = Math.max(r1.y0_pct, r2.y0_pct);
  const x1 = Math.min(r1.x1_pct, r2.x1_pct);
  const y1 = Math.min(r1.y1_pct, r2.y1_pct);

  if (x0 >= x1 || y0 >= y1) return false;

  const ia = (x1 - x0) * (y1 - y0);
  const a1 = Math.max(0.0001, (r1.x1_pct - r1.x0_pct) * (r1.y1_pct - r1.y0_pct));
  const a2 = Math.max(0.0001, (r2.x1_pct - r2.x0_pct) * (r2.y1_pct - r2.y0_pct));
  const minA = Math.min(a1, a2);

  return ia / minA >= threshold;
}

/**
 * Build bidirectional field ↔ block spatial overlap maps.
 *
 * @param {object} fieldBboxes - extractedFields.field_bboxes dict
 * @param {object} blockMap    - block_map dict from API
 * @param {number} overlapThreshold - min overlap ratio (default 0.05)
 * @returns {{ fieldBlockMap: Record<string,string[]>, blockFieldMap: Record<string,string[]> }}
 */
export function computeFieldBlockMap(fieldBboxes, blockMap, overlapThreshold = 0.05) {
  const fieldBlockMap = {};  // fieldName → [blockId, ...]
  const blockFieldMap = {};  // blockId  → [fieldName, ...]

  if (!fieldBboxes || !blockMap) return { fieldBlockMap, blockFieldMap };

  // Normalise field bboxes once
  const normFields = {};
  for (const [fname, bb] of Object.entries(fieldBboxes)) {
    if (!bb || typeof bb !== "object") continue;
    normFields[fname] = _fieldToPct(bb);
  }

  for (const [fname, fbb] of Object.entries(normFields)) {
    if (!fieldBlockMap[fname]) fieldBlockMap[fname] = [];
    for (const [bid, binfo] of Object.entries(blockMap)) {
      if (!binfo || typeof binfo !== "object") continue;
      const blockPct = {
        page: binfo.page || 1,
        x0_pct: binfo.x0_pct ?? 0,
        y0_pct: binfo.y0_pct ?? 0,
        x1_pct: binfo.x1_pct ?? 0,
        y1_pct: binfo.y1_pct ?? 0,
      };
      if (rectsOverlap(fbb, blockPct, overlapThreshold)) {
        fieldBlockMap[fname].push(bid);
        if (!blockFieldMap[bid]) blockFieldMap[bid] = [];
        blockFieldMap[bid].push(fname);
      }
    }
  }

  return { fieldBlockMap, blockFieldMap };
}

/**
 * Find block IDs whose bboxes overlap the given citation bbox (any format).
 */
export function findMatchingBlockIds(citationBbox, blockMap) {
  if (!citationBbox || !blockMap) return [];

  // Normalise citation bbox to percentage
  let pct;
  if (citationBbox.x0_pct !== undefined || citationBbox.y0_pct !== undefined) {
    // Already percentage-based (from MarkdownTab blockMap)
    pct = {
      page: citationBbox.page || 1,
      x0_pct: citationBbox.x0_pct ?? 0,
      y0_pct: citationBbox.y0_pct ?? 0,
      x1_pct: citationBbox.x1_pct ?? 0,
      y1_pct: citationBbox.y1_pct ?? 0,
    };
  } else if (Array.isArray(citationBbox)) {
    // Normalised array [x0, y0, x1, y1]
    pct = {
      page: citationBbox.page || 1,
      x0_pct: citationBbox[0] ?? 0,
      y0_pct: citationBbox[1] ?? 0,
      x1_pct: citationBbox[2] ?? 0,
      y1_pct: citationBbox[3] ?? 0,
    };
  } else if (citationBbox.x0 !== undefined) {
    // Absolute coords (from field_bboxes)
    const pw = citationBbox.page_w || 1;
    const ph = citationBbox.page_h || 1;
    pct = {
      page: citationBbox.page || 1,
      x0_pct: (citationBbox.x0 || 0) / pw,
      y0_pct: (citationBbox.y0 || 0) / ph,
      x1_pct: (citationBbox.x1 || 0) / pw,
      y1_pct: (citationBbox.y1 || 0) / ph,
    };
  } else {
    return [];
  }

  const results = [];
  for (const [bid, binfo] of Object.entries(blockMap)) {
    if (!binfo || typeof binfo !== "object") continue;
    if (rectsOverlap(pct, {
      page: binfo.page || 1,
      x0_pct: binfo.x0_pct ?? 0,
      y0_pct: binfo.y0_pct ?? 0,
      x1_pct: binfo.x1_pct ?? 0,
      y1_pct: binfo.y1_pct ?? 0,
    })) {
      results.push(bid);
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// useBlockMap hook — fetches blockMap once per doc, module-level cache
// ---------------------------------------------------------------------------

const _BLOCK_MAP_CACHE = new Map();

export function useBlockMap(docId) {
  const [blockMap, setBlockMap] = useState(() => _BLOCK_MAP_CACHE.get(docId) ?? null);
  const [loading, setLoading] = useState(() => !_BLOCK_MAP_CACHE.has(docId));

  useEffect(() => {
    if (!docId) return;
    if (_BLOCK_MAP_CACHE.has(docId)) {
      setBlockMap(_BLOCK_MAP_CACHE.get(docId));
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    exportFullMarkdown(docId)
      .then((data) => {
        if (cancelled) return;
        const bm = data?.blockMap ?? null;
        _BLOCK_MAP_CACHE.set(docId, bm);
        setBlockMap(bm);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [docId]);

  return { blockMap, loading };
}

/**
 * Clear cached blockMap (e.g. after markdown save/reprocess).
 */
export function clearBlockMapCache(docId) {
  _BLOCK_MAP_CACHE.delete(docId);
}

// ---------------------------------------------------------------------------
// Table cell bbox computation (Phase 2)
// ---------------------------------------------------------------------------

/**
 * Compute per-cell bboxes for a GFM table by partitioning the parent table
 * block's bbox evenly by rows × columns.
 *
 * @param {string} blockId - parent table block ID (e.g. "b_0042")
 * @param {object} tableBlock - blockMap entry for the table
 * @param {string} tableText - the rendered markdown table text
 * @returns {Record<string, object>} cell block ID → {x0_pct, y0_pct, ...} entries
 */
export function computeTableCellBboxes(blockId, tableBlock, tableText) {
  const { x0_pct, y0_pct, x1_pct, y1_pct, page, page_w, page_h } = tableBlock;
  if (x0_pct === undefined || y0_pct === undefined) return {};

  const lines = (tableText || "").split("\n").filter((l) => l.trim().startsWith("|"));
  if (lines.length < 2) return {};

  // First line = header, second = separator — count data rows including header
  const dataLines = lines.filter((l, i) => !(i === 1 && l.includes("---")));
  const rowCount = dataLines.length;
  if (rowCount === 0) return {};

  const firstRow = dataLines[0];
  const colCount = firstRow.split("|").filter((c) => c.trim()).length;
  if (colCount === 0) return {};

  const cellW = (x1_pct - x0_pct) / colCount;
  const cellH = (y1_pct - y0_pct) / rowCount;

  const cells = {};
  for (let r = 0; r < rowCount; r++) {
    const cellsInRow = (dataLines[r] || "").split("|").filter((c) => c.trim());
    for (let c = 0; c < Math.min(cellsInRow.length, colCount); c++) {
      cells[`${blockId}_r${r}_c${c}`] = {
        kind: "table_cell",
        page: page || 1,
        x0_pct: x0_pct + c * cellW,
        y0_pct: y0_pct + r * cellH,
        x1_pct: x0_pct + (c + 1) * cellW,
        y1_pct: y0_pct + (r + 1) * cellH,
        page_w: page_w || 1,
        page_h: page_h || 1,
        text: cellsInRow[c]?.trim() || "",
      };
    }
  }
  return cells;
}
