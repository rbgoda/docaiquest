// MarkdownTab — unified document Markdown with 3 views:
// 1. Rendered — clean markdown (default)
// 2. Blocks   — card-style blocks with block IDs + PDF sync + per-block inline edit
// 3. Edit     — full plain textarea preserving <!-- block:id --> markers
// Save & Reprocess triggers re-chunk + re-embed + re-extract on the backend.
import MiniMarkdown from "./MiniMarkdown.jsx";
import EditHistory from "./EditHistory.jsx";
import { exportFullMarkdown, fetchEditHistory, resetMarkdownOverride, saveMarkdownOverride, translateDocument, fetchDocumentTranslations, downloadDocumentExport } from "../../api";
import ErrorBoundary from "../../views/ErrorBoundary.jsx";
import { ErrorState, LoadingState } from "../Shell.jsx";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { computeTableCellBboxes } from "../../lib/fieldBlockLink.js";

// Per-doc in-memory cache so a tab re-mount doesn't re-fetch.
const MARKDOWN_CACHE = new Map();

export function invalidateMarkdownCache(docId) {
  if (docId) MARKDOWN_CACHE.delete(docId);
}

// Supported target languages for translation
const LANGUAGES = {
  fr: "French", de: "German", es: "Spanish", it: "Italian",
  pt: "Portuguese", nl: "Dutch", zh: "Chinese (Simplified)",
  ja: "Japanese", ko: "Korean", ar: "Arabic", ru: "Russian",
  pl: "Polish", tr: "Turkish", vi: "Vietnamese", th: "Thai",
  hi: "Hindi",
};

// Parse annotated markdown (with <!-- block:id --> markers) into segments.
// Each segment is {blockId: string|null, md: string}.
function _parseAnnotatedMarkdown(body) {
  const parts = body.split(/(<!-- block:b_\w+ -->)/);
  const segments = [];
  let currentBlockId = null;
  for (const part of parts) {
    const m = part.match(/<!-- block:(b_\w+) -->/);
    if (m) {
      // Flush a pending block that had no text (e.g. empty table cells)
      // so the table grouper sees every cell and preserves the grid shape.
      if (currentBlockId) {
        segments.push({ blockId: currentBlockId, md: "" });
      }
      currentBlockId = m[1];
    } else if (part.trim()) {
      segments.push({ blockId: currentBlockId, md: part });
      currentBlockId = null;
    }
  }
  return segments;
}

// Rebuild full body from segments array.
function _segmentsToBody(segments) {
  return segments.map(s => (s.blockId ? `<!-- block:${s.blockId} -->` : "") + s.md).join("");
}

// Strip block markers to get clean markdown for the Rendered view.
function _stripBlockMarkers(body) {
  return (body || "").replace(/<!-- block:b_\w+ -->/g, "");
}

// Diff two annotated markdown bodies to find which block IDs changed.
// Returns a Set of blockId strings.
function _diffEditedBlocks(oldMd, newMd) {
  const oldSegs = _parseAnnotatedMarkdown(oldMd);
  const newSegs = _parseAnnotatedMarkdown(newMd);
  const oldMap = {};
  for (const s of oldSegs) { if (s.blockId) oldMap[s.blockId] = s.md; }
  const newMap = {};
  for (const s of newSegs) { if (s.blockId) newMap[s.blockId] = s.md; }
  const edited = new Set();
  for (const [bid, text] of Object.entries(newMap)) {
    if (!(bid in oldMap) || oldMap[bid] !== text) edited.add(bid);
  }
  return edited;
}

// ── GFM table parsing (used by hybrid mode in Blocks view) ──────────────

/** Split a GFM pipe-table row into trimmed cell strings. Same logic as MiniMarkdown's splitTableRow. */
function _splitTableRow(line) {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(s => s.trim());
}

/** Parse body markdown to find all GFM pipe tables, each annotated with its
 *  page number from `\n---\n\n## Page N` markers.  Detection logic mirrors
 *  MiniMarkdown: a '|' line followed by a separator row (|:---:|). */
function _parseGfmTables(body) {
  if (!body) return [];
  const tables = [];
  const lines = body.split("\n");
  let currentPage = 1;
  let i = 0;
  while (i < lines.length) {
    const pm = lines[i].match(/^## Page (\d+)/);
    if (pm) { currentPage = parseInt(pm[1], 10); i++; continue; }
    if (/\|/.test(lines[i]) && i + 1 < lines.length &&
        /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const startIdx = i;
      const header = _splitTableRow(lines[i]);
      i += 2; // skip header + separator
      const rows = [];
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") {
        rows.push(_splitTableRow(lines[i]));
        i++;
      }
      tables.push({
        page: currentPage,
        tableText: lines.slice(startIdx, i).join("\n"),
        header,
        rows,
      });
      continue;
    }
    i++;
  }
  return tables;
}

/** Pre-scan annotatedBody cell segments to determine the sequential order of
 *  table blocks per page.  Returns {parentId: {page, orderIndex}} dict. */
function _computeTablePageOrder(annotatedBody, blockMap) {
  const CELL_RX = /_r\d+_c\d+$/;
  const segs = _parseAnnotatedMarkdown(annotatedBody || "");
  const order = {};
  const counter = {};
  for (const seg of segs) {
    if (seg.blockId && CELL_RX.test(seg.blockId)) {
      const parentId = seg.blockId.replace(CELL_RX, "");
      if (order[parentId]) continue;
      const page = blockMap[parentId]?.page || 1;
      if (!counter[page]) counter[page] = 0;
      order[parentId] = { page, orderIndex: counter[page]++ };
    }
  }
  return order;
}

/** Match a blockMap table entry to the corresponding GFM table from body.
 *  Strategy 1: normalized text comparison (strip whitespace + separator rows).
 *  Strategy 2: position-based fallback (Nth table on the same page). */
function _matchGfmTable(gfmTables, blockMap, parentId, pageNumber, pageTableIndex) {
  const blockText = blockMap[parentId]?.text;
  const onPage = gfmTables.filter(t => t.page === pageNumber);
  if (blockText) {
    const normalize = (t) =>
      t.split("\n")
       .map(l => l.replace(/\s+/g, " ").trim())
       .filter(l => l.startsWith("|") && !/^\|[\s:\-]+\|$/.test(l))
       .join("\n");
    const normBlock = normalize(blockText);
    for (const t of onPage) {
      if (normBlock === normalize(t.tableText)) return t;
    }
  }
  if (pageTableIndex >= 0 && pageTableIndex < onPage.length) {
    return onPage[pageTableIndex];
  }
  return null;
}

// Helper: read from cache — handles {body, annotatedBody, blockMap, edited, rendered} objects and legacy strings.
function _cacheGet(docId) {
  const c = MARKDOWN_CACHE.get(docId);
  if (!c) return null;
  if (typeof c === "string") return { body: c, annotatedBody: null, blockMap: null, edited: false, rendered: null };
  return { body: c.body || null, annotatedBody: c.annotatedBody || null, blockMap: c.blockMap || null, edited: !!c.edited, rendered: c.rendered || null };
}

function MarkdownTab({ docId, doc, revealed, onCite, activeBlockIds = [], blockFieldMap = {} }) {
  const init = _cacheGet(docId);
  const [body, setBody] = useState(init?.body ?? null);                         // formatted (Rendered view)
  const [annotatedBody, setAnnotatedBody] = useState(init?.annotatedBody ?? null); // with markers (Blocks/Edit)
  const [blockMap, setBlockMap] = useState(init?.blockMap ?? null);
  const [edited, setEdited] = useState(init?.edited ?? false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // "rendered" | "blocks" | "edit" — default to blocks so cards + edit are visible
  const [viewMode, setViewMode] = useState("blocks");
  const [draft, setDraft] = useState("");          // full-body draft for Edit view
  const [saving, setSaving] = useState(false);
  const [reprocessedMsg, setReprocessedMsg] = useState(null);
  // Per-block inline edit state (Blocks view)
  const [editSegIdx, setEditSegIdx] = useState(null); // which segment is being edited
  const [segDraft, setSegDraft] = useState("");        // draft text for that segment
  // Edit history + block highlighting
  const [showHistory, setShowHistory] = useState(false);
  const [editedBlockIds, setEditedBlockIds] = useState(new Set());
  const _loadedHistoryFor = useRef(null); // docId we already fetched history for
  const blocksRef = useRef(null); // scroll container for blocks view
  const translateLangRef = useRef(null); // translate language select

  // ── Translation state (M54) ────────────────────────────────────────────
  const [translations, setTranslations] = useState(null);         // {fr: {translated_at, ...}, ...}
  const [translating, setTranslating] = useState(false);
  const [viewLanguage, setViewLanguage] = useState("original");   // "original" | lang code
  const [translationError, setTranslationError] = useState(null);
  // Enhanced markdown mode — when true, shows vision-rendered markdown (rich GFM).
  // Initialized from cache so the toggle label always matches what's displayed.
  const [enhancedMode, setEnhancedMode] = useState(init?.rendered === "vision");
  const [enhancing, setEnhancing] = useState(false);  // loading vision markdown
  // Raw (chunk-based) body cached separately so switching back from Enhanced is instant.
  const rawBodyRef = useRef(null);       // always chunk-based
  const enhancedBodyRef = useRef(null);  // vision-rendered (fetched on demand)

  // ── scroll first active block into view when activeBlockIds changes ──
  useEffect(() => {
    if (!activeBlockIds.length || !blocksRef.current) return;
    const first = blocksRef.current.querySelector(`.active-block`);
    if (first) {
      first.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [activeBlockIds]);

  // ── pre-compute per-cell bboxes for table-kind blocks ─────────────────
  const cellBboxes = useMemo(() => {
    if (!blockMap) return {};
    const cells = {};
    for (const [bid, info] of Object.entries(blockMap)) {
      if (info?.kind?.toLowerCase() === "table" && info?.text) {
        Object.assign(cells, computeTableCellBboxes(bid, info, info.text));
      }
    }
    return cells;
  }, [blockMap]);

  // ── load edit history to highlight previously edited blocks ─────────
  useEffect(() => {
    if (!edited || !docId || _loadedHistoryFor.current === docId) return;
    _loadedHistoryFor.current = docId;
    fetchEditHistory(docId)
      .then((history) => {
        const ids = new Set();
        for (const h of (history || [])) {
          if (h.fieldPath === "__markdown__" && h.originalValue && h.newValue) {
            const diffed = _diffEditedBlocks(h.originalValue, h.newValue);
            diffed.forEach((id) => ids.add(id));
          }
        }
        if (ids.size > 0) setEditedBlockIds(ids);
      })
      .catch(() => {}); // silently ignore — highlighting is cosmetic
  }, [edited, docId]);

  // ── fetch translations on mount ───────────────────────────────────────
  useEffect(() => {
    if (!docId || edited) return;
    fetchDocumentTranslations(docId)
      .then(r => setTranslations(r.translations || {}))
      .catch(() => {}); // non-critical
  }, [docId, edited]);

  // ── translate handler ─────────────────────────────────────────────────
  const handleTranslate = async (lang) => {
    if (translating || !docId || !lang) return;
    setTranslating(true);
    setTranslationError(null);
    try {
      const r = await translateDocument(docId, lang);
      setViewLanguage(lang);
      // Update cache with translated content
      MARKDOWN_CACHE.set(docId, {
        body: r.body,
        annotatedBody: r.annotatedBody || r.body,
        blockMap: blockMap,
        edited: false,
        _language: lang,
      });
      setBody(r.body);
      setAnnotatedBody(r.annotatedBody || r.body);
      // Refresh translations list
      const t = await fetchDocumentTranslations(docId);
      setTranslations(t.translations || {});
    } catch (e) {
      setTranslationError(e.message || "Translation failed");
      setTimeout(() => setTranslationError(null), 8000);
    } finally {
      setTranslating(false);
    }
  };

  // ── switch language handler ───────────────────────────────────────────
  const handleSwitchLanguage = async (lang) => {
    setViewLanguage(lang);
    setTranslationError(null);
    if (lang === "original") {
      // Restore original from cache or re-fetch
      const cached = _cacheGet(docId);
      if (cached && !cached._language) {
        setBody(cached.body);
        setAnnotatedBody(cached.annotatedBody);
      } else {
        // cache entry itself is translated — force a fresh fetch
        MARKDOWN_CACHE.delete(docId);
        run(true);
      }
      return;
    }
    // For a translated language — trigger translation (which caches internally)
    handleTranslate(lang);
  };

  // ── toggle enhanced (vision-rendered) / raw (chunk-based) markdown ──
  // Caches both versions in refs so toggling back and forth is instant.
  // enhancedMode is correctly initialised from cache → the button always
  // matches what is displayed, so the toggle direction is always right.
  const toggleEnhanced = async () => {
    const next = !enhancedMode;
    setEnhancedMode(next);
    setError(null);

    if (viewLanguage !== "original") {
      setTranslationError(null);
      return;
    }

    if (next) {
      // ── switching TO Enhanced ──────────────────────────────────────────
      if (enhancedBodyRef.current) {
        _setBodyFromRef(enhancedBodyRef.current, "vision");
        return;
      }
      // Vision not cached anywhere — run force=true (slow, up to 5 min)
      setEnhancing(true);
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 300_000);
      try {
        const r = await exportFullMarkdown(docId, { force: true, signal: ac.signal });
        clearTimeout(timer);
        enhancedBodyRef.current = { body: r.body, annotatedBody: r.annotatedBody || null, blockMap: r.blockMap || null, edited: !!r.edited };
        MARKDOWN_CACHE.set(docId, { body: r.body, annotatedBody: r.annotatedBody || null, blockMap: r.blockMap || null, edited: !!r.edited, rendered: "vision" });
        setBody(r.body); setAnnotatedBody(r.annotatedBody || null); setBlockMap(r.blockMap || null); setEdited(!!r.edited);
      } catch (e) {
        clearTimeout(timer);
        setError(e.name === "AbortError" ? "Vision rendering timed out — the document may be too large. Try again." : (e.message || "Failed to load enhanced markdown"));
        setEnhancedMode(false);
      } finally {
        setEnhancing(false);
      }
    } else {
      // ── switching TO Raw ────────────────────────────────────────────────
      if (rawBodyRef.current) {
        _setBodyFromRef(rawBodyRef.current, null);
        return;
      }
      // Fallback (shouldn't be needed — raw is seeded on mount)
      setEnhancing(true);
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 30_000);
      try {
        const r = await exportFullMarkdown(docId, { raw: true, signal: ac.signal });
        clearTimeout(timer);
        rawBodyRef.current = { body: r.body, annotatedBody: r.annotatedBody || null, blockMap: r.blockMap || null, edited: !!r.edited };
        MARKDOWN_CACHE.set(docId, { body: r.body, annotatedBody: r.annotatedBody || null, blockMap: r.blockMap || null, edited: !!r.edited, rendered: null });
        setBody(r.body); setAnnotatedBody(r.annotatedBody || null); setBlockMap(r.blockMap || null); setEdited(!!r.edited);
      } catch (e) {
        clearTimeout(timer);
        setError(e.name === "AbortError" ? "Raw markdown load timed out — try again" : (e.message || "Failed to load raw markdown"));
        setEnhancedMode(true);
      } finally {
        setEnhancing(false);
      }
    }
  };

  // Helper: set body/annotatedBody/blockMap from a ref
  const _setBodyFromRef = (ref, rendered) => {
    setBody(ref.body); setAnnotatedBody(ref.annotatedBody); setBlockMap(ref.blockMap); setEdited(!!ref.edited);
    MARKDOWN_CACHE.set(docId, { body: ref.body, annotatedBody: ref.annotatedBody, blockMap: ref.blockMap, edited: !!ref.edited, rendered });
  };

  // ── shared save ──────────────────────────────────────────────────────
  const doSave = async (newBody, changedBlockIds) => {
    if (saving || !(newBody || "").trim()) return;
    setSaving(true);
    setReprocessedMsg(null);
    try {
      const r = await saveMarkdownOverride(docId, newBody, { reprocess: true, changedBlockIds });
      setEdited(true);
      // Re-fetch to get updated body + annotatedBody + blockMap
      MARKDOWN_CACHE.delete(docId);
      rawBodyRef.current = null;
      enhancedBodyRef.current = null;
      const fresh = await exportFullMarkdown(docId, { force: true });
      MARKDOWN_CACHE.set(docId, { body: fresh.body, annotatedBody: fresh.annotatedBody || null, blockMap: fresh.blockMap || null, edited: true });
      setBody(fresh.body);
      setAnnotatedBody(fresh.annotatedBody || null);
      setBlockMap(fresh.blockMap || null);
      // The fresh fetch above is vision (force). Seed enhancedBodyRef and
      // fetch raw in background so the toggle is instant later.
      enhancedBodyRef.current = { body: fresh.body, annotatedBody: fresh.annotatedBody, blockMap: fresh.blockMap, edited: true };
      setEnhancedMode(true);
      _fetchRawMarkdown(docId);
      setViewMode("blocks");
      setEditSegIdx(null);
      if (r.reprocessed) {
        setReprocessedMsg(
          `Reprocessed: ${r.chunksUpdated || 0} chunks updated` +
          (r.chunksKept ? `, ${r.chunksKept} kept` : "") +
          (r.fields ? `, ${Object.keys(r.fields).length} fields re-extracted` : "") +
          "."
        );
        setTimeout(() => setReprocessedMsg(null), 6000);
      } else if (r.error) {
        setReprocessedMsg(`Reprocess failed: ${r.error}`);
      }
    } catch (e) {
      console.error("Markdown save failed:", e);
      setReprocessedMsg("Save failed — check console for details");
    } finally { setSaving(false); }
  };

  // ── per-block inline save ────────────────────────────────────────────
  const saveBlockEdit = () => {
    const segments = _parseAnnotatedMarkdown(annotatedBody || body);
    if (editSegIdx == null || editSegIdx >= segments.length) return;
    const changedBlockId = segments[editSegIdx].blockId;
    segments[editSegIdx].md = segDraft;
    // Track edited block for visual highlighting
    if (changedBlockId) {
      setEditedBlockIds((prev) => { const next = new Set(prev); next.add(changedBlockId); return next; });
    }
    doSave(_segmentsToBody(segments), changedBlockId ? [changedBlockId] : null);
  };

  // ── full-text edit save ──────────────────────────────────────────────
  const saveFullEdit = () => doSave(draft);

  // ── reset ────────────────────────────────────────────────────────────
  const resetEdit = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const r = await resetMarkdownOverride(docId);
      MARKDOWN_CACHE.set(docId, { body: r.body, annotatedBody: r.annotatedBody || null, blockMap: r.blockMap || null, edited: false });
      setBody(r.body); setAnnotatedBody(r.annotatedBody || null); setBlockMap(r.blockMap || null); setEdited(false); setViewMode("rendered");
      setEditSegIdx(null);
      // Clear refs so the next toggle fetches fresh data
      rawBodyRef.current = null;
      enhancedBodyRef.current = null;
      setEnhancedMode(false);
      // Seed the raw-body cache in background
      _fetchRawMarkdown(docId);
    } catch { /* ignore */ } finally { setSaving(false); }
  };

  // ── fetch ────────────────────────────────────────────────────────────
  const run = (force = false) => {
    if (!force && MARKDOWN_CACHE.has(docId)) {
      const cached = MARKDOWN_CACHE.get(docId);
      setBody(typeof cached === "string" ? cached : cached?.body);
      setAnnotatedBody(cached?.annotatedBody || null);
      setBlockMap(cached?.blockMap || null);
      setEdited(!!cached?.edited);
      return;
    }
    setError(null);
    setBody(null);
    setLoading(true);
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 60_000);
    exportFullMarkdown(docId, { force, signal: ac.signal })
      .then(r => {
        clearTimeout(timer);
        const isVision = r.rendered === "vision";
        MARKDOWN_CACHE.set(docId, {
          body: r.body, annotatedBody: r.annotatedBody || null,
          blockMap: r.blockMap || null, edited: !!r.edited,
          rendered: isVision ? "vision" : null,
        });
        setBody(r.body);
        setAnnotatedBody(r.annotatedBody || null);
        setBlockMap(r.blockMap || null);
        setEdited(!!r.edited);
        // Keep the toggle label in sync with what the backend returned
        setEnhancedMode(isVision);
        // Seed the ref caches so toggling is instant
        if (isVision) {
          enhancedBodyRef.current = { body: r.body, annotatedBody: r.annotatedBody, blockMap: r.blockMap, edited: !!r.edited };
          _fetchRawMarkdown(docId);  // get raw in background for instant Raw toggle
        } else {
          rawBodyRef.current = { body: r.body, annotatedBody: r.annotatedBody, blockMap: r.blockMap, edited: !!r.edited };
          enhancedBodyRef.current = null;  // not yet fetched
        }
      })
      .catch(e => { clearTimeout(timer); setError(e.name === "AbortError" ? "Markdown load timed out — try again" : e.message); })
      .finally(() => setLoading(false));
  };

  // Silent background fetch of the raw (chunk-based) markdown to populate the
  // rawBodyRef cache — always fast (no LLM, ~deterministic build).
  const _fetchRawMarkdown = (id) => {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 30_000);
    exportFullMarkdown(id, { raw: true, signal: ac.signal })
      .then(r => {
        clearTimeout(timer);
        rawBodyRef.current = { body: r.body, annotatedBody: r.annotatedBody, blockMap: r.blockMap || null };
      })
      .catch(() => { clearTimeout(timer); /* best-effort */ });
  };

  useEffect(() => {
    // Restore from cache on mount or doc change — skip fetch if cached
    const cached = _cacheGet(docId);
    if (cached) {
      const isVision = cached.rendered === "vision";
      setBody(cached.body);
      setAnnotatedBody(cached.annotatedBody);
      setBlockMap(cached.blockMap);
      setEdited(cached.edited);
      setEnhancedMode(isVision);
      setLoading(false);
      setError(null);
      // Seed refs from the cached data so toggling is instant
      if (isVision) {
        enhancedBodyRef.current = { body: cached.body, annotatedBody: cached.annotatedBody, blockMap: cached.blockMap, edited: !!cached.edited };
        _fetchRawMarkdown(docId);
      } else {
        rawBodyRef.current = { body: cached.body, annotatedBody: cached.annotatedBody, blockMap: cached.blockMap, edited: !!cached.edited };
        enhancedBodyRef.current = null;
        // If we know vision exists for this doc (rendered field), fetch it in bg
        _fetchRawMarkdown(docId);
      }
      return;
    }
    run();
    /* eslint-disable-next-line */
  }, [docId, revealed]);

  if (loading) {
    return (
      <div className="p-4">
        <LoadingState label="Loading Markdown…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-4">
        <ErrorState message={error} />
        <button onClick={run} className="btn-gold mt-3" style={{ padding: "6px 14px", borderRadius: 6, fontSize: 12 }}>
          Retry
        </button>
      </div>
    );
  }
  if (body == null && annotatedBody == null) return null;  // need at least one

  const isEdit = viewMode === "edit";
  const isBlocks = viewMode === "blocks";
  // Always show Blocks tab — even without block markers, segmented view is useful
  const hasBlocks = true;

  return (
    <div className="p-4">
      {/* ── Toolbar ──────────────────────────────────────────────────── */}
      <div className="row gap-2 mb-3" style={{ alignItems: "center", flexWrap: "wrap" }}>
        {isEdit ? (
          <>
            <button onClick={() => setViewMode("rendered")}
                    className="border bg2"
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              👁 View
            </button>
            <button onClick={saveFullEdit}
                    disabled={saving}
                    className="btn-gold"
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer", fontWeight: 600 }}>
              {saving ? "Saving…" : "💾 Save & Reprocess"}
            </button>
            <button onClick={() => { setDraft(annotatedBody || body); setViewMode("rendered"); }}
                    className="border bg2"
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              Cancel
            </button>
          </>
        ) : (
          <>
            {hasBlocks && (
              <button onClick={() => { setEditSegIdx(null); setViewMode(isBlocks ? "rendered" : "blocks"); }}
                      className="border bg2"
                      style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
                {isBlocks ? "👁 Rendered" : "📍 Blocks"}
              </button>
            )}
            {/* Enhanced markdown toggle — only in Rendered view */}
            {!isBlocks && (
              <button onClick={toggleEnhanced}
                      disabled={enhancing}
                      title={enhancedMode ? "Vision-rendered markdown with rich formatting" : "Raw chunk-based markdown"}
                      className="border bg2"
                      style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer",
                               ...(enhancedMode ? { borderColor: "var(--blue, #5b9bd5)", color: "var(--blue, #5b9bd5)" } : {}) }}>
                {enhancing ? "⏳ Loading…" : enhancedMode ? "✨ Enhanced" : "📄 Raw"}
              </button>
            )}
            <button onClick={() => { setDraft(annotatedBody || body); setViewMode("edit"); }}
                    title="Edit the full document Markdown"
                    className="border bg2"
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              ✎ Full edit
            </button>
            {edited && (
              <button onClick={resetEdit} disabled={saving}
                      title="Discard edits, restore the parsed text"
                      className="border bg2"
                      style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
                Reset
              </button>
            )}
            <button onClick={() => setShowHistory((s) => !s)}
                    title="Show edit history for this document"
                    className="border bg2"
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer", ...(showHistory ? { borderColor: "var(--gold2)", color: "var(--gold)" } : {}) }}>
              📜 {showHistory ? "Hide history" : "History"}
            </button>
          </>
        )}
        {/* ── Translation controls (M54) ───────────────────────────────── */}
        {!isEdit && (
          <>
            <span style={{ color: "var(--line)", fontSize: 14, userSelect: "none" }}>|</span>
            <select
              value={viewLanguage}
              onChange={(e) => handleSwitchLanguage(e.target.value)}
              className="border bg2"
              style={{ padding: "5px 8px", borderRadius: 4, fontSize: 11, cursor: "pointer", maxWidth: 130 }}
            >
              <option value="original">🌐 Original</option>
              {translations && Object.keys(translations).length > 0 && (
                <optgroup label="Translated">
                  {Object.entries(translations).map(([code, info]) => (
                    <option key={code} value={code}>
                      {LANGUAGES[code] || code.toUpperCase()} ✓
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            <select
              ref={translateLangRef}
              defaultValue=""
              className="border bg2"
              style={{ padding: "5px 8px", borderRadius: 4, fontSize: 11, cursor: "pointer", maxWidth: 130 }}
            >
              <option value="" disabled>Translate to…</option>
              {Object.entries(LANGUAGES).map(([code, label]) => (
                <option key={code} value={code}>{label} ({code.toUpperCase()})</option>
              ))}
            </select>
            <button onClick={() => {
                    const lang = translateLangRef.current?.value;
                    if (lang) handleTranslate(lang);
                  }}
                    disabled={translating}
                    className="border bg2"
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              {translating ? "⏳ Translating…" : "🌐 Translate"}
            </button>
          </>
        )}
        {edited && !isEdit && (
          <span title="Full text was hand-corrected"
                style={{ fontSize: 10, padding: "1px 8px", borderRadius: 999, border: "1px solid var(--gold, #E2BC68)", color: "var(--gold, #E2BC68)" }}>
            edited
          </span>
        )}
        {viewLanguage !== "original" && !isEdit && (
          <span title={`Viewing ${LANGUAGES[viewLanguage] || viewLanguage} translation`}
                style={{ fontSize: 10, padding: "1px 8px", borderRadius: 999, border: "1px solid var(--blue, #5b9bd5)", color: "var(--blue, #5b9bd5)" }}>
            {viewLanguage.toUpperCase()}
          </span>
        )}
        <div style={{ flex: 1 }} />
        {reprocessedMsg && (
          <span className="ink2" style={{ fontSize: 10, fontStyle: "italic" }}>{reprocessedMsg}</span>
        )}
        {translationError && (
          <span style={{ fontSize: 10, fontStyle: "italic", color: "var(--red, #e74c3c)" }}>{translationError}</span>
        )}
        {translating && (
          <span className="ink3" style={{ fontSize: 10, fontStyle: "italic" }}>Translating via AI…</span>
        )}
        {enhancing && (
          <span className="ink3" style={{ fontSize: 10, fontStyle: "italic" }}>Loading enhanced markdown…</span>
        )}
        <button onClick={() => navigator.clipboard.writeText(_stripBlockMarkers(body))}
                className="border bg2"
                style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          📋 Copy
        </button>
        <button onClick={() => {
                  const clean = _stripBlockMarkers(body);
                  const blob = new Blob([clean], { type: "text/markdown" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url; a.download = `${docId}${viewLanguage !== "original" ? `-${viewLanguage}` : ""}.md`;
                  document.body.appendChild(a); a.click(); a.remove();
                  URL.revokeObjectURL(url);
                }}
                className="border bg2"
                style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          ⬇ Download .md
        </button>
        {/* ── Export buttons (M54) ─────────────────────────────────────────── */}
        <span style={{ fontSize: 10, color: "var(--ink3)", userSelect: "none" }}>Export</span>
        <button onClick={() => downloadDocumentExport(docId, "json", `${docId}-fields.json`)}
                className="border bg2"
                style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          JSON
        </button>
        <button onClick={() => downloadDocumentExport(docId, "csv", `${docId}-fields.csv`)}
                className="border bg2"
                style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          CSV
        </button>
      </div>

      {/* ── Edit history (collapsible) ────────────────────────────────── */}
      {showHistory && (
        <div className="mb-3 p-3 border rounded-md" style={{
          background: "var(--bg1)", borderColor: "var(--line)",
          maxHeight: 260, overflow: "auto",
        }}>
          <EditHistory docId={docId} />
        </div>
      )}

      {/* ── Content ──────────────────────────────────────────────────── */}
      {isEdit ? (
        // ── Full edit — raw textarea ──────────────────────────────────
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            className="md-edit-textarea"
            style={{
              width: "100%", minHeight: "calc(100vh - 320px)",
              padding: 14, fontFamily: "var(--mono), monospace", fontSize: 12,
              lineHeight: 1.6, resize: "vertical", outline: "none",
              borderRadius: 6, border: "1px solid var(--line)",
              background: "var(--bg2)", color: "var(--ink)",
            }}
            spellCheck={false}
          />
          <div className="ink3" style={{ fontSize: 10, fontStyle: "italic", padding: "0 4px" }}>
            💡 Edit the Markdown above. Block markers (<code>&lt;!-- block:id --&gt;</code>) are
            preserved so sections stay linked to the PDF. On save, chunks and fields will be
            regenerated from your edits.
          </div>
        </div>
      ) : isBlocks && hasBlocks ? (
        // ── Blocks view — cards with per-block inline edit ────────────
        <div ref={blocksRef} style={{
          maxHeight: "calc(100vh - 280px)", overflow: "auto",
          fontSize: 13, lineHeight: 1.55,
        }}>
          {(() => {
            // Group consecutive table cells by parent block ID so they
            // render as a single HTML <table> card instead of a vertical
            // stack of individual cell cards.
            const rawSegs = _parseAnnotatedMarkdown(annotatedBody || body);
            const CELL_RX = /_r\d+_c\d+$/;
            const groups = [];
            let i = 0;
            while (i < rawSegs.length) {
              const seg = rawSegs[i];
              const isCell = seg.blockId && CELL_RX.test(seg.blockId);
              if (isCell) {
                const parentId = seg.blockId.replace(CELL_RX, "");
                const cells = [];
                while (i < rawSegs.length) {
                  const s = rawSegs[i];
                  if (s.blockId && s.blockId.startsWith(parentId + "_r")) {
                    cells.push(s);
                    i++;
                  } else { break; }
                }
                // Only render as an HTML <table> when there are enough
                // non-empty cells to make a table meaningful.  Single-row
                // fragments with 0–1 values are Docling parser artifacts
                // (reference ranges, stray values) — keep them as cards.
                const nonEmpty = cells.filter(c => (c.md || "").trim()).length;
                const rows = new Set(cells.map(c => { const m = c.blockId?.match(/_r(\d+)_c\d+$/); return m ? m[1] : null; })).size;
                const useTable = rows >= 2 || nonEmpty >= 3;
                if (useTable) {
                  groups.push({ type: "table", parentId, cells });
                } else {
                  // Push cells individually as blocks, preserving original positions
                  for (const c of cells) {
                    groups.push({ type: "block", seg: c, idx: i });
                  }
                }
              } else {
                groups.push({ type: "block", seg, idx: i });
                i++;
              }
            }

            // ── Hybrid mode: parse body's complete GFM tables once ────────
            const gfmTables = (body && annotatedBody) ? _parseGfmTables(body) : [];
            const tablePageOrder = (annotatedBody && blockMap)
              ? _computeTablePageOrder(annotatedBody, blockMap) : {};

            // ── render a single group ──
            let globalIdx = 0;
            return groups.map((g) => {
              if (g.type === "block") {
                // ── Regular block (unchanged) ──────────────────────────
                const seg = g.seg;
                const gi = g.idx;
                const bb = seg.blockId ? (cellBboxes[seg.blockId] || blockMap[seg.blockId]) : null;
                const bbox = bb ? {
                  x0_pct: bb.x0_pct, y0_pct: bb.y0_pct,
                  x1_pct: bb.x1_pct, y1_pct: bb.y1_pct,
                  page: bb.page, page_w: bb.page_w, page_h: bb.page_h,
                } : null;
                const isEditingBlock = editSegIdx === gi;
                const isEdited = seg.blockId && editedBlockIds.has(seg.blockId);
                const isActive = seg.blockId && activeBlockIds.includes(seg.blockId);

                return (
                  <div key={`b-${gi}`}
                       className={`bg2 border p-3${bbox ? " clickable-block" : ""}${isEdited ? " edited-block" : ""}${isActive ? " active-block" : ""}`}
                       style={{
                         borderRadius: 0, borderTop: globalIdx++ === 0 ? undefined : "none",
                         transition: "background 0.12s, border-color 0.12s",
                         ...(isActive ? {
                           borderLeft: "3px solid #7C6FD6",
                           background: "rgba(124,111,214,0.08)",
                         } : isEdited ? {
                           borderLeft: "3px solid #5B9A8B",
                           background: "rgba(91,154,139,0.06)",
                         } : {}),
                       }}>
                    {isEditingBlock ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <textarea value={segDraft}
                          onChange={e => setSegDraft(e.target.value)}
                          className="md-edit-textarea"
                          style={{
                            width: "100%", minHeight: 80, padding: 10,
                            fontFamily: "var(--mono), monospace", fontSize: 12,
                            lineHeight: 1.6, resize: "vertical", outline: "none",
                            borderRadius: 4, border: "1px solid var(--line)",
                            background: "var(--bg1)", color: "var(--ink)",
                          }}
                          spellCheck={false} autoFocus />
                        <div className="row gap-1" style={{ alignItems: "center" }}>
                          <button onClick={saveBlockEdit} disabled={saving}
                            className="btn-gold"
                            style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer", fontWeight: 600 }}>
                            {saving ? "…" : "✓ Save block"}
                          </button>
                          <button onClick={() => setEditSegIdx(null)}
                            className="border bg2"
                            style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
                            ✕ Cancel
                          </button>
                          {saving && <span className="ink3" style={{ fontSize: 10 }}>Reprocessing…</span>}
                        </div>
                      </div>
                    ) : (
                      <>
                        <div onClick={bbox && onCite ? () => onCite({ page: bb.page, bbox, quote: bb.text?.slice(0, 120) || "" }) : undefined}
                             style={{ cursor: bbox ? "pointer" : undefined }}
                             title={bbox ? `Click to locate on page ${bb.page}` : undefined}>
                          <ErrorBoundary>
                            <MiniMarkdown source={seg.md} />
                          </ErrorBoundary>
                        </div>
                        <div className="row between" style={{ marginTop: 6, alignItems: "center" }}>
                          <span className="mono ink3" style={{ fontSize: 9 }}>
                            {bbox ? `${seg.blockId} · p.${bb.page} · ${bb.kind}` : seg.blockId || `segment ${gi}`}
                            {isEdited && <span style={{ color: "#5B9A8B", marginLeft: 6, fontWeight: 600 }}>✎ edited</span>}
                          </span>
                          <div className="row gap-1" style={{ alignItems: "center" }}>
                            {bbox && (
                              <span style={{ fontSize: 9, color: "var(--gold2)", cursor: "pointer" }}
                                    onClick={() => onCite({ page: bb.page, bbox, quote: bb.text?.slice(0, 120) || "" })}
                                    title="Locate on PDF">📍 locate</span>
                            )}
                            <button onClick={(e) => { e.stopPropagation(); setEditSegIdx(gi); setSegDraft(seg.md); }}
                              title="Edit this block's text"
                              style={{
                                padding: "3px 10px", borderRadius: 4, fontSize: 10, cursor: "pointer",
                                background: "var(--bg1)", border: "1px solid var(--gold2)",
                                color: "var(--gold, #b07814)", fontWeight: 500,
                              }}>✎ Edit block</button>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                );
              }

              // ── Table group: render as HTML <table> with clickable cells ──
              const parentBlock = blockMap?.[g.parentId] || cellBboxes[g.cells[0]?.blockId];
              const parentBbox = parentBlock ? {
                x0_pct: parentBlock.x0_pct, y0_pct: parentBlock.y0_pct,
                x1_pct: parentBlock.x1_pct, y1_pct: parentBlock.y1_pct,
                page: parentBlock.page, page_w: parentBlock.page_w, page_h: parentBlock.page_h,
              } : null;

              // ── Hybrid: try to find matching GFM table from body ──
              const tableMeta = tablePageOrder[g.parentId];
              const matchedGfm = (gfmTables.length > 0 && tableMeta)
                ? _matchGfmTable(gfmTables, blockMap, g.parentId, tableMeta.page, tableMeta.orderIndex)
                : null;

              if (matchedGfm) {
                // ── Hybrid: render body's complete GFM table with blockMap bboxes ──
                const numRows = matchedGfm.rows.length;
                const numCols = matchedGfm.header.length;
                const anyActiveHybrid = g.cells.some(c => c.blockId && activeBlockIds.includes(c.blockId));

                return (
                  <div key={`t-${g.parentId}`}
                       className={`bg2 border p-0${anyActiveHybrid ? " active-block" : ""}`}
                       style={{
                         borderRadius: 0, borderTop: globalIdx++ === 0 ? undefined : "none",
                         transition: "background 0.12s, border-color 0.12s",
                         ...(anyActiveHybrid ? {
                           borderLeft: "3px solid #7C6FD6",
                           background: "rgba(124,111,214,0.08)",
                         } : {}),
                       }}>
                    <table style={{
                      width: "100%", borderCollapse: "collapse",
                      fontSize: 11.5, fontFamily: "var(--mono), monospace",
                    }}>
                      <thead>
                        <tr>
                          {matchedGfm.header.map((cellText, ci) => {
                            const cellKey = `${g.parentId}_r0_c${ci}`;
                            const cellBb = cellBboxes[cellKey] || null;
                            const cellActive = activeBlockIds.includes(cellKey);
                            return (
                              <th key={`hh-0-${ci}`}
                                onClick={cellBb && onCite ? () => onCite({
                                  page: cellBb.page,
                                  bbox: { x0_pct: cellBb.x0_pct, y0_pct: cellBb.y0_pct, x1_pct: cellBb.x1_pct, y1_pct: cellBb.y1_pct, page: cellBb.page, page_w: cellBb.page_w, page_h: cellBb.page_h },
                                  blockId: cellKey,
                                  quote: cellText.slice(0, 120),
                                }) : undefined}
                                title={cellBb ? `Click to locate on page ${cellBb.page}` : undefined}
                                style={{
                                  padding: "6px 10px", textAlign: "left",
                                  borderBottom: "2px solid var(--line)",
                                  background: cellActive ? "rgba(124,111,214,0.18)" : "var(--bg1)",
                                  fontWeight: 600, color: "var(--ink)",
                                  cursor: cellBb ? "pointer" : undefined,
                                  borderRight: ci < (numCols - 1) ? "1px solid var(--line)" : undefined,
                                }}>
                                {cellText}
                              </th>
                            );
                          })}
                        </tr>
                      </thead>
                      <tbody>
                        {matchedGfm.rows.map((row, ri) => (
                          <tr key={`hb-${ri}`}>
                            {row.map((cellText, ci) => {
                              const cellKey = `${g.parentId}_r${ri + 1}_c${ci}`;
                              const cellBb = cellBboxes[cellKey] || null;
                              const cellActive = activeBlockIds.includes(cellKey);
                              return (
                                <td key={`hb-${ri}-${ci}`}
                                  onClick={cellBb && onCite ? () => onCite({
                                    page: cellBb.page,
                                    bbox: { x0_pct: cellBb.x0_pct, y0_pct: cellBb.y0_pct, x1_pct: cellBb.x1_pct, y1_pct: cellBb.y1_pct, page: cellBb.page, page_w: cellBb.page_w, page_h: cellBb.page_h },
                                    blockId: cellKey,
                                    quote: cellText.slice(0, 120),
                                  }) : undefined}
                                  title={cellBb ? `Click to locate on page ${cellBb.page}` : undefined}
                                  style={{
                                    padding: "5px 10px", textAlign: "left",
                                    borderBottom: "1px solid var(--line)",
                                    background: cellActive ? "rgba(124,111,214,0.18)" : "transparent",
                                    color: "var(--ink)",
                                    cursor: cellBb ? "pointer" : undefined,
                                    borderRight: ci < (row.length - 1) ? "1px solid var(--line)" : undefined,
                                  }}>
                                  {cellText}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {/* Table footer */}
                    <div style={{
                      padding: "5px 10px", borderTop: "1px solid var(--line)",
                      background: "var(--bg2)", display: "flex", alignItems: "center",
                      justifyContent: "space-between",
                    }}>
                      <span className="mono ink3" style={{ fontSize: 9 }}>
                        {g.parentId} · {numRows + 1}×{numCols} table
                        {parentBlock?.page ? ` · p.${parentBlock.page}` : ""}
                        <span style={{ color: "#5B9A8B", marginLeft: 8, fontWeight: 600 }}>hybrid</span>
                      </span>
                      {parentBbox && onCite && (
                        <span style={{ fontSize: 9, color: "var(--gold2)", cursor: "pointer" }}
                              onClick={() => onCite({
                                page: parentBlock.page,
                                bbox: parentBbox,
                                blockId: g.parentId,
                                quote: `Table ${g.parentId}`,
                              })}
                              title="Locate table on PDF">📍 locate table</span>
                      )}
                    </div>
                  </div>
                );
              }

              // ── Fallback: existing cell-based 2D grid reconstruction ──

              // Build 2D grid [row][col] from cells
              const grid = [];
              let maxCol = 0;
              for (const c of g.cells) {
                const m = c.blockId.match(/_r(\d+)_c(\d+)$/);
                if (!m) continue;
                const r = parseInt(m[1], 10), co = parseInt(m[2], 10);
                while (grid.length <= r) grid.push([]);
                while (grid[r].length <= co) grid[r].push(null);
                grid[r][co] = c;
                maxCol = Math.max(maxCol, co);
              }
              for (const row of grid) {
                while (row.length <= maxCol) row.push(null);
              }
              const isHeader = grid.length > 1;

              // Any cell active?
              const anyActive = g.cells.some(c => c.blockId && activeBlockIds.includes(c.blockId));

              return (
                <div key={`t-${g.parentId}`}
                     className={`bg2 border p-0${anyActive ? " active-block" : ""}`}
                     style={{
                       borderRadius: 0, borderTop: globalIdx++ === 0 ? undefined : "none",
                       transition: "background 0.12s, border-color 0.12s",
                       ...(anyActive ? {
                         borderLeft: "3px solid #7C6FD6",
                         background: "rgba(124,111,214,0.08)",
                       } : {}),
                     }}>
                  <table style={{
                    width: "100%", borderCollapse: "collapse",
                    fontSize: 11.5, fontFamily: "var(--mono), monospace",
                  }}>
                    {isHeader && (
                      <thead>
                        {grid.slice(0, 1).map((row, ri) => (
                          <tr key={`h-${ri}`}>
                            {row.map((cell, ci) => {
                              const cellBb = cell?.blockId ? (cellBboxes[cell.blockId] || null) : null;
                              const cellBbox = cellBb ? {
                                x0_pct: cellBb.x0_pct, y0_pct: cellBb.y0_pct,
                                x1_pct: cellBb.x1_pct, y1_pct: cellBb.y1_pct,
                                page: cellBb.page, page_w: cellBb.page_w, page_h: cellBb.page_h,
                              } : null;
                              const cellActive = cell?.blockId && activeBlockIds.includes(cell.blockId);
                              return (
                                <th key={`h-${ri}-${ci}`}
                                  onClick={cellBbox && onCite ? () => onCite({
                                    page: cellBb.page, bbox: cellBbox,
                                    blockId: cell.blockId,
                                    quote: cellBb.text?.slice(0, 120) || (cell?.md || "").slice(0, 120),
                                  }) : undefined}
                                  title={cellBbox ? `Click to locate on page ${cellBb.page}` : undefined}
                                  style={{
                                    padding: "6px 10px", textAlign: "left",
                                    borderBottom: "2px solid var(--line)",
                                    background: cellActive ? "rgba(124,111,214,0.18)" : "var(--bg1)",
                                    fontWeight: 600, color: "var(--ink)",
                                    cursor: cellBbox ? "pointer" : undefined,
                                    borderRight: ci < (row.length - 1) ? "1px solid var(--line)" : undefined,
                                  }}>
                                  {cell ? cell.md : ""}
                                </th>
                              );
                            })}
                          </tr>
                        ))}
                      </thead>
                    )}
                    <tbody>
                      {grid.slice(isHeader ? 1 : 0).map((row, ri) => (
                        <tr key={`b-${ri}`}>
                          {row.map((cell, ci) => {
                            const cellBb = cell?.blockId ? (cellBboxes[cell.blockId] || null) : null;
                            const cellBbox = cellBb ? {
                              x0_pct: cellBb.x0_pct, y0_pct: cellBb.y0_pct,
                              x1_pct: cellBb.x1_pct, y1_pct: cellBb.y1_pct,
                              page: cellBb.page, page_w: cellBb.page_w, page_h: cellBb.page_h,
                            } : null;
                            const cellActive = cell?.blockId && activeBlockIds.includes(cell.blockId);
                            return (
                              <td key={`b-${ri}-${ci}`}
                                onClick={cellBbox && onCite ? () => onCite({
                                  page: cellBb.page, bbox: cellBbox,
                                  blockId: cell?.blockId,
                                  quote: cellBb.text?.slice(0, 120) || (cell?.md || "").slice(0, 120),
                                }) : undefined}
                                title={cellBbox ? `Click to locate on page ${cellBb.page}` : undefined}
                                style={{
                                  padding: "5px 10px", textAlign: "left",
                                  borderBottom: "1px solid var(--line)",
                                  background: cellActive ? "rgba(124,111,214,0.18)" : "transparent",
                                  color: cell ? "var(--ink)" : "var(--ink3)",
                                  cursor: cellBbox ? "pointer" : undefined,
                                  borderRight: ci < (row.length - 1) ? "1px solid var(--line)" : undefined,
                                }}>
                                {cell ? cell.md : <span style={{ opacity: 0.3, fontStyle: "italic" }}>—</span>}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {/* Table footer */}
                  <div style={{
                    padding: "5px 10px", borderTop: "1px solid var(--line)",
                    background: "var(--bg2)", display: "flex", alignItems: "center",
                    justifyContent: "space-between",
                  }}>
                    <span className="mono ink3" style={{ fontSize: 9 }}>
                      {g.parentId} · {grid.length}×{maxCol + 1} table
                      {parentBlock?.page ? ` · p.${parentBlock.page}` : ""}
                    </span>
                    {parentBbox && onCite && (
                      <span style={{ fontSize: 9, color: "var(--gold2)", cursor: "pointer" }}
                            onClick={() => onCite({
                              page: parentBlock.page,
                              bbox: parentBbox,
                              blockId: g.parentId,
                              quote: `Table ${g.parentId}`,
                            })}
                            title="Locate table on PDF">📍 locate table</span>
                    )}
                  </div>
                </div>
              );
            });
          })()}
        </div>
      ) : (
        // ── Rendered view (default) — clean rendered markdown ────────
        <div className="markdown-rendered" style={{
          maxHeight: "calc(100vh - 280px)", overflow: "auto",
          fontSize: 13, lineHeight: 1.55,
        }}>
          <ErrorBoundary>
            <MiniMarkdown source={_stripBlockMarkers(body)} />
          </ErrorBoundary>
        </div>
      )}
    </div>
  );
}

export { MarkdownTab };
