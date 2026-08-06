import React, { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon.jsx";
import RichMessage from "../components/RichMessage.jsx";
import { Pill, LoadingState, ErrorState } from "../components/Shell.jsx";
import PdfDocumentViewer from "../components/PdfDocumentViewer.jsx";
import TextFileViewer from "../components/TextFileViewer.jsx";
import XlsxFileViewer from "../components/XlsxFileViewer.jsx";
import ErrorBoundary from "./ErrorBoundary.jsx";
import ResizableHandle from "../components/ResizableHandle.jsx";
import DragDivider from "../components/DragDivider.jsx";
import { useIsMobile } from "../useIsMobile.js";
import {
  fetchDocChat, generateDocSummary, postDocChatMessage,
  reclassifyDocument, reanalyzeDocument, setDocumentType, fetchDocTypes,
  editDocumentField, addFieldFromRegion, addLineItemFromRegion, addField, deleteField,
  reviewDocument,
  fetchCategories, createCategory,
  revealDocPii, hideDocPii, fetchDocument,
  listAnnotations, createAnnotation, patchAnnotation, deleteAnnotation, exportAnnotationsMarkdown,
} from "../api";
import { useApiResource } from "../api/useApi.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { usePrompt, useAlert } from "../components/ConfirmDialog.jsx";
import EditHistory from "../components/doc-chat/EditHistory.jsx";
import MiniMarkdown from "../components/doc-chat/MiniMarkdown.jsx";
import SmartVisuals from "../components/doc-chat/SmartVisuals.jsx";
import { ArtifactBar } from "../components/AgentExtras.jsx";
import { AdvancedSidebar } from "../components/doc-chat/AdvancedSidebar.jsx";
import { MarkdownDocViewer, CsvDocumentViewer, ImageDocumentViewer } from "../components/doc-chat/DocumentViewers.jsx";
import CopyJsonButton from "../components/doc-chat/CopyJsonButton.jsx";
import DocTypeEditor from "../components/doc-chat/DocTypeEditor.jsx";
import { invalidateMarkdownCache, MarkdownTab } from "../components/doc-chat/MarkdownTab.jsx";
import { useBlockMap, computeFieldBlockMap, findMatchingBlockIds, clearBlockMapCache } from "../lib/fieldBlockLink.js";
import { LinkedTab, ReviewStatusActions, ReconcileBanner, RecallGapsPanel, UnstructuredNotice, WhyReviewBanner } from "../components/doc-chat/DocPanels.jsx";
import GraphTab from "../components/doc-chat/GraphTab.jsx";
import { invalidateJsonCache } from "../components/doc-chat/JsonTab.jsx";
import DocStatsStrip from "../components/doc-chat/DocStatsStrip.jsx";
import FieldsTab from "../components/doc-chat/FieldsTab.jsx";
import TextTab from "../components/doc-chat/TextTab.jsx";

// Render a record-cell value as readable text — an object/array cell (e.g. a lab
// test's nested attributes) must not show as "[object Object]". Returns null for
// empty so the caller can render an em-dash.
function cellText(v) {
  if (v == null || v === "") return null;
  if (typeof v !== "object") return String(v);
  const one = (x) => {
    if (x == null || typeof x !== "object") return String(x ?? "");
    if (x.value != null && x.value !== "") {
      const lbl = x.label != null && x.label !== "" ? String(x.label).replace(/_/g, " ") : null;
      return lbl ? `${lbl}: ${x.value}` : String(x.value);
    }
    // flatten fields, RECURSING into nested arrays/objects (e.g. a lab record's
    // `attributes` array) so they render as readable pairs, not "[object Object]".
    return Object.entries(x)
      .filter(([k, z]) => z != null && z !== "" && k !== "kind" && !k.startsWith("_"))
      .map(([k, z]) => (typeof z === "object" ? cellText(z) : `${k.replace(/_/g, " ")}: ${z}`))
      .filter(Boolean).join(", ");
  };
  const s = Array.isArray(v) ? v.map(one).filter(Boolean).join("; ") : one(v);
  return s || null;
}

// Width of the document pane (right side). The chat pane fills the remaining
// flexible center. Old preference was stored under chatWidth — we read it as
// a migration fallback so users don't lose their resize. Same numeric range
// (320..900), so the value is directly reusable.
const LS_DOC_W  = "docaiq.docchat.docWidth";
const LS_CHAT_W = "docaiq.docchat.chatWidth";  // legacy
const LS_ZOOM   = "docaiq.docchat.zoom";

function loadInt(key, fallback) {
  if (typeof localStorage === "undefined") return fallback;
  const v = parseInt(localStorage.getItem(key) || "", 10);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}
function saveInt(key, v) {
  try { if (typeof localStorage !== "undefined") localStorage.setItem(key, String(v)); } catch {}
}
function loadDocWidth() {
  // Try new key first; fall back to legacy chatWidth so people who already
  // resized don't get reset to the default on this code change.
  if (typeof localStorage === "undefined") return 560;
  const v = parseInt(localStorage.getItem(LS_DOC_W) || localStorage.getItem(LS_CHAT_W) || "", 10);
  return Number.isFinite(v) && v > 0 ? v : 560;
}

// DocumentChatPanel · the side-by-side reviewer experience for a single doc.
//
// Left  : PdfDocumentViewer rendering the actual file via /api/documents/<id>/file.
//         Yellow bbox overlay flashes on the page when the reviewer clicks a
//         citation chip in the chat thread.
// Right : Tabbed pane — Chat | Markdown | JSON.
//         Chat tab: auto-summary at top (generated on first open), Q&A below,
//         each AI message carries citation chips that jump + highlight.
//         Markdown / JSON tabs lazy-load on first click; "Copy" button on each.

// Primary views. Markdown + JSON are secondary "capsules" (see ON_DEMAND below):
// the always-visible Extracted-fields pane is the fields surface, so JSON is no
// longer a confusing co-equal tab, and Markdown (a costly vision render) is only
// generated on request.
const TABS = [
  { id: "chat",     label: "Chat" },
  { id: "linked",   label: "Linked" },
  { id: "graph",    label: "Graph" },
  { id: "fields",   label: "Fields" },
];
const ON_DEMAND = [
  { id: "text", label: "Text", icon: "📝 ", hint: "Chunks + rendered Markdown" },
  { id: "markdown", label: "Markdown", icon: "📄 ", hint: "Editable Markdown with View/Edit and reprocess" },
];


export default function DocumentChatPanel({ doc, onClose, onDocUpdated, workbench }) {
  const promptDialog = usePrompt();
  const alertDialog = useAlert();
  // Default to the most useful tab for this doc: Text for unstructured (text-only) docs
  // where the field schema is thin, Chat otherwise. Resets when a different doc is opened.
  const [tab, setTab] = useState(() => doc?.trust?.state === "unstructured" ? "text" : "chat");
  useEffect(() => {
    setTab(doc?.trust?.state === "unstructured" ? "text" : "chat");
  }, [doc?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const [reclassifying, setReclassifying] = useState(false);
  const [reclassifyError, setReclassifyError] = useState(null);
  // Local review-status mirror so the pill flips instantly without
  // waiting for a parent refetch.
  const [liveReviewStatus, setLiveReviewStatus] = useState(doc.reviewStatus || "pending");
  const [reviewBusy, setReviewBusy] = useState(false);
  useEffect(() => { setLiveReviewStatus(doc.reviewStatus || "pending"); }, [doc.reviewStatus, doc.id]);

  const handleReview = async (status) => {
    if (reviewBusy) return;
    let reason = null;
    if (status === "exception") {
      reason = await promptDialog({
        title: "Reason for marking exception?",
        body: "Explain what's wrong with this document so the next reviewer (or the vendor) has context.",
        placeholder: "e.g. signature page missing, scan quality too low to verify cert number",
        required: true,
        maxLength: 500,
        confirmLabel: "Mark exception",
      });
      if (!reason) return;
    }
    setReviewBusy(true);
    try {
      const fresh = await reviewDocument(doc.id, { status, reason });
      setLiveReviewStatus(fresh.reviewStatus || status);
      if (onDocUpdated) onDocUpdated(fresh);
    } catch (e) {
      alertDialog({ title: `Mark ${status} failed`, body: e.message });
    } finally {
      setReviewBusy(false);
    }
  };

  // Zoom percentage for the document viewer. Persisted across sessions so a
  // reviewer's preferred reading size sticks. 100% = native size in
  // PdfDocumentViewer (which internally multiplies by 1.4 for legibility).
  const [zoom, setZoom] = useState(() => loadInt(LS_ZOOM, 100));
  const setZoomPersist = (v) => {
    const clamped = Math.max(50, Math.min(300, v));
    setZoom(clamped);
    saveInt(LS_ZOOM, clamped);
  };

  // M53 · user highlights / annotations. Draw boxes on the doc → backend captures
  // the boxed text; persist + re-render; export to markdown.
  const [annotateMode, setAnnotateMode] = useState(false);
  const [annotations, setAnnotations] = useState([]);
  const [selectedAnnId, setSelectedAnnId] = useState(null);
  useEffect(() => {
    if (!doc?.id) { setAnnotations([]); return; }
    let cancelled = false;
    listAnnotations(doc.id)
      .then((r) => { if (!cancelled) setAnnotations(r?.annotations || []); })
      .catch(() => { if (!cancelled) setAnnotations([]); });
    return () => { cancelled = true; };
  }, [doc?.id]);
  const handleCreateAnnotation = async (page, bbox) => {
    try {
      const a = await createAnnotation(doc.id, { page, bbox });
      setAnnotations((prev) => [...prev, a]);
      setSelectedAnnId(a.id);
    } catch { /* ignore — viewer stays usable */ }
  };
  const handleDeleteAnnotation = async (id) => {
    try {
      await deleteAnnotation(doc.id, id);
      setAnnotations((prev) => prev.filter((a) => a.id !== id));
      if (selectedAnnId === id) setSelectedAnnId(null);
    } catch { /* ignore */ }
  };
  const handleNoteAnnotation = async (a) => {
    const note = window.prompt("Note for this highlight:", a.note || "");
    if (note === null) return;
    try {
      const upd = await patchAnnotation(doc.id, a.id, { note });
      setAnnotations((prev) => prev.map((x) => (x.id === a.id ? upd : x)));
    } catch { /* ignore */ }
  };
  const handleExportHighlights = async () => {
    try {
      const r = await exportAnnotationsMarkdown(doc.id);
      const blob = new Blob([r?.markdown || ""], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${(doc.name || "document").replace(/\.[^.]+$/, "")}.highlights.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
  };

  // Phase 2b · draw-to-correct — use a highlight to fix a field: sets the field's
  // value + MOVES its bbox to the drawn box + marks it verified (conf→1.0). The
  // FieldEdit audit row also feeds the (opt-in, value-free) learning loop.
  const [correctField, setCorrectField] = useState("");
  const [correctValue, setCorrectValue] = useState("");
  // Region → field: draw a box, name it → a new field whose value is pulled from the
  // text under the box (backend extracts it; the label feeds the type's learned schema).
  const [newFieldLabel, setNewFieldLabel] = useState("");
  const [regionBusy, setRegionBusy] = useState(false);
  const [regionErr, setRegionErr] = useState("");
  const handleAddFieldFromRegion = async (ann) => {
    const label = (newFieldLabel || "").trim();
    if (!label || regionBusy) return;
    setRegionBusy(true); setRegionErr("");
    try {
      const r = await addFieldFromRegion(doc.id, { label, page: ann.page, bbox: ann.bbox });
      invalidateJsonCache(doc.id); invalidateMarkdownCache(doc.id);
      // Entity-aware: the box may merge into an existing field (dedupe) rather than add new.
      if (onDocUpdated) onDocUpdated(r.document || r);
      setNewFieldLabel("");
      if (r.appended) { setRegionErr(`Added to "${r.field}" list.`); }
      else if (r.merged) { setRegionErr(`Already captured as "${r.field}" — added this location.`); }
      else { setSelectedAnnId(null); setRegionErr(""); }
    } catch (e) {
      setRegionErr(e?.message || "Couldn't read text in that box");
    } finally { setRegionBusy(false); }
  };
  // Invoice line item: box an amount → append a {description, amount, currency} row.
  const [lineItemDesc, setLineItemDesc] = useState("");
  const handleAddLineItem = async (ann) => {
    if (regionBusy) return;
    setRegionBusy(true); setRegionErr("");
    try {
      const r = await addLineItemFromRegion(doc.id, {
        page: ann.page, bbox: ann.bbox, description: lineItemDesc.trim() || null,
      });
      invalidateJsonCache(doc.id); invalidateMarkdownCache(doc.id);
      if (onDocUpdated) onDocUpdated(r.document || r);
      setLineItemDesc("");
      setRegionErr(`Line item ${r.count} added (${r.lineItem?.currency || ""} ${r.lineItem?.amount || ""}).`);
    } catch (e) {
      setRegionErr(e?.message || "Couldn't read a line amount in that box");
    } finally { setRegionBusy(false); }
  };
  // Existing field labels — picking one appends (e.g. a second `seat`) instead of a new field.
  const existingFieldLabels = (() => {
    const f = (doc.extractedFields && doc.extractedFields.fields) || {};
    const set = new Set();
    // Scalar fields AND scalar-lists (e.g. line_amount:[100,200]) — both are appendable.
    // Exclude arrays-of-objects (key_facts etc.); their entry-labels are added below.
    Object.entries(f).forEach(([k, v]) => {
      if (typeof v === "string") set.add(k);
      else if (Array.isArray(v) && (v.length === 0 || typeof v[0] !== "object")) set.add(k);
    });
    ["key_facts", "identifiers", "amounts", "dates", "parties", "records"].forEach(arr =>
      (f[arr] || []).forEach(it => { if (it && (it.label || it.name)) set.add(it.label || it.name); }));
    return [...set].sort();
  })();
  const fieldKeys = Object.keys((doc.extractedFields && doc.extractedFields.fields) || {})
    .filter((k) => {
      const v = doc.extractedFields.fields[k];
      return v == null || typeof v !== "object";   // top-level scalar fields only
    });
  const handleApplyCorrection = async (ann) => {
    if (!correctField) return;
    try {
      const fresh = await editDocumentField(doc.id, {
        field_path: `fields.${correctField}`,
        value: correctValue,
        reason: "Phase 2b · draw-to-correct (highlight)",
        page: ann.page,
        bbox: ann.bbox,
      });
      if (onDocUpdated) onDocUpdated(fresh);
      setSelectedAnnId(null); setCorrectField(""); setCorrectValue("");
    } catch { /* ignore */ }
  };

  const handleReclassify = async () => {
    if (reclassifying) return;
    setReclassifyError(null);
    setReclassifying(true);
    try {
      const fresh = await reclassifyDocument(doc.id);
      // Bust the Markdown + JSON tab caches for this doc so the next view
      // reflects the fresh extraction. Without this, JsonTab still serves
      // the pre-Re-extract body from cache and the new field_bboxes don't
      // reach the user.
      invalidateMarkdownCache(doc.id);
      invalidateJsonCache(doc.id);
      if (onDocUpdated) onDocUpdated(fresh);
    } catch (e) {
      setReclassifyError(e.message);
    } finally {
      setReclassifying(false);
    }
  };
  // Phase 3 · re-run extraction with the strong model (escalate a weak extraction on demand).
  const [reanalyzing, setReanalyzing] = useState(false);
  // M47 · Clean fields sidebar toggle
  const [showFieldsPanel, setShowFieldsPanel] = useState(false);
  const handleReanalyze = async () => {
    if (reanalyzing) return;
    setReclassifyError(null); setReanalyzing(true);
    try {
      const r = await reanalyzeDocument(doc.id);
      invalidateMarkdownCache(doc.id); invalidateJsonCache(doc.id);
      if (onDocUpdated) onDocUpdated(r.document || r);
    } catch (e) {
      setReclassifyError(e.message);
    } finally {
      setReanalyzing(false);
    }
  };
  // citations[] driving the PDF overlay: highlights[] for PdfDocumentViewer
  // (text-fuzzy match → yellow rect) plus focusedHl to scroll into view.
  const [activeCitations, setActiveCitations] = useState([]);
  const [focusedHl, setFocusedHl] = useState(null);
  const [focusedChunkPk, setFocusedChunkPk] = useState(null);  // chunk to scroll to in ChunksTab

  // ── Three-pane sync: activeBlockIds coordinates highlights across
  //     MarkdownTab, FieldsTab, and PdfDocumentViewer FieldBoxes.
  const { blockMap } = useBlockMap(doc?.id);
  const [activeBlockIds, setActiveBlockIds] = useState([]);
  const [fieldBlockMap, setFieldBlockMap] = useState({});   // fieldName → blockId[]
  const [blockFieldMap, setBlockFieldMap] = useState({});   // blockId  → fieldName[]
  const [focusedField, setFocusedField] = useState(null);    // field to pulse on PDF

  // Recompute field↔block spatial map when data changes
  useEffect(() => {
    const fboxes = doc?.extractedFields?.field_bboxes;
    if (!fboxes || !Object.keys(fboxes).length || !blockMap) {
      setFieldBlockMap({});
      setBlockFieldMap({});
      return;
    }
    const { fieldBlockMap: fbm, blockFieldMap: bfm } = computeFieldBlockMap(fboxes, blockMap);
    setFieldBlockMap(fbm);
    setBlockFieldMap(bfm);
  }, [doc?.extractedFields?.field_bboxes, blockMap]);

  // Resizable document-pane width. Document is now on the RIGHT (chat in
  // center), so dragging the splitter RIGHT shrinks it. Reads the legacy
  // chat-width preference on first load via loadDocWidth() so users keep
  // their resize.
  const [docWidth, setDocWidth] = useState(() => loadDocWidth());
  const onDocResize = (delta) => {
    setDocWidth(w => {
      const next = Math.max(320, Math.min(1000, w - delta));
      saveInt(LS_DOC_W, next);
      return next;
    });
  };
  const mobile = useIsMobile();
  // On mobile, stacking all three panes was too complex (feedback pk23). Instead show
  // ONE pane at a time via a segmented control (Document / Fields / Chat).
  const [mobPane, setMobPane] = useState("chat");
  // Width of the tall LEFT column (whichever panel the layout puts there).
  const [factsW, setFactsW] = useState(340);
  const onFactsResize = (delta) => setFactsW(w => Math.max(220, Math.min(640, w + delta)));
  // Height of the TOP-right panel (the top/bottom split on the right).
  const [topH, setTopH] = useState(440);
  const onRowResize = (dy) => setTopH(h => Math.max(150, Math.min(920, h + dy)));

  // ── User-chosen panel arrangement (persisted) ──────────────────────────
  // Three panels — Fields, Document, Chat — placed into a tall left column + a
  // stacked top/bottom right column. Each preset assigns a slot per panel.
  const [layout, setLayout] = useState(() => {
    // v2 key: the old key held a grid preset for existing users, which overrode the
    // new Simple default and kept them on the old 2-column view. A fresh key resets
    // everyone to Simple; their choice re-persists under v2.
    try { return localStorage.getItem("docaiq.docchat.layout.v2") || "simple"; } catch { return "simple"; }
  });
  // Simple mode: a clean vertical flow — stats → document (big) → surface capsules
  // → small chat. "Advanced" toggles back to the resizable 3-panel grid below.
  const simple = !mobile && layout === "simple";
  // Zoom + review-status controls. In simple mode these live in the merged top
  // box (passed to DocStatsBar's `right` slot) instead of a separate header row.
  // M47 · Unified "Advanced" sidebar — resizable width via drag handle
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advancedW, setAdvancedW] = useState(() => {
    try { return Number(localStorage.getItem("docaiq.advancedW")) || 340; } catch { return 340; }
  });
  const resizeAdvanced = (dx) => {
    setAdvancedW(w => {
      const next = Math.max(260, Math.min(600, w + dx));
      try { localStorage.setItem("docaiq.advancedW", String(next)); } catch {}
      return next;
    });
  };
  const [chatOpen, setChatOpen] = useState(false); // M47 · chat starts collapsed — click to expand
  const [rightTab, setRightTab] = useState("chat"); // "chat" | "advanced" | "markdown"
  const [docMinimized, setDocMinimized] = useState(false); // Minimize document → full-width chat
  // Document panel width — resizable via DragDivider (matches DocumentsDashboard pattern)
  const [docW, setDocW] = useState(() => {
    try { return Number(localStorage.getItem("docaiq.docW")) || 600; } catch { return 600; }
  });
  const docTopControls = (
    <>
      {!workbench && (
        <button onClick={() => { const next = !docMinimized; setDocMinimized(next); if (next) { setChatOpen(true); } }}
          style={{
            display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0, height: 36,
            padding: "0 12px", borderRadius: 10, fontSize: 11.5, cursor: "pointer",
            background: docMinimized ? "var(--bg2)" : "color-mix(in srgb, #E0A23B 18%, var(--bg1))",
            border: "1px solid " + (docMinimized ? "var(--line)" : "color-mix(in srgb, #E0A23B 40%, var(--line))"),
            color: docMinimized ? "var(--ink3)" : "#b07814",
            fontWeight: docMinimized ? 400 : 600, whiteSpace: "nowrap",
          }}>
          📄 {docMinimized ? "Document" : "Document"}
        </button>
      )}
      {!workbench && (
        <button onClick={() => { if (chatOpen || showAdvanced) { setChatOpen(false); setShowAdvanced(false); } else { setChatOpen(true); setRightTab("chat"); } }}
          style={{
            display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0, height: 36,
            padding: "0 12px", borderRadius: 10, fontSize: 11.5, cursor: "pointer",
            background: (chatOpen || showAdvanced) ? "color-mix(in srgb, #E0A23B 18%, var(--bg1))" : "var(--bg2)",
            border: "1px solid " + ((chatOpen || showAdvanced) ? "color-mix(in srgb, #E0A23B 40%, var(--line))" : "var(--line)"),
            color: (chatOpen || showAdvanced) ? "#b07814" : "var(--ink3)",
            fontWeight: (chatOpen || showAdvanced) ? 600 : 400, whiteSpace: "nowrap",
          }}>
          💬 {chatOpen || showAdvanced ? (rightTab === "markdown" ? "Markdown" : rightTab === "advanced" ? "Advanced" : "Chat") : "Chat"}
        </button>
      )}
      <button onClick={() => setZoomPersist(zoom - 25)} title="Zoom out" style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
        width: 30, height: 30, borderRadius: 8, fontSize: 15, cursor: "pointer",
        background: "var(--bg2)", border: "1px solid var(--line)", color: "var(--ink2)",
      }}>−</button>
      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--ink2)", minWidth: 36, textAlign: "center", flexShrink: 0 }}>{zoom}%</span>
      <button onClick={() => setZoomPersist(zoom + 25)} title="Zoom in" style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
        width: 30, height: 30, borderRadius: 8, fontSize: 15, cursor: "pointer",
        background: "var(--bg2)", border: "1px solid var(--line)", color: "var(--ink2)",
      }}>+</button>
      <div style={{ width: 1, height: 24, background: "var(--line)", margin: "0 4px", flexShrink: 0 }} />
      <ReviewStatusActions status={liveReviewStatus} busy={reviewBusy} onMark={handleReview}
                           reviewedBy={doc.reviewedBy} reviewedAt={doc.reviewedAt} />
      <div style={{ width: 1, height: 24, background: "var(--line)", margin: "0 4px", flexShrink: 0 }} />
      <button onClick={onClose} title="Close document"
        style={{
          display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0, height: 36,
          padding: "0 12px", borderRadius: 10, fontSize: 11.5, cursor: "pointer",
          background: "var(--bg2)", border: "1px solid var(--line)",
          color: "var(--ink3)", fontWeight: 500, whiteSpace: "nowrap",
        }}>
        ✕ Close
      </button>
    </>
  );
  // Simple-mode vertical split: draggable document height + full-screen document toggle.
  const [docH, setDocH] = useState(() => {
    try { return Number(localStorage.getItem("docaiq.docchat.docH")) || 440; } catch { return 440; }
  });
  const setDocHPersist = (fn) => setDocH((h) => {
    const next = Math.max(160, Math.min(1100, typeof fn === "function" ? fn(h) : fn));
    try { localStorage.setItem("docaiq.docchat.docH", String(next)); } catch { /* ignore */ }
    return next;
  });
  const [docExpanded, setDocExpanded] = useState(false);
  const setLayoutPersist = (v) => { setLayout(v); try { localStorage.setItem("docaiq.docchat.layout.v2", v); } catch { /* ignore */ } };
  const LAYOUTS = {
    "fields-left": { fields: "left", doc: "top",    chat: "bottom" },  // Fields · Doc / Chat
    "chat-left":   { chat: "left",   doc: "top",    fields: "bottom" }, // Chat · Doc / Fields
    "doc-left":    { doc: "left",    fields: "top", chat: "bottom" },   // Document · Fields / Chat
  };
  const SLOT = {
    left:   { gridColumn: 1, gridRow: "1 / 4" },
    top:    { gridColumn: 3, gridRow: 1 },
    bottom: { gridColumn: 3, gridRow: 3 },
  };
  const posOf = (name) => SLOT[(LAYOUTS[layout] || LAYOUTS["fields-left"])[name] || "bottom"];
  // Per-pane wrapper style: on mobile only the active pane is displayed (fills the
  // area); on desktop each pane is grid-placed by the chosen layout.
  const paneStyle = (name) => {
    if (mobile) {
      return { display: mobPane === name ? "flex" : "none", flexDirection: "column", flex: "1 1 0", minHeight: 0, minWidth: 0, width: "100%", overflow: "hidden" };
    }
    if (simple) {
      // Document takes full height (chat moved to right panel)
      if (name !== "doc") return { display: "none" };
      return { display: "flex", flexDirection: "column", flex: "1 1 0", minHeight: 200, minWidth: 0, overflow: "hidden" };
    }
    return { ...posOf(name), display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0, overflow: "hidden" };
  };

  if (!doc) return null;

  // Triggered from the chat thread when a reviewer clicks a citation chip.
  // M47 · Handle PDF click → reverse bbox lookup → highlight field
  const [locatedField, setLocatedField] = useState(null);
  const handlePageClick = async (page, x, y) => {
    if (!doc?.id) return;
    try {
      const r = await fetch(`/api/documents/${encodeURIComponent(doc.id)}/locate?page=${page}&x=${x}&y=${y}`);
      const data = await r.json();
      const field = data.hits?.find(h => h.type === "field");
      if (field) {
        setLocatedField(field.name);
        // Three-pane sync: also highlight the blocks this field came from
        const relatedBlockIds = fieldBlockMap[field.name] || [];
        setActiveBlockIds([...relatedBlockIds]);
        // Also highlight in PDF
        const bb = field.bbox;
        if (bb && onCite) {
          onCite({ page, bbox: bb, chunkPk: field.chunk_pk || 0, quote: `${field.name}: ${field.value}` }, 0);
        }
        // Auto-scroll the fields list to this field
        setTimeout(() => {
          const el = document.getElementById(`field-${field.name}`);
          el?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 100);
      }
    } catch {}
  };

  // Three-pane sync: field box clicked on PDF → highlight matching blocks
  const handleSelectField = (fieldName) => {
    setFocusedField((prev) => prev === fieldName ? null : fieldName);
    const relatedBlockIds = fieldBlockMap[fieldName] || [];
    setActiveBlockIds([...relatedBlockIds]);
    // Also scroll the fields list to this field
    setTimeout(() => {
      const el = document.getElementById(`field-${fieldName}`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
  };

  const onCite = (citation, idx) => {
    const hl = {
      id: `cit-${citation.chunkPk}`,
      page: citation.page,
      pin: String((idx % 9) + 1),
      text: citation.quote || "",
      color: citation.bbox ? "#E2BC68" : "gold",
      // M47 · propagate bbox for precise PDF overlay (was being silently dropped)
      bbox: citation.bbox || undefined,
    };
    setActiveCitations([hl]);
    setFocusedHl(hl.id);
    if (citation.chunkPk != null) setFocusedChunkPk(citation.chunkPk);

    // Three-pane sync: find which blockIds overlap the cited bbox.
    // When the citation carries an explicit blockId (e.g. table cell), include it
    // alongside the spatial overlap results so that cell-level precision is preserved.
    if (citation.bbox && blockMap) {
      const matching = findMatchingBlockIds(citation.bbox, blockMap);
      if (citation.blockId) {
        setActiveBlockIds([citation.blockId, ...matching]);
      } else {
        setActiveBlockIds(matching);
      }
    } else if (citation.blockId) {
      setActiveBlockIds([citation.blockId]);
    }
  };

  return (
    <div className="bg1 border rounded-xl" style={{
      display: "flex", flexDirection: "column", height: "100%", width: "100%", overflow: "hidden",
    }}>
      {/* Main 2-column layout: Document | Right Panel (Chat or Advanced) */}
      <div style={{
        display: "flex", flexDirection: "row", flex: "1 1 0", minHeight: 0, width: "100%", overflow: "hidden",
      }}>
      {/* Left: Document view — collapsible to give chat full width */}
      <div style={{
        ...(docMinimized && !mobile
          ? { display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", background: "var(--bg2)" }
          : (mobile || simple
            ? { display: "flex", flexDirection: "column" }
            : { display: "grid", gridTemplateColumns: `${factsW}px 6px 1fr`, gridTemplateRows: `${topH}px 6px 1fr` }
          )),
        flex: (docMinimized && !mobile) ? "0 0 48px"
              : ((chatOpen || showAdvanced) && !mobile ? `0 0 ${docW}px` : "1 1 0"),
        minHeight: 0, minWidth: 0, overflow: "hidden",
        position: "relative",
        transition: "flex 0.2s",
      }}>
      {/* Minimized: slim restore arrow. Expanded: content + slim collapse arrow at right edge */}
      {(!mobile && docMinimized) ? (
        <button onClick={() => { setDocMinimized(false); setChatOpen(true); }}
          title="Restore document"
          className="hover-bg"
          style={{ width: "100%", flex: 1, cursor: "pointer", fontSize: 18,
            color: "var(--ink2)", display: "flex", alignItems: "center",
            justifyContent: "center", border: "none", background: "none" }}>
          »
        </button>
      ) : (
      <>
      <div style={{ display: "contents" }}>
      {/* Mobile: segmented control — pick one pane instead of stacking all three (pk23) */}
      {mobile && (
        <div className="row" style={{ flex: "0 0 auto", gap: 6, padding: 8, borderBottom: "1px solid var(--line)", background: "var(--bg2)" }}>
          {[["doc", "Document"], ["fields", "Fields"], ["chat", "Chat"]].map(([id, label]) => (
            <button key={id} onClick={() => setMobPane(id)}
              className={mobPane === id ? "btn-gold" : "border bg1"}
              style={{ flex: 1, padding: "9px 0", borderRadius: 999, fontSize: 12.5, fontWeight: mobPane === id ? 600 : 400, cursor: "pointer" }}>
              {label}
            </button>
          ))}
          <button onClick={onClose} title="Close" className="border bg1 hover-bg ink3" style={{ flex: "0 0 auto", width: 38, borderRadius: 999, display: "grid", placeItems: "center" }}>
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {/* Extracted fields — editable + appendable · placed by layout */}
      {/* HIDE when showing right panel with Advanced tab */}
      {!(showAdvanced && (showAdvanced || chatOpen)) && (
      <div style={paneStyle("fields")}>
        <div className="row between p-3 border-b" style={{ flex: "0 0 auto", background: "var(--bg2)", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <span className="upper ink3" style={{ fontSize: 10, letterSpacing: ".06em" }}>Extracted fields</span>
          <div className="row gap-1" style={{ alignItems: "center", flexWrap: "wrap" }}>
            <button onClick={handleReclassify} disabled={reclassifying}
              title="Re-run classifier + fact extractor" className="border bg1 hover-bg"
              style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: reclassifying ? "wait" : "pointer" }}>
              {reclassifying ? "…" : "Re-extract"}
            </button>
            <button onClick={handleReanalyze} disabled={reanalyzing}
              title="Re-run extraction with the best (strong) model — for a weak or thin extraction"
              className="border bg1 hover-bg"
              style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: reanalyzing ? "wait" : "pointer" }}>
              {reanalyzing ? "…" : "✨ Best model"}
            </button>
            <button onClick={() => setAnnotateMode((m) => !m)}
              title="Draw highlight boxes on the document → captures the text + your notes"
              className="border bg1 hover-bg"
              style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer",
                       background: annotateMode ? "rgba(224,162,59,0.25)" : undefined,
                       borderColor: annotateMode ? "#e0a23b" : undefined }}>
              {annotateMode ? "✎ Drawing…" : "✎ Highlight"}{annotations.length ? ` (${annotations.length})` : ""}
            </button>
            <CopyJsonButton data={doc.extractedFields} />
          </div>
        </div>
        {doc.extractedFields?.fields && Object.keys(doc.extractedFields?.field_bboxes || {}).length === 0 && (
          <div className="ink3" style={{ flex: "0 0 auto", fontSize: 11, padding: "6px 12px",
            borderBottom: "1px solid var(--line)", background: "var(--bg1)", lineHeight: 1.4 }}>
            📍 On-document highlighting is available for PDFs (and scanned images). This document has no
            page coordinates, so clicking a field won't highlight it in the viewer.
          </div>
        )}
        <div style={{ flex: "1 1 0", minHeight: 0, overflow: "auto", padding: 12 }}>
          {/* Hidden + re-rendered as the "Fields" surface in Simple mode → skip the duplicate. */}
          {!simple && (doc.extractedFields && (doc.extractedFields.fields || doc.extractedFields.notes)
            ? <FactsCard ef={doc.extractedFields} onCite={onCite} doc={doc} onDocUpdated={onDocUpdated} activeBlockIds={activeBlockIds} fieldBlockMap={fieldBlockMap} />
            : <div className="ink3 text-sm" style={{ fontStyle: "italic" }}>No extracted fields yet.</div>)}
        </div>
      </div>
      )}

      {/* resize handles — advanced-grid desktop only (simple/mobile stack vertically) */}
      {!mobile && !simple && (
        <ResizableHandle onDelta={onFactsResize} ariaLabel="Resize left panel"
          style={{ gridColumn: 2, gridRow: "1 / 4" }} />
      )}
      {!mobile && !simple && (
        <ResizableHandle orientation="horizontal" onDelta={onRowResize} ariaLabel="Resize top panel"
          style={{ gridColumn: 3, gridRow: 2 }} />
      )}

      {/* Chat (AI summary + conversation) · placed by layout. min-height:0 + min-width:0
          is the fix for the "chat input pushed off-screen" bug — without it the flex
          column ignores the cell's height ceiling and the inner overflow:auto never engages. */}
      <div className="flex col" style={paneStyle("chat")}>
        <div className="row p-3 border-b gap-2" style={{ alignItems: "center", flex: "0 0 auto" }}>
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
                    className={tab === t.id ? "btn-gold" : "border bg2"}
                    style={{ padding: "5px 12px", borderRadius: 14, fontSize: 11, cursor: "pointer" }}>
              {t.label}
            </button>
          ))}
          {/* On-demand capsules — generated only when opened (Text = chunks + rendered Markdown). */}
          <span aria-hidden className="ink3" style={{ opacity: 0.4, margin: "0 2px" }}>·</span>
          {ON_DEMAND.map(c => {
            const mdPrimary = c.id === "text" && doc?.trust?.state === "unstructured";
            return (
            <button key={c.id} onClick={() => setTab(c.id)}
                    className={tab === c.id ? "btn-gold" : "border bg1 ink3"}
                    title={mdPrimary ? "This document is text-only — rendered Markdown is the most useful view" : c.hint}
                    style={{ padding: "5px 10px", borderRadius: 14, fontSize: 11, cursor: "pointer",
                             opacity: tab === c.id ? 1 : 0.8 }}>
              {c.icon}{c.label}{mdPrimary ? " ★" : ""}
            </button>
          );})}
          <div style={{ flex: 1 }} />
          {doc.piiProtected && (
            <PiiToggle doc={doc} onDocUpdated={onDocUpdated} />
          )}
          <span className="serif font-semibold truncate ink3" style={{ maxWidth: 200, fontSize: 13 }}>
            {doc.name}
          </span>
          <button onClick={onClose} title="Close document" aria-label="Close document"
            className="border bg1 hover-bg ink2" style={{ flex: "0 0 auto", padding: "4px 8px", borderRadius: 6, display: "grid", placeItems: "center" }}>
            <Icon name="x" size={15} />
          </button>
        </div>
        <div style={{ flex: "1 1 0", minHeight: 0, display: "flex", flexDirection: "column" }}>
          {/* In Simple mode this pane is hidden and re-rendered as the surface capsules
              + bottom chat below, so skip mounting these here (avoids a duplicate chat).
              Graph is the exception — it renders in all modes since it has no Simple fallback. */}
          {!simple && tab === "chat"     && <ChatTab doc={doc} onCite={onCite} />}
          {!simple && tab === "linked"   && <LinkedTab  doc={doc} />}
          {tab === "graph"    && <ErrorBoundary><GraphTab   doc={doc} /></ErrorBoundary>}
          {!simple && tab === "fields"   && <FieldsTab doc={doc} onCite={onCite} onDocUpdated={onDocUpdated} locatedField={locatedField} revealed={!!doc.piiRevealed} activeBlockIds={activeBlockIds} fieldBlockMap={fieldBlockMap} />}
          {!simple && tab === "text"     && <TextTab   doc={doc} onCite={onCite} focusedChunkPk={focusedChunkPk} revealed={!!doc.piiRevealed} activeBlockIds={activeBlockIds} blockFieldMap={blockFieldMap} />}
          {!simple && tab === "markdown" && <MarkdownTab docId={doc.id} doc={doc} revealed={!!doc.piiRevealed} onCite={onCite} activeBlockIds={activeBlockIds} blockFieldMap={blockFieldMap} />}
        </div>
      </div>

      {/* Document viewer (with zoom controls) · placed by layout */}
      <div style={paneStyle("doc")}>
        {/* Unified quality KPI strip + inline controls — inside the document view */}
        <DocStatsStrip doc={doc} onReview={() => { if (mobile) setMobPane("fields"); }} controls={docTopControls} />
        {/* In simple mode this whole header row is hidden — zoom + review
            live in the capsule strip above. Doc-type + layout stay here
            (for Advanced mode). */}
        {!simple && (
        <div className="row between p-3 border-b" style={{ alignItems: "center", background: "var(--bg2)", flex: "0 0 auto", flexWrap: "wrap", gap: 8 }}>
          <div className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
            <Icon name="folder" size={14} />
            <DocTypeEditor doc={doc} onDocUpdated={onDocUpdated} />
            <span className="mono ink3 text-xs">{doc.id}</span>
          </div>
          <div className="row gap-1" style={{ alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
            {/* One-click return to the Simple stacked view */}
            {!mobile && !simple && (
              <button onClick={() => setLayoutPersist("simple")}
                      title="Back to the simple stacked view"
                      className="btn-gold"
                      style={{ height: 24, borderRadius: 4, fontSize: 11, cursor: "pointer", padding: "0 10px", marginRight: 4 }}>
                ◀ Simple view
              </button>
            )}
            {/* Layout picker */}
            {!mobile && !simple && (
              <select value={layout} onChange={(e) => setLayoutPersist(e.target.value)}
                      title="Arrange the panels"
                      className="border bg2"
                      style={{ height: 24, borderRadius: 4, fontSize: 11, color: "var(--ink2)", cursor: "pointer", padding: "0 4px" }}>
                <option value="simple">▤ Simple (stacked)</option>
                <option value="fields-left">▦ Fields · Doc / Chat</option>
                <option value="chat-left">▦ Chat · Doc / Fields</option>
                <option value="doc-left">▦ Doc · Fields / Chat</option>
              </select>
            )}
            {/* Zoom + review status now live in the DocStatsStrip capsule row */}
          </div>
        </div>
        )}
        {reclassifyError && (
          <div className="px-3 py-2" style={{ background: "rgba(216,98,94,0.15)", borderBottom: "1px solid rgba(216,98,94,0.4)", fontSize: 11 }}>
            Re-extract failed: {reclassifyError}
          </div>
        )}
        {(annotateMode || annotations.length > 0) && (
          <div className="px-3 py-2 border-b" style={{ background: "var(--bg1)", fontSize: 11, maxHeight: 150, overflow: "auto", flex: "0 0 auto" }}>
            <div className="row between" style={{ marginBottom: 6, alignItems: "center" }}>
              <span className="upper ink3" style={{ fontSize: 10, letterSpacing: 0.5 }}>My highlights ({annotations.length})</span>
              <button onClick={handleExportHighlights} disabled={!annotations.length}
                      className="border bg2 hover-bg"
                      style={{ padding: "2px 8px", borderRadius: 3, fontSize: 10, cursor: annotations.length ? "pointer" : "default", opacity: annotations.length ? 1 : 0.5 }}>
                Export .md
              </button>
            </div>
            {annotateMode && (
              <div className="ink3" style={{ marginBottom: 6, fontStyle: "italic" }}>
                Drag a box on the document to capture its text. Click a highlight to add a note.
              </div>
            )}
            {annotations.map((a) => (
              <div key={a.id} style={{ borderTop: "1px solid var(--line)", padding: "4px 0",
                            background: selectedAnnId === a.id ? "rgba(224,162,59,0.10)" : undefined }}>
                <div className="row gap-2" style={{ alignItems: "center" }}>
                  <span className="mono ink3" style={{ flex: "0 0 auto", fontSize: 9 }}>p{a.page}</span>
                  <button onClick={() => { const sel = selectedAnnId === a.id ? null : a.id; setSelectedAnnId(sel); setCorrectField(""); setCorrectValue(sel ? (a.text || "") : ""); }}
                          title={a.text || ""}
                          className="truncate" style={{ flex: "1 1 0", minWidth: 0, textAlign: "left", background: "none", border: "none", color: "var(--ink2)", cursor: "pointer", fontSize: 11 }}>
                    {(a.note || a.text || "(image region)").slice(0, 80)}
                  </button>
                  <button onClick={() => handleNoteAnnotation(a)} title="Add / edit note"
                          className="ink3 hover-bg" style={{ flex: "0 0 auto", border: "none", background: "none", cursor: "pointer", fontSize: 12 }}>📝</button>
                  <button onClick={() => handleDeleteAnnotation(a.id)} title="Delete highlight"
                          className="ink3 hover-bg" style={{ flex: "0 0 auto", border: "none", background: "none", cursor: "pointer", fontSize: 13, lineHeight: 1 }}>×</button>
                </div>
                {/* Phase 2b · draw-to-correct: turn this highlight into a field fix */}
                {selectedAnnId === a.id && fieldKeys.length > 0 && (
                  <div className="row gap-1" style={{ marginTop: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="ink3" style={{ fontSize: 10 }}>Correct field →</span>
                    <select value={correctField} onChange={(e) => setCorrectField(e.target.value)}
                            className="border bg2" style={{ fontSize: 11, padding: "2px 4px", borderRadius: 4, maxWidth: 130 }}>
                      <option value="">field…</option>
                      {fieldKeys.map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                    <input value={correctValue} onChange={(e) => setCorrectValue(e.target.value)} placeholder="correct value"
                           className="border bg2" style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, flex: "1 1 90px", minWidth: 60, color: "var(--ink)" }} />
                    <button onClick={() => handleApplyCorrection(a)} disabled={!correctField}
                            className="border bg2 hover-bg" style={{ fontSize: 10, padding: "2px 9px", borderRadius: 4, cursor: correctField ? "pointer" : "default", opacity: correctField ? 1 : 0.5 }}>
                      Apply fix
                    </button>
                  </div>
                )}
                {/* Region → field: name this box → a new field (value = text under it) */}
                {selectedAnnId === a.id && (
                  <div className="row gap-1" style={{ marginTop: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="ink3" style={{ fontSize: 10 }}>Field →</span>
                    <input value={newFieldLabel} onChange={(e) => setNewFieldLabel(e.target.value)}
                           list="regionfield-labels"
                           placeholder="new name, or pick a field (e.g. seat) to append"
                           title="Type a new field name, or choose an existing field to append another value (e.g. a 2nd seat)"
                           className="border bg2" style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, flex: "1 1 120px", minWidth: 70, color: "var(--ink)" }} />
                    <datalist id="regionfield-labels">
                      {existingFieldLabels.map(l => <option key={l} value={l} />)}
                    </datalist>
                    <button onClick={() => handleAddFieldFromRegion(a)} disabled={!newFieldLabel.trim() || regionBusy}
                            title="Save the text under this box into the schema (appends if the field already exists)"
                            className="border bg2 hover-bg" style={{ fontSize: 10, padding: "2px 9px", borderRadius: 4, cursor: (newFieldLabel.trim() && !regionBusy) ? "pointer" : "default", opacity: (newFieldLabel.trim() && !regionBusy) ? 1 : 0.5 }}>
                      {regionBusy ? "Saving…"
                        : existingFieldLabels.some(l => l.toLowerCase() === newFieldLabel.trim().toLowerCase())
                          ? `Append to ${newFieldLabel.trim()} →` : "Add field →"}
                    </button>
                    {regionErr && <span style={{ color: "var(--rose, #D8625E)", fontSize: 10 }}>{regionErr}</span>}
                  </div>
                )}
                {/* Invoice line item: box the amount → a {description, amount, currency} row */}
                {selectedAnnId === a.id && (doc.docType === "invoice" || doc.docType === "receipt") && (
                  <div className="row gap-1" style={{ marginTop: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="ink3" style={{ fontSize: 10 }}>Line item →</span>
                    <input value={lineItemDesc} onChange={(e) => setLineItemDesc(e.target.value)}
                           placeholder="description (optional)"
                           title="Box the line's amount; this becomes a line_items row {description, amount, currency}. Currency is auto-detected."
                           className="border bg2" style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, flex: "1 1 120px", minWidth: 70, color: "var(--ink)" }} />
                    <button onClick={() => handleAddLineItem(a)} disabled={regionBusy}
                            title="The boxed amount + currency become a line_items row"
                            className="border bg2 hover-bg" style={{ fontSize: 10, padding: "2px 9px", borderRadius: 4, cursor: regionBusy ? "default" : "pointer", opacity: regionBusy ? 0.5 : 1 }}>
                      {regionBusy ? "Adding…" : "Add line item →"}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <ReconcileBanner doc={doc} />
        <WhyReviewBanner doc={doc} status={liveReviewStatus} />
        <UnstructuredNotice doc={doc} onViewMarkdown={() => setTab("text")} />
        {/* In simple mode this moves into the Fields surface — the document box stays clean. */}
        {!simple && <RecallGapsPanel doc={doc} onCite={onCite} onDocUpdated={onDocUpdated} />}
        <div style={{ flex: "1 1 0", minHeight: 0, overflow: "auto" }}>
          {(() => {
            const mt = (doc.mimeType || "").toLowerCase();
            const nm = (doc.name || "").toLowerCase();
            const isImage = mt.startsWith("image/");
            const isXlsx = mt === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        || nm.endsWith(".xlsx");
            const isCsv = !isXlsx && (mt === "text/csv" || mt === "application/csv"
                       || mt === "application/vnd.ms-excel"
                       || nm.endsWith(".csv"));
            // Plain-text uploads (.txt/.md/.eml/...) aren't PDFs — PDF.js throws
            // "Invalid PDF structure". Render them as text instead.
            const isText = (mt.startsWith("text/") && !isCsv)
                        || mt === "message/rfc822"
                        || /\.(txt|md|markdown|log|eml|text)$/.test(nm);
            const isPdf = mt === "application/pdf" || nm.endsWith(".pdf");
            // Office binaries (docx/doc/pptx/ppt/odt/odp/rtf) can't render in
            // PDF.js or the browser. Fall back to the extracted markdown so the
            // content is still viewable (faithful layout would need a server-side
            // LibreOffice→PDF render — see docs).
            const isOffice = /\.(docx?|pptx?|odt|odp|rtf)$/.test(nm)
                        || mt.includes("wordprocessingml") || mt.includes("presentationml")
                        || mt === "application/msword" || mt === "application/vnd.ms-powerpoint"
                        || mt.includes("opendocument");
            if (isImage) return <ImageDocumentViewer doc={doc} highlights={activeCitations} zoom={zoom} />;
            if (isXlsx)  return <XlsxFileViewer doc={doc} zoom={zoom} />;
            if (isCsv)   return <CsvDocumentViewer doc={doc} zoom={zoom} />;
            if (isText)  return <TextFileViewer doc={doc} zoom={zoom} showHeader />;
            if (isOffice) return <MarkdownDocViewer doc={doc} zoom={zoom} />;
            // Anything that ISN'T a PDF must not be force-fed to PDF.js (it throws
            // "Invalid PDF structure" → blank/error). Show its markdown instead.
            if (!isPdf) return <MarkdownDocViewer doc={doc} zoom={zoom} />;
            return null;
          })() || (
            <PdfDocumentViewer
              doc={doc}
              highlights={activeCitations}
              focusedHl={focusedHl}
              setFocusedHl={setFocusedHl}
              zoom={zoom}
              annotateMode={annotateMode}
              annotations={annotations}
              onCreateAnnotation={handleCreateAnnotation}
              selectedAnnId={selectedAnnId}
              onSelectAnn={setSelectedAnnId}
              hideHeader={simple}
              onPageClick={handlePageClick}
              focusedField={focusedField}
              onSelectField={handleSelectField}
              activeBlockIds={activeBlockIds}
              fieldBlockMap={fieldBlockMap}
            />
          )}
        </div>
      </div>
      {/* Slim collapse arrow at right edge — matches AllDocuments « pattern */}
      {!mobile && !docMinimized && (
        <button onClick={() => setDocMinimized(true)}
          title="Collapse document → full-width chat"
          className="border bg2 hover-bg"
          style={{
            position: "absolute", right: 3, top: "50%", transform: "translateY(-50%)", zIndex: 100,
            width: 20, height: 42, borderRadius: 6, cursor: "pointer", fontSize: 13,
            color: "var(--ink2)", lineHeight: 1,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
          «
        </button>
      )}
      </div>
      </>
      )}
      </div>
      {/* End of document view div */}
      {/* Right panel: Chat OR Advanced — collapsible like All Documents */}
      {(!simple || !mobile) && (
        (showAdvanced || chatOpen) ? (
          <>
            {/* Drag to resize the document panel */}
            <DragDivider getWidth={() => docW} setWidth={(w) => { setDocW(w); try { localStorage.setItem("docaiq.docW", String(w)); } catch {} }} min={320} max={1100} />
            {/* Right panel content — fills remaining space */}
            <div style={{
              flex: "1 1 0", minWidth: 280,
              background: "var(--bg1)", borderLeft: "1px solid var(--line)",
              display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0,
            }}>
              {/* Tab bar */}
              <div className="row border-b" style={{ flexShrink: 0, gap: 2, padding: "8px 10px", background: "var(--bg2)" }}>
                <button onClick={() => { setRightTab("chat"); setShowAdvanced(false); setChatOpen(true); }}
                  style={{ padding: "6px 12px", borderRadius: 4, fontSize: 12, cursor: "pointer",
                    background: rightTab === "chat" ? "var(--gold2)" : "transparent",
                    color: rightTab === "chat" ? "var(--ink)" : "var(--ink3)",
                    border: "none", fontWeight: rightTab === "chat" ? 600 : 400, whiteSpace: "nowrap" }}>
                  💬 Chat
                </button>
                <button onClick={() => { setRightTab("advanced"); setShowAdvanced(true); setChatOpen(true); }}
                  style={{ padding: "6px 12px", borderRadius: 4, fontSize: 12, cursor: "pointer",
                    background: rightTab === "advanced" ? "var(--gold2)" : "transparent",
                    color: rightTab === "advanced" ? "var(--ink)" : "var(--ink3)",
                    border: "none", fontWeight: rightTab === "advanced" ? 600 : 400, whiteSpace: "nowrap" }}>
                  🔧 Advanced
                </button>
                <button onClick={() => { setRightTab("markdown"); setShowAdvanced(false); setChatOpen(true); }}
                  style={{ padding: "6px 12px", borderRadius: 4, fontSize: 12, cursor: "pointer",
                    background: rightTab === "markdown" ? "var(--gold2)" : "transparent",
                    color: rightTab === "markdown" ? "var(--ink)" : "var(--ink3)",
                    border: "none", fontWeight: rightTab === "markdown" ? 600 : 400, whiteSpace: "nowrap" }}>
                  📄 Markdown
                </button>
                <div style={{ flex: 1 }} />
                <button onClick={() => { setShowAdvanced(false); setChatOpen(false); }}
                  title="Collapse panel"
                  className="border bg1 hover-bg" style={{ padding: "4px 8px", borderRadius: 4, fontSize: 14, cursor: "pointer", lineHeight: 1 }}>
                  ◀
                </button>
              </div>
              {/* Content */}
              <div style={{ flex: "1 1 0", overflow: "auto", display: "flex", flexDirection: "column" }}>
                {rightTab === "chat" && <ChatTab doc={doc} onCite={onCite} />}
                {rightTab === "advanced" && (
                  <AdvancedSidebar doc={doc} onCite={onCite}
                    onDocUpdated={onDocUpdated} onReclassify={handleReclassify} reclassifying={reclassifying}
                    layout={layout} setLayout={setLayoutPersist} docExpanded={docExpanded} setDocExpanded={setDocExpanded}
                    width={advancedW} onResize={resizeAdvanced} locatedField={locatedField}
                    activeBlockIds={activeBlockIds} fieldBlockMap={fieldBlockMap} />
                )}
                {rightTab === "markdown" && (
                  <MarkdownTab docId={doc.id} doc={doc} revealed={!!doc.piiRevealed} onCite={onCite} activeBlockIds={activeBlockIds} blockFieldMap={blockFieldMap} />
                )}
              </div>
            </div>
          </>
        ) : (
          /* Collapsed — slim toggle strip (same pattern as All Documents) */
          <button onClick={() => setChatOpen(true)} title="Show chat"
            className="bg1 border rounded-xl hover-bg"
            style={{ flex: "0 0 36px", width: 36, cursor: "pointer", fontSize: 15,
              color: "var(--ink2)", display: "flex", alignItems: "center",
              justifyContent: "center", borderLeft: "1px solid var(--line)" }}>
            💬
          </button>
        )
      )}
      </div>
      {/* End of 2-column layout */}
    </div>
  );
}


// ── Chat tab ──────────────────────────────────────────────────────────────

// Suggested questions per document type — shown as one-click chips so the reviewer
// doesn't have to think of what to ask. Keyed by doc_type; falls back to DEFAULT_Q.
const QUESTIONS_BY_TYPE = {
  invoice:                 ["What's the total amount?", "When is it due?", "Who is the vendor?", "List all line items"],
  receipt:                 ["What's the total?", "What was purchased?", "What's the date?", "What payment method?"],
  credit_card_statement:   ["What's the balance due?", "When is payment due?", "What are the biggest charges?", "What's the minimum payment?"],
  bank_statement:          ["What's the closing balance?", "Total deposits vs withdrawals?", "List the transactions", "Any large or unusual charges?"],
  national_id:             ["What's the ID number?", "What's the full name?", "What's the date of birth?", "When does it expire?"],
  passport:                ["What's the passport number?", "When does it expire?", "What's the nationality?", "What's the date of birth?"],
  resume:                  ["Summarize the experience", "What are the key skills?", "What are the contact details?", "How many years of experience?"],
  business_profile:        ["What's the company name?", "Who are the owners / directors?", "What's the registration number?", "What's the registered address?"],
  training_certificate:    ["What certification is this?", "Who is it awarded to?", "When was it issued?", "Who issued it?"],
  master_service_agreement:["Who are the parties?", "What's the term / duration?", "What are the key obligations?", "How can it be terminated?"],
  reminder:                ["What do I need to do?", "When is it due?", "Summarize the action items"],
  shopping_list:           ["What's on the list?", "How many items?", "Any quantities noted?"],
};
const DEFAULT_Q = ["Summarize this document", "What are the key dates?", "What are the important numbers?", "Any risks or red flags?"];
const suggestQuestions = (docType) => QUESTIONS_BY_TYPE[docType] || DEFAULT_Q;

// M44.P11.2 · per-document PII reveal/hide. Visible only when the doc is
// PII-protected; the Reveal/Hide button shows for owner/admin/reviewer
// (server also enforces + audits). Flipping it updates the doc so the Chat /
// Markdown / JSON tabs re-render detokenized (or re-hidden).
function PiiToggle({ doc, onDocUpdated }) {
  const { hasRole } = useAuth();
  const [busy, setBusy] = useState(false);
  const canReveal = hasRole("admin") || hasRole("reviewer");
  const revealed = !!doc.piiRevealed;
  const toggle = async () => {
    if (!canReveal || busy) return;
    setBusy(true);
    try {
      const r = revealed ? await hideDocPii(doc.id) : await revealDocPii(doc.id);
      // Bust the Markdown/JSON tab caches so they re-fetch with the new state.
      invalidateMarkdownCache(doc.id);
      invalidateJsonCache(doc.id);
      // Re-fetch the doc detail so the Key Facts panel + content show the
      // revealed (or re-hidden) values, not just flip the badge.
      let fresh = { ...doc, piiProtected: r.piiProtected, piiRevealed: r.piiRevealed };
      try { fresh = { ...(await fetchDocument(doc.id)), piiProtected: r.piiProtected, piiRevealed: r.piiRevealed }; }
      catch { /* keep the optimistic flags */ }
      onDocUpdated?.(fresh);
    } catch (_e) { /* surfaced via no state change */ }
    finally { setBusy(false); }
  };
  return (
    <span className="row gap-2" style={{ alignItems: "center" }}>
      <span className="mono"
            title="PII (cards, IBAN, passport, NRIC, SSN…) is tokenized in storage; real values are encrypted in the vault"
            style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10,
                     background: revealed ? "rgba(216,98,94,0.12)" : "rgba(63,164,122,0.12)",
                     color: revealed ? "var(--rose)" : "var(--emerald)" }}>
        {revealed ? "PII revealed" : "🔒 PII protected"}
      </span>
      {canReveal && (
        <button onClick={toggle} disabled={busy} className="border bg2"
                style={{ fontSize: 10, padding: "2px 10px", borderRadius: 10, cursor: "pointer" }}
                title={revealed ? "Re-hide PII" : "Reveal real values (audited)"}>
          {busy ? "…" : (revealed ? "Hide" : "Reveal")}
        </button>
      )}
    </span>
  );
}

function ChatTab({ doc, onCite }) {
  const { data: thread, loading, error, setData } = useApiResource(
    () => fetchDocChat(doc.id), [doc.id, doc.piiRevealed]
  );
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const [sumOpen, setSumOpen] = useState(false);   // expand the (long) AI summary
  const [hideSummary, setHideSummary] = useState(false);  // M47 · toggle AI summary visibility
  const scrollRef = useRef(null);

  // Auto-scroll to bottom on new message.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [thread?.messages?.length]);

  // Auto-generate the summary on first open if not cached.
  useEffect(() => {
    if (!thread || thread.summary || summarizing) return;
    if (doc.ingestionStatus !== "ready") return;
    let cancelled = false;
    setSummarizing(true);
    generateDocSummary(doc.id)
      .then(msg => {
        if (cancelled) return;
        return fetchDocChat(doc.id).then(t => setData(t));
      })
      .catch(e => { if (!cancelled) setSendError(`Auto-summary failed: ${e.message}`); })
      .finally(() => { if (!cancelled) setSummarizing(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread, doc.id, doc.ingestionStatus]);

  // Send a question — from the input box OR a suggested-question chip.
  const send = async (raw) => {
    const text = (raw || "").trim();
    if (!text || sending || doc.ingestionStatus !== "ready") return;
    setSending(true);
    setSendError(null);
    setDraft("");
    try {
      // Optimistic: push the user msg locally before the AI replies.
      setData(prev => prev ? {
        ...prev,
        messages: [
          ...prev.messages,
          { id: -Date.now(), role: "user", text, citations: [], createdAt: new Date().toISOString() },
        ],
      } : prev);
      await postDocChatMessage(doc.id, text);
      // Refetch the whole thread so the optimistic user-msg gets the real PK.
      const fresh = await fetchDocChat(doc.id);
      setData(fresh);
    } catch (err) {
      setSendError(err.message);
    } finally {
      setSending(false);
    }
  };
  const submit = (e) => { e?.preventDefault(); send(draft); };

  if (loading) return <LoadingState label="Loading chat…"/>;
  if (error)   return <ErrorState message={error}/>;

  // Drop the auto-generated summary message — it's already shown (concisely) in the AI summary
  // card above, so rendering the full breakdown again just floods the chat. Matches both the
  // meta tag and the legacy "(summary)" text prefix.
  const nonSummaryMsgs = (thread.messages || []).filter(
    m => !(m.role === "ai" && (m.meta === "summary" || (m.text || "").startsWith("(summary)")))
  );

  return (
    <div className="flex col" style={{ flex: "1 1 0", minHeight: 0 }}>
      <div ref={scrollRef} style={{ flex: "1 1 0", minHeight: 0, overflow: "auto", padding: 16 }}>
        {/* Key Facts now lives in its own left column (see DocumentChatPanel) — the chat
            keeps just the AI summary + conversation. */}
        {/* AI summary — collapsed to a few lines by default (it can be long); Show more expands it */}
        {!hideSummary && thread.summary && (
          <div className="bg2 border rounded-md p-3 mb-3" style={{ borderColor: "rgba(200,160,76,0.4)" }}>
            <div className="upper ink3 mb-2" style={{ fontSize: 10 }}>AI summary</div>
            <div className="text-sm" style={{
              whiteSpace: "pre-wrap",
              ...(sumOpen ? {} : { display: "-webkit-box", WebkitLineClamp: 6, WebkitBoxOrient: "vertical", overflow: "hidden" }),
            }}>{thread.summary}</div>
            {(thread.summary.length > 320) && (
              <button onClick={() => setSumOpen(o => !o)} className="ink3"
                style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, padding: 0, marginTop: 6, color: "var(--gold2)" }}>
                {sumOpen ? "Show less ▲" : "Show more ▼"}
              </button>
            )}
          </div>
        )}
        {summarizing && !thread.summary && (
          <div className="ink3 text-sm mb-3" style={{ fontStyle: "italic" }}>Generating summary…</div>
        )}
        {nonSummaryMsgs.map(m => <MessageRow key={m.id} m={m} onCite={onCite}/>)}
      </div>

      {sendError && (
        <div className="text-sm p-2 mx-4 mb-2" style={{ background: "rgba(216,98,94,0.08)", color: "var(--rose)", borderRadius: 4 }}>
          {sendError}
        </div>
      )}

      {/* Suggested questions — one-click, tailored to the document type */}
      {doc.ingestionStatus === "ready" && (
        <div className="row px-3 pt-2" style={{ gap: 6, flexWrap: "wrap", flex: "0 0 auto" }}>
          {suggestQuestions(doc.docType).map((q) => (
            <button key={q} type="button" onClick={() => send(q)} disabled={sending}
              className="border bg2 hover-bg"
              style={{ fontSize: 11, padding: "4px 10px", borderRadius: 999, color: "var(--ink2)", cursor: sending ? "default" : "pointer" }}>
              {q}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="p-3 row gap-2" style={{ flex: "0 0 auto" }}>
        <input
          type="text"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={doc.ingestionStatus !== "ready" ? "Document not ready yet…" : "Ask about this document…"}
          disabled={sending || doc.ingestionStatus !== "ready"}
          className="bg1 border grow"
          style={{ padding: "9px 14px", borderRadius: 999, fontSize: 13, color: "var(--ink)", outline: "none", minWidth: 0 }}
        />
        {/* M47 · AI summary toggle — left of Send */}
        {thread.summary && (
          <button type="button" onClick={() => setHideSummary(v => !v)}
            title={hideSummary ? "Show AI summary" : "Hide AI summary"}
            style={{ width: 36, height: 36, borderRadius: 999, display: "grid", placeItems: "center",
              padding: 0, flex: "0 0 auto", fontSize: 15, cursor: "pointer",
              background: hideSummary ? "var(--bg2)" : "rgba(226,188,104,0.15)",
              border: `1px solid ${hideSummary ? "var(--line)" : "var(--gold2)"}`,
              color: hideSummary ? "var(--ink3)" : "var(--gold2)",
            }}>
            <Icon name="file-text" size={16} />
          </button>
        )}
        <button type="submit" disabled={sending || !draft.trim()} className="btn-gold" title="Send" aria-label="Send"
                style={{ width: 40, height: 40, borderRadius: 999, display: "grid", placeItems: "center", padding: 0, flex: "0 0 auto" }}>
          {sending ? "…" : <Icon name="send" size={17} />}
        </button>
      </form>
    </div>
  );
}


// M44.P2 · Document Agent trace renderer. Lazy-loaded list of ReAct
// steps (thought · action · observation) so reviewers can verify *how*
// the agent arrived at an answer. One row per step, with the tool name
// + args rendered as compact JSON and the observation truncated.
function TraceView({ rows, loading, onCite }) {
  if (loading && !rows) {
    return <div className="ink3 text-xs mt-2 mono">loading reasoning trace…</div>;
  }
  if (!rows || rows.length === 0) {
    return <div className="ink3 text-xs mt-2 mono">(no trace recorded)</div>;
  }
  return (
    <div className="mt-2" style={{
      borderLeft: "2px solid rgba(200,160,76,0.45)",
      paddingLeft: 10,
      fontSize: 11,
    }}>
      {rows.map((step) => {
        const isFinal = step.actionName === "final_answer";
        return (
          <div key={step.stepIndex} className="mb-2">
            <div className="row gap-1" style={{ alignItems: "center", color: "var(--gold2)" }}>
              <span className="mono" style={{ fontSize: 10 }}>step {step.stepIndex}</span>
              <span style={{ fontSize: 10, color: "var(--ink3)" }}>·</span>
              <span className="mono" style={{ fontSize: 10 }}>
                {step.actionName || "(no action)"}
              </span>
              {step.latencyMs != null && (
                <span className="mono" style={{ fontSize: 10, color: "var(--ink3)" }}>
                  {step.latencyMs}ms
                </span>
              )}
            </div>
            {step.thought && (
              <div className="ink2" style={{ fontSize: 11, fontStyle: "italic", marginTop: 2 }}>
                💭 {step.thought}
              </div>
            )}
            {step.actionArgs && Object.keys(step.actionArgs).length > 0 && (
              <div className="mono ink3" style={{ fontSize: 10, marginTop: 2 }}>
                args: {JSON.stringify(step.actionArgs).slice(0, 220)}
              </div>
            )}
            {step.observation && !isFinal && (
              <div className="mono ink3" style={{ fontSize: 10, marginTop: 2 }}>
                ↳ {step.observation.slice(0, 280)}
                {step.observation.length > 280 ? "…" : ""}
              </div>
            )}
            {step.error && (
              <div className="mono" style={{ fontSize: 10, marginTop: 2, color: "#D8625E" }}>
                ⚠ {step.error}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


// Collapsible "Thinking" disclosure — the model's reasoning steps captured from the
// answer's [[THINKING]] block (backend chat_pipeline). Shown above the answer, like a
// research assistant surfacing HOW it reached the result. Dashed rail echoes the pattern.
function ThinkingBlock({ steps }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="mb-1" style={{ maxWidth: "100%" }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="ink3"
        style={{ background: "none", border: "none", cursor: "pointer", padding: 0,
                 fontSize: 11, display: "flex", alignItems: "center", gap: 5 }}
      >
        <span style={{ fontSize: 9 }}>{open ? "▾" : "▸"}</span>
        <span style={{ letterSpacing: "0.04em" }}>Thinking</span>
        {!open && <span style={{ fontSize: 10, opacity: 0.7 }}>· {steps.length} step{steps.length === 1 ? "" : "s"}</span>}
      </button>
      {open && (
        <div style={{ marginTop: 4, paddingLeft: 9, borderLeft: "1px dashed var(--line)",
                      display: "flex", flexDirection: "column", gap: 4 }}>
          {steps.map((s, i) => (
            <div key={i} className="ink3" style={{ fontSize: 11, lineHeight: 1.5 }}>{s}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function MessageRow({ m, onCite }) {
  const isUser = m.role === "user";
  // M43.P1.5 · "challenged by critic" badge appears when the backend
  // tagged this message with meta='critiqued' (the answer went through
  // ≥1 refine pass before being returned).
  const wasCritiqued = !isUser && m.meta === "critiqued";
  // M44.P2 · "agent" badge appears when the answer came from the
  // Document Agent ReAct loop. meta starts with 'agent' (may include
  // ' · forced_terminate' when the loop hit MAX_STEPS).
  const wasAgent = !isUser && typeof m.meta === "string" && m.meta.startsWith("agent");
  // M44.P7 · provenance chip for every other pipeline step · so
  // reviewers can see which path produced the answer (and trust /
  // distrust accordingly). 1-line, low-key, gold text.
  const provenanceChip = (() => {
    if (isUser || !m.meta) return null;
    if (wasCritiqued || wasAgent) return null; // handled above
    if (m.meta.startsWith("cache_hit"))    return { icon: "♻", label: "FROM CACHE · prior reviewer's answer · 0 LLM" };
    if (m.meta === "identity_guard")       return { icon: "🛡", label: "IDENTITY GUARD · wrong-person refused" };
    if (m.meta === "facts_det")            return { icon: "📋", label: "FROM STRUCTURED FACTS · 0 LLM" };
    if (m.meta === "facts")                return { icon: "📋", label: "FROM STRUCTURED FACTS" };
    if (m.meta === "full_doc_ctx")         return { icon: "📄", label: "WHOLE-DOC CONTEXT · single LLM call" };
    if (m.meta === "rag_retrieval")        return { icon: "🔍", label: "RAG · retrieved + reranked excerpts" };
    if (m.meta === "artifact_fallback")    return { icon: "⚡", label: "DB FALLBACK · LLM was unavailable" };
    if (m.meta === "summary")              return null; // initial summary already has the AI SUMMARY header
    if (m.meta === "single_shot")          return null; // legacy path · no badge
    return null;
  })();
  const [showTrace, setShowTrace] = React.useState(false);
  const [traceRows, setTraceRows] = React.useState(null);
  const [traceLoading, setTraceLoading] = React.useState(false);
  const loadTrace = React.useCallback(async () => {
    if (traceRows || traceLoading) return;
    setTraceLoading(true);
    try {
      const r = await fetch(`/api/chat-messages/${m.pk}/trace`, { credentials: "same-origin" });
      if (r.ok) {
        const body = await r.json();
        setTraceRows(body.steps || []);
      }
    } catch (e) {
      console.warn("trace fetch failed", e);
    } finally {
      setTraceLoading(false);
    }
  }, [m.pk, traceRows, traceLoading]);
  // Thumbs feedback · only on AI messages, fires the helpful/unhelpful
  // endpoints which increment counters on the matching reflexion_pairs
  // row. State is per-row local so a rapid second click is a no-op.
  const [vote, setVote] = React.useState(null); // 'up' | 'down' | null
  const sendVote = async (dir) => {
    if (vote === dir) return;
    setVote(dir);
    try {
      await fetch(
        `/api/chat-messages/${m.pk}/${dir === "up" ? "helpful" : "unhelpful"}`,
        { method: "POST", credentials: "same-origin" },
      );
    } catch (e) {
      console.warn("vote failed", e);
    }
  };
  return (
    <div className="mb-3" style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div style={{ maxWidth: "85%" }}>
        {!isUser && Array.isArray(m.thinking) && m.thinking.length > 0 && (
          <ThinkingBlock steps={m.thinking} />
        )}
        <div className={`bubble ink ${isUser ? "bubble-you" : "bubble-ai"}`} style={{
          fontSize: 13, maxWidth: "100%",
          ...(wasCritiqued ? { borderColor: "rgba(200,160,76,0.55)" } : null),
        }}>
          {isUser ? <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span> : <RichMessage content={m.text} />}
        </div>
        {!isUser && <SmartVisuals content={m.text} />}
        {!isUser && <ArtifactBar artifacts={m.artifacts} />}
        {wasCritiqued && (
          <div className="row gap-1 mt-1" style={{ fontSize: 10, color: "var(--gold2)", alignItems: "center" }}>
            <span>⚖</span>
            <span style={{ letterSpacing: "0.04em" }}>
              CHALLENGED BY CRITIC · refined before answering
            </span>
          </div>
        )}
        {provenanceChip && (
          <div className="row gap-1 mt-1" style={{ fontSize: 10, color: "var(--ink3)", alignItems: "center" }}>
            <span>{provenanceChip.icon}</span>
            <span style={{ letterSpacing: "0.04em" }}>{provenanceChip.label}</span>
          </div>
        )}
        {wasAgent && (
          <div className="row gap-1 mt-1" style={{ fontSize: 10, color: "var(--gold2)", alignItems: "center" }}>
            <span>🤖</span>
            <span style={{ letterSpacing: "0.04em" }}>
              DOCUMENT AGENT · multi-step reasoning
              {m.meta.includes("forced_terminate") && " · hit step limit"}
            </span>
            <button
              type="button"
              onClick={() => { setShowTrace(s => !s); if (!showTrace) loadTrace(); }}
              style={{
                marginLeft: 6, padding: "1px 8px", fontSize: 10, borderRadius: 4,
                border: "1px solid var(--line)", background: "var(--bg2)",
                color: "var(--ink2)", cursor: "pointer",
              }}
            >{showTrace ? "Hide" : "Show"} reasoning</button>
          </div>
        )}
        {wasAgent && showTrace && (
          <TraceView rows={traceRows} loading={traceLoading} onCite={onCite} />
        )}
        {m.confidence != null && (
          <div className="ink3 mono text-xs mt-1">confidence {(m.confidence * 100).toFixed(0)}%</div>
        )}
        {(m.citations || []).length > 0 && (
          <div className="mt-2">
            <div className="ink3" style={{ fontSize: 9, letterSpacing: ".08em", marginBottom: 4 }}>
              SOURCES · click to jump to the region on the document
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {m.citations.map((c, i) => (
                <button key={i}
                        onClick={() => onCite({ page: c.page, bbox: c.bbox, chunkPk: c.chunkPk, quote: c.quote }, i)}
                        title={c.bbox ? "Jump to the exact region on the document" : "Jump to the source page"}
                        className="border bg2 hover-bg"
                        style={{
                          textAlign: "left", padding: "5px 9px", borderRadius: 6, cursor: "pointer",
                          display: "flex", gap: 8, alignItems: "baseline", width: "100%",
                        }}>
                  <span className="mono" style={{ fontSize: 10, color: "var(--gold2)", whiteSpace: "nowrap", flexShrink: 0 }}>
                    p.{c.page} {c.bbox ? "📍" : "🔗"}
                  </span>
                  {c.quote && (
                    <span className="ink2" style={{
                      fontSize: 11, lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis",
                      display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                    }}>“{c.quote}”</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}
        {!isUser && m.pk && (
          <div className="row gap-2 mt-2" style={{ alignItems: "center" }}>
            <button
              type="button"
              onClick={() => sendVote("up")}
              title="This answer was helpful · trains future answers"
              style={{
                padding: "2px 8px", fontSize: 11, borderRadius: 4,
                border: vote === "up" ? "1px solid var(--gold)" : "1px solid var(--line)",
                background: vote === "up" ? "rgba(200,160,76,0.18)" : "var(--bg2)",
                color: vote === "up" ? "var(--gold2)" : "var(--ink2)",
                cursor: "pointer",
              }}
            >👍 Helpful</button>
            <button
              type="button"
              onClick={() => sendVote("down")}
              title="This answer was wrong · ignore similar critiques next time"
              style={{
                padding: "2px 8px", fontSize: 11, borderRadius: 4,
                border: vote === "down" ? "1px solid rgba(216,98,94,0.55)" : "1px solid var(--line)",
                background: vote === "down" ? "rgba(216,98,94,0.10)" : "var(--bg2)",
                color: vote === "down" ? "#D8625E" : "var(--ink2)",
                cursor: "pointer",
              }}
            >👎</button>
            {vote && <span className="ink3" style={{ fontSize: 10 }}>thanks · feedback recorded</span>}
          </div>
        )}
      </div>
    </div>
  );
}


// ── Facts card ────────────────────────────────────────────────────────────
// Renders documents.extracted_fields (the JSON pulled by the universal
// fact_extractor or the KYC vision extractor) as a compact key/value card.
// Scalar fields render inline; arrays of objects (parties[], signature_blocks[],
// line_items[], key_obligations[], etc.) render as collapsed details/summary
// blocks. The reviewer sees deterministic facts at a glance without asking.
function FactsCard({ ef, onCite, doc, onDocUpdated, activeBlockIds = [], fieldBlockMap = {} }) {
  const alertDialog = useAlert();
  const [expanded, setExpanded] = useState(true);
  // Direct add-field (type a name + value, no box) + per-field delete.
  const [addName, setAddName] = useState("");
  const [addVal, setAddVal] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const refreshDoc = async () => {
    try { const fresh = await fetchDocument(doc.id); onDocUpdated?.(fresh); } catch { /* keep */ }
  };
  const submitAddField = async () => {
    const name = addName.trim();
    if (!name || addBusy) return;
    setAddBusy(true); setEditErr(null);
    try {
      await addField(doc.id, { label: name, value: addVal });
      setAddName(""); setAddVal("");
      await refreshDoc();
    } catch (e) { setEditErr(e.message || "Couldn't add the field"); }
    finally { setAddBusy(false); }
  };
  const removeField = async (key) => {
    if (!window.confirm(`Remove the "${key}" field from this document?`)) return;
    try { await deleteField(doc.id, key); await refreshDoc(); }
    catch (e) { setEditErr(e.message || "Couldn't delete the field"); }
  };
  const [editing, setEditing] = useState(null);   // { path } when an edit input is open
  const [saving, setSaving] = useState(false);
  const [editErr, setEditErr] = useState(null);
  const [showHistory, setShowHistory] = useState(false);
  // Per-row edit state for array-of-objects fields:
  //   { fieldKey: 'top_transactions', idx: 5 }  → row 5 of top_transactions is in edit mode
  const [editingArray, setEditingArray] = useState(null);
  const [arraySaving, setArraySaving] = useState(false);
  // Live overrides so the UI reflects edits instantly without waiting
  // for the parent to refetch. Falls back to ef.fields otherwise.
  // For arrays we override the entire array; the resolver below merges.
  const [liveOverrides, setLiveOverrides] = useState({});
  const fields = ef?.fields || {};
  const fieldBboxes = ef?.field_bboxes || {};
  const fieldConf = ef?.field_confidence || {};
  const docType = ef?.doc_type || "document";
  const conf = ef?.confidence;
  const notes = ef?.notes;
  // G7 · how many populated fields the extractor flagged low-confidence (<0.7) →
  // the reviewer's to-check count, shown in the header.
  const needsReview = Object.entries(fields).filter(
    ([k, v]) => v != null && v !== "" && typeof fieldConf[k] === "number" && fieldConf[k] < 0.7
  ).length;

  // Render even with empty fields IF we have notes — that's the
  // extractor's "I tried but this doc doesn't fit the schema" message
  // and the reviewer needs to see it (otherwise the panel is just blank).
  const hasAnyField = fields && Object.values(fields).some(
    v => v != null && v !== "" && !(Array.isArray(v) && v.length === 0)
  );
  if (!hasAnyField && !notes) return null;

  // Click a field → push its bbox to the PDF overlay so the yellow rect
  // jumps to the exact phrase. Falls back gracefully when the field has no
  // pinned bbox (image-only docs, fields whose value couldn't be located).
  const cite = (fname) => {
    if (!onCite) return;
    const bb = fieldBboxes[fname];
    if (!bb) return;
    const bbox = { page: bb.page, x0: bb.x0, y0: bb.y0, x1: bb.x1, y1: bb.y1 };
    if (bb.page_w && bb.page_h) { bbox.page_w = bb.page_w; bbox.page_h = bb.page_h; }
    onCite({
      page: bb.page,
      bbox,
      chunkPk: bb.chunk_pk || 0,
      quote: `${fname}: ${fields[fname]}`,
    }, 0);
  };

  return (
    <div className="bg2 border rounded-md p-3 mb-3" style={{ borderColor: "rgba(139,127,214,0.5)" }}>
      <div className="row between mb-2" style={{ alignItems: "center" }}>
        <div className="row gap-2" style={{ alignItems: "center" }}>
          <div className="upper ink3" style={{ fontSize: 10 }}>Key facts</div>
          <Pill color="violet">{docType}</Pill>
          {conf != null && (
            <span className="mono ink3" style={{ fontSize: 10 }}>
              {(conf * 100).toFixed(0)}% conf
            </span>
          )}
          {needsReview > 0 && (
            <span
              title={`${needsReview} field${needsReview === 1 ? "" : "s"} the extractor was unsure about — flagged ⚠ check below`}
              style={{
                fontSize: 9, fontWeight: 700, color: "#E0A23B",
                border: "1px solid rgba(224,162,59,0.55)", background: "rgba(224,162,59,0.12)",
                borderRadius: 3, padding: "1px 5px", lineHeight: 1.4,
              }}
            >⚠ {needsReview} to review</span>
          )}
        </div>
        <button onClick={() => setExpanded(e => !e)}
                className="hover-bg ink3"
                style={{ padding: "2px 8px", borderRadius: 4, fontSize: 10, cursor: "pointer", border: "1px solid var(--line)" }}>
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      {expanded && (
        <div style={{ fontSize: 12 }}>
          {/* Extractor notes — when the LLM can't reliably fill the schema
              (foreign-language doc, wrong doc-type, low-quality image),
              it explains why in `notes`. Surface this BEFORE the empty
              field rows so the reviewer knows the AI honestly couldn't
              parse it, not that the UI is broken. */}
          {notes && (
            <div className="border rounded-md mb-3 p-3" style={{
              background: "rgba(224,162,59,0.10)",
              borderColor: "rgba(224,162,59,0.5)",
              fontSize: 12, lineHeight: 1.5,
            }}>
              <div className="upper mb-1" style={{
                fontSize: 10, letterSpacing: 0.6, color: "#E0A23B", fontWeight: 700,
              }}>
                ⚠ Extractor note
                {conf != null && (
                  <span className="ink3 ml-2" style={{ fontSize: 9, fontWeight: 500 }}>
                    confidence {(conf * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <div className="ink2">{notes}</div>
              <div className="ink3 mt-2" style={{ fontSize: 10 }}>
                Use the Re-extract button to retry, or manually edit fields below.
              </div>
            </div>
          )}
          {Object.entries(fields).map(([k, v]) => {
            // Apply any in-session edits without waiting for refetch
            const liveValue = liveOverrides[k] !== undefined ? liveOverrides[k] : v;
            const isArrayOfObjects = Array.isArray(liveValue) && liveValue.length > 0
              && typeof liveValue[0] === "object" && liveValue[0] !== null;
            return (
              <FactRow
                key={k}
                label={k}
                value={liveValue}
                hasBbox={!!fieldBboxes[k]}
                onCite={() => cite(k)}
                isActive={(() => { const linked = fieldBlockMap[k] || []; return linked.some(bid => activeBlockIds.includes(bid)); })()}
                editing={editing?.path === `fields.${k}`}
                onStartEdit={doc && !isArrayOfObjects ? () => setEditing({ path: `fields.${k}` }) : null}
                onDelete={doc && !isArrayOfObjects ? () => removeField(k) : null}
                onCancelEdit={() => setEditing(null)}
                onSave={async (newVal, reason) => {
                  if (!doc) return;
                  setSaving(true); setEditErr(null);
                  try {
                    await editDocumentField(doc.id, {
                      field_path: `fields.${k}`,
                      value: newVal,
                      reason: reason || null,
                    });
                    setLiveOverrides(o => ({ ...o, [k]: newVal }));
                    setEditing(null);
                  } catch (e) {
                    setEditErr(e.message);
                  } finally {
                    setSaving(false);
                  }
                }}
                saving={saving}
                // Edited-this-session fields are human-verified → confident (clears
                // the ⚠ flag instantly); otherwise show the extractor's G4 score.
                confidence={liveOverrides[k] !== undefined ? 1 : fieldConf[k]}
                // docType drives the category dropdown's vocabulary
                // (expense vs income enum). vendorPk lets the picker pull
                // vendor-local custom entries for this doc. Both pass
                // through to ScalarEditor.
                docType={doc?.docType}
                vendorPk={doc?.vendorPk}
                // Per-row pencil for array-of-objects fields (transactions,
                // line items). Only wired when we have a doc context.
                fieldKey={k}
                editingArrayIdx={editingArray?.fieldKey === k ? editingArray.idx : null}
                onEditArrayItem={doc && isArrayOfObjects ? (i) => setEditingArray({ fieldKey: k, idx: i }) : null}
                onCancelArrayItem={() => setEditingArray(null)}
                arraySaving={arraySaving}
                onApproveArrayItem={doc && isArrayOfObjects ? async (i, nextValue) => {
                  // Toggle _approved on row i. Records audit trail via the
                  // standard PATCH /fields endpoint; optimistic update
                  // applied locally so the green tick lights up instantly.
                  try {
                    await editDocumentField(doc.id, {
                      field_path: `fields.${k}.${i}._approved`,
                      value: nextValue,
                      reason: nextValue ? "Item approved" : "Item un-approved",
                    });
                    setLiveOverrides(o => {
                      const arr = (o[k] !== undefined ? o[k] : liveValue).slice();
                      arr[i] = { ...arr[i], _approved: nextValue };
                      return { ...o, [k]: arr };
                    });
                  } catch (e) {
                    alertDialog({ title: "Approve failed", body: e.message });
                  }
                } : null}
                onSaveArrayItem={async (i, updates, reason) => {
                  if (!doc) return;
                  setArraySaving(true); setEditErr(null);
                  try {
                    // Patch each changed field separately so the field_edits
                    // audit trail captures one row per actual change. Most
                    // edits are 1-2 fields (category + reason).
                    for (const [field, val] of Object.entries(updates)) {
                      await editDocumentField(doc.id, {
                        field_path: `fields.${k}.${i}.${field}`,
                        value: val,
                        reason: reason || null,
                      });
                    }
                    // Apply locally
                    setLiveOverrides(o => {
                      const arr = (o[k] !== undefined ? o[k] : liveValue).slice();
                      arr[i] = { ...arr[i], ...updates };
                      return { ...o, [k]: arr };
                    });
                    setEditingArray(null);
                  } catch (e) {
                    setEditErr(e.message);
                  } finally {
                    setArraySaving(false);
                  }
                }}
              />
            );
          })}
          {editErr && (
            <div className="mt-2 p-2 border rounded-md" style={{
              background: "rgba(216,98,94,0.15)", borderColor: "rgba(216,98,94,0.4)",
              fontSize: 11, color: "#D8625E",
            }}>
              Edit failed: {editErr}
            </div>
          )}
          {!hasAnyField && !notes && (
            <div className="ink3 text-sm" style={{ fontStyle: "italic", padding: "8px 0" }}>
              No fields extracted.
            </div>
          )}
          {/* Direct add-field — type a NAME + VALUE (separate), no box-drawing needed */}
          {doc && (
            <div className="mt-3 pt-2" style={{ borderTop: "1px solid var(--line)" }}>
              <div className="upper ink3 mb-2" style={{ fontSize: 9, letterSpacing: ".06em" }}>Add a field</div>
              <div className="row gap-1" style={{ flexWrap: "wrap", alignItems: "center" }}>
                <input value={addName} onChange={(e) => setAddName(e.target.value)}
                  placeholder="field name (e.g. Country/Place of birth)" title="The field's name"
                  className="border bg1" style={{ flex: "1 1 130px", minWidth: 100, padding: "5px 8px", borderRadius: 4, fontSize: 11, color: "var(--ink)" }} />
                <input value={addVal} onChange={(e) => setAddVal(e.target.value)}
                  placeholder="value (e.g. INDIA)" title="The value"
                  onKeyDown={(e) => { if (e.key === "Enter") submitAddField(); }}
                  className="border bg1" style={{ flex: "1 1 110px", minWidth: 90, padding: "5px 8px", borderRadius: 4, fontSize: 11, color: "var(--ink)" }} />
                <button onClick={submitAddField} disabled={!addName.trim() || addBusy}
                  className="btn-gold" style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: (addName.trim() && !addBusy) ? "pointer" : "default", opacity: (addName.trim() && !addBusy) ? 1 : 0.5 }}>
                  {addBusy ? "Adding…" : "Add field"}
                </button>
              </div>
            </div>
          )}
          {doc && (
            <div className="mt-3 pt-2" style={{ borderTop: "1px solid var(--line)" }}>
              <button
                onClick={() => setShowHistory(s => !s)}
                className="ink3 hover-bg"
                style={{
                  padding: "3px 8px", borderRadius: 4, fontSize: 10, cursor: "pointer",
                  border: "1px solid var(--line)", background: "transparent",
                }}>
                {showHistory ? "Hide" : "Show"} edit history
              </button>
              {showHistory && <EditHistory docId={doc.id}/>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// Edit-history list pulled on demand.
function FactRow({ label, value, hasBbox, onCite, isActive, editing, onStartEdit, onDelete, onCancelEdit, onSave, saving,
                   fieldKey, onEditArrayItem, editingArrayIdx, onSaveArrayItem, onCancelArrayItem, arraySaving,
                   onApproveArrayItem, docType, vendorPk, confidence }) {
  const niceLabel = label.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  // G7 · per-field confidence (G4). Flag fields the extractor was unsure about so
  // the reviewer knows exactly what to check/correct — the heart of the review UI.
  const lowConf = typeof confidence === "number" && confidence < 0.7;
  const confBadge = lowConf ? (
    <span
      title={`Low extractor confidence (${Math.round(confidence * 100)}%) — please verify`}
      style={{
        fontSize: 9, fontWeight: 700, color: "#E0A23B",
        border: "1px solid rgba(224,162,59,0.55)", background: "rgba(224,162,59,0.12)",
        borderRadius: 3, padding: "0 4px", lineHeight: 1.5, whiteSpace: "nowrap",
      }}
    >⚠ check</span>
  ) : null;

  // Empty values get a muted "—" so the reviewer can tell what was checked
  // but came back blank vs. what was never asked for.
  if (value == null || value === "" || (Array.isArray(value) && value.length === 0)) {
    return (
      <div className="row" style={{ padding: "3px 0", gap: 10, alignItems: "center" }}>
        <span className="ink3" style={{ minWidth: 140, fontSize: 11 }}>{niceLabel}</span>
        <span className="ink3">—</span>
        {confBadge}
      </div>
    );
  }

  // Boolean scalar → Yes/No pill
  if (typeof value === "boolean") {
    return (
      <div className="row" style={{ padding: "3px 0", gap: 10, alignItems: "center" }}>
        <span className="ink3" style={{ minWidth: 140, fontSize: 11 }}>{niceLabel}</span>
        <Pill color={value ? "emerald" : "amber"}>{value ? "Yes" : "No"}</Pill>
      </div>
    );
  }

  // Array of objects → render as a real table when there are 3+ items
  // (transactions / line items). Falls back to the per-item card view
  // for short arrays where a table would be overkill (e.g. 1-2 parties
  // on an agreement, a single signature block).
  if (Array.isArray(value) && typeof value[0] === "object" && value[0] !== null) {
    // Pick column order: union of all keys, with common ones surfacing first
    // and a stable preferred-column order for transaction-like arrays so
    // CC statements render Date | Description | Amount | Direction.
    const preferred = ["date", "posted_date", "description", "merchant", "signatory_name",
                       "signatory_role", "signature_date", "page",
                       "name", "role", "quantity", "unit_price", "amount", "direction",
                       "category", "balance_after"];
    const seen = new Set();
    const cols = [];
    for (const p of preferred) {
      if (value.some(o => o && Object.prototype.hasOwnProperty.call(o, p)) && !seen.has(p)) {
        cols.push(p); seen.add(p);
      }
    }
    for (const obj of value) {
      for (const k of Object.keys(obj || {})) {
        if (!seen.has(k)) { cols.push(k); seen.add(k); }
      }
    }
    // `_approved` is the per-row sign-off flag — rendered as a green ✓
    // in the action column, not as a data cell. Filter it out so it
    // doesn't show up as a redundant "_approved: true" column.
    const dataCols = cols.filter(c => c !== "_approved");
    const approvedCount = value.filter(o => o?._approved).length;
    const useTable = value.length >= 3;
    return (
      <details style={{ padding: "3px 0" }} open>
        <summary className="ink3" style={{ cursor: "pointer", fontSize: 11 }}>
          {niceLabel} <span className="mono">({value.length})</span>
          {onApproveArrayItem && (
            <span className="ml-2" style={{
              fontSize: 10, color: approvedCount > 0 ? "#3FA47A" : "var(--ink3)", fontWeight: 600,
            }}>
              · ✓ {approvedCount}/{value.length} approved
            </span>
          )}
        </summary>
        {useTable ? (
          <div style={{ marginTop: 6, overflow: "auto", borderRadius: 4, border: "1px solid var(--line)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead style={{ background: "var(--bg1)" }}>
                <tr>
                  {dataCols.map(c => (
                    <th key={c} style={{
                      padding: "5px 8px", textAlign: "left",
                      fontSize: 10, textTransform: "uppercase",
                      letterSpacing: 0.06, color: "var(--ink3)",
                      fontWeight: 600, whiteSpace: "nowrap",
                      borderBottom: "1px solid var(--line)",
                    }}>
                      {c.replace(/_/g, " ")}
                    </th>
                  ))}
                  {onEditArrayItem && (
                    <th style={{ width: 32, borderBottom: "1px solid var(--line)" }}/>
                  )}
                </tr>
              </thead>
              <tbody>
                {value.map((obj, i) => {
                  // Row in edit mode → replace the data row with an inline
                  // editor that spans all columns.
                  if (editingArrayIdx === i && onSaveArrayItem) {
                    return (
                      <tr key={`edit-${i}`} style={{ borderTop: i ? "1px solid var(--line)" : "none", background: "rgba(200,160,76,0.08)" }}>
                        <td colSpan={dataCols.length + 1} style={{ padding: 8 }}>
                          {(obj && ("direction" in obj || "unit_price" in obj || "balance_after" in obj
                                    || ("amount" in obj && "category" in obj))) ? (
                            <TxnRowEditor
                              txn={obj}
                              onCancel={onCancelArrayItem}
                              onSave={(updates, reason) => onSaveArrayItem(i, updates, reason)}
                              saving={arraySaving}
                              vendorPk={vendorPk}
                            />
                          ) : (
                            <GenericRowEditor
                              row={obj}
                              onCancel={onCancelArrayItem}
                              onSave={(updates, reason) => onSaveArrayItem(i, updates, reason)}
                              saving={arraySaving}
                            />
                          )}
                        </td>
                      </tr>
                    );
                  }
                  return (
                    <tr key={i} style={{ borderTop: i ? "1px solid var(--line)" : "none" }}>
                      {dataCols.map(c => {
                        const v = obj?.[c];
                        const isMoney = c === "amount" || c === "balance_after" || c === "unit_price";
                        const isDate = c === "date" || c === "posted_date" || c === "signature_date";
                        const isDir = c === "direction";
                        return (
                          <td key={c} style={{
                            padding: "5px 8px", verticalAlign: "top",
                            fontFamily: isMoney || isDate ? "var(--mono)" : "inherit",
                            color: isDir && v === "credit" ? "#3FA47A" : isDir && v === "debit" ? "#D8625E" : "inherit",
                            textAlign: isMoney ? "right" : "left",
                            whiteSpace: c === "description" ? "normal" : "nowrap",
                          }}>
                            {cellText(v) ?? <span className="ink3">—</span>}
                          </td>
                        );
                      })}
                      {onEditArrayItem && (
                        <td style={{ padding: "3px 6px", textAlign: "right", verticalAlign: "top", whiteSpace: "nowrap" }}>
                          {/* Per-row approve toggle. _approved=true persists
                              via the existing PATCH /fields endpoint; the
                              tick lights up green when set. */}
                          <button
                            onClick={() => onApproveArrayItem?.(i, !obj?._approved)}
                            title={obj?._approved ? "Unapprove this row" : "Approve this row"}
                            className="hover-bg"
                            style={{
                              padding: "1px 6px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                              border: "1px solid " + (obj?._approved ? "rgba(63,164,122,0.55)" : "var(--line)"),
                              background: obj?._approved ? "rgba(63,164,122,0.18)" : "transparent",
                              color: obj?._approved ? "#3FA47A" : "var(--ink3)",
                              lineHeight: 1, marginRight: 4, fontWeight: 600,
                            }}
                          >
                            {obj?._approved ? "✓" : "○"}
                          </button>
                          <button
                            onClick={() => onEditArrayItem(i)}
                            title="Edit this row"
                            className="hover-bg ink3"
                            style={{
                              padding: "1px 5px", borderRadius: 3, fontSize: 10, cursor: "pointer",
                              border: "1px solid var(--line)", lineHeight: 1, opacity: 0.7,
                              background: "transparent",
                            }}
                          >
                            ✏️
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ paddingLeft: 12, marginTop: 4 }}>
            {value.map((obj, i) => (
              <div key={i} className="bg1 border rounded-md p-2 mb-1" style={{ fontSize: 11 }}>
                {Object.entries(obj).map(([sk, sv]) => (
                  <div key={sk} className="row" style={{ gap: 8 }}>
                    <span className="ink3" style={{ minWidth: 110 }}>{sk.replace(/_/g, " ")}</span>
                    <span style={{ wordBreak: "break-word" }}>{cellText(sv) ?? "—"}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </details>
    );
  }

  // Array of strings → bullet list
  if (Array.isArray(value)) {
    return (
      <div style={{ padding: "3px 0" }}>
        <div className="ink3" style={{ fontSize: 11, marginBottom: 2 }}>{niceLabel}</div>
        <ul style={{ paddingLeft: 18, margin: 0 }}>
          {value.map((s, i) => <li key={i} style={{ fontSize: 12 }}>{String(s)}</li>)}
        </ul>
      </div>
    );
  }

  // Nested object (e.g. invoice.vendor → {name, address, tax_id})
  if (typeof value === "object") {
    return (
      <div style={{ padding: "3px 0" }}>
        <div className="ink3" style={{ fontSize: 11, marginBottom: 2 }}>{niceLabel}</div>
        <div style={{ paddingLeft: 12, fontSize: 11 }}>
          {Object.entries(value).map(([sk, sv]) => (
            <div key={sk} className="row" style={{ gap: 8 }}>
              <span className="ink3" style={{ minWidth: 110 }}>{sk.replace(/_/g, " ")}</span>
              <span style={{ wordBreak: "break-word" }}>{cellText(sv) ?? "—"}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Plain scalar — clickable pin icon when a tight bbox is available,
  // plus an inline edit button when an onStartEdit handler is wired.
  if (editing) {
    return <ScalarEditor label={fieldKey || niceLabel} value={value} onCancel={onCancelEdit} onSave={onSave} saving={saving} docType={docType} vendorPk={vendorPk}/>;
  }
  return (
    <div className="row group" style={{ padding: "3px 0", gap: 10, alignItems: "center", ...(isActive ? { background: "rgba(124,111,214,0.08)", borderLeft: "3px solid #7C6FD6", paddingLeft: 6, borderRadius: 2 } : {}) }}>
      <span className="ink3" style={{ minWidth: 140, fontSize: 11 }}>{niceLabel}</span>
      <span style={{ wordBreak: "break-word", flex: 1 }}>{String(value)}</span>
      {confBadge}
      {hasBbox && onCite && (
        <button
          onClick={onCite}
          title="Jump to this field on the PDF"
          className="hover-bg ink3"
          style={{
            padding: "1px 6px", borderRadius: 3, fontSize: 10, cursor: "pointer",
            border: "1px solid var(--line)", lineHeight: 1, opacity: 0.7,
          }}
        >
          📍
        </button>
      )}
      {onStartEdit && (
        <button
          onClick={onStartEdit}
          title="Edit this field"
          className="hover-bg ink3"
          style={{
            padding: "1px 6px", borderRadius: 3, fontSize: 10, cursor: "pointer",
            border: "1px solid var(--line)", lineHeight: 1, opacity: 0.7,
          }}
        >
          ✏️
        </button>
      )}
      {onDelete && (
        <button
          onClick={onDelete}
          title="Delete this field"
          className="hover-bg ink3"
          style={{
            padding: "1px 6px", borderRadius: 3, fontSize: 11, cursor: "pointer",
            border: "1px solid var(--line)", lineHeight: 1, opacity: 0.7,
          }}
        >
          🗑
        </button>
      )}
    </div>
  );
}


// Category enums must match backend canonical vocab in app/agents/categorizer.py.
// See CATEGORIES.md for the master spec.
const EXPENSE_CATEGORIES = [
  "Meals", "Travel", "Transport", "Utilities", "Subscriptions", "Healthcare",
  "Fitness", "Shopping", "Office", "Entertainment", "Government Fees",
  "Banking Fees", "Cash / Payments", "Tax", "Other",
];
const INCOME_CATEGORIES = [
  "Sales", "Service Revenue", "Consulting", "Subscription Revenue", "Rental",
  "Royalties", "Interest", "Dividends", "Tax Refund", "Reimbursement Received",
  "Grants", "Other Income",
];


// GenericRowEditor — inline editor for a NON-transaction array row: key_facts /
// identifiers / amounts / dates ({label, value}), parties ({name, role}), etc. Renders one
// input per key present, so editing a `{label:"row", value:"CC"}` key-fact edits label/value
// — not the transaction fields TxnRowEditor would (the bug this fixes).
function GenericRowEditor({ row, onCancel, onSave, saving }) {
  const keys = Object.keys(row || {}).filter(k => k !== "_approved" && k !== "page");
  const [vals, setVals] = useState(() => Object.fromEntries(keys.map(k => [k, row?.[k] ?? ""])));
  const [reason, setReason] = useState("");
  const submit = (e) => {
    e?.preventDefault();
    if (saving) return;
    const updates = {};
    keys.forEach(k => { if (String(vals[k] ?? "") !== String(row?.[k] ?? "")) updates[k] = vals[k]; });
    onSave(updates, reason);
  };
  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {keys.map(k => (
        <label key={k} className="row gap-2" style={{ alignItems: "center" }}>
          <span className="ink3" style={{ fontSize: 10, minWidth: 64, textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</span>
          <input value={vals[k]} onChange={(e) => setVals(v => ({ ...v, [k]: e.target.value }))}
                 className="border bg2" style={{ fontSize: 11, padding: "3px 7px", borderRadius: 4, flex: 1, color: "var(--ink)" }} />
        </label>
      ))}
      <input value={reason} onChange={(e) => setReason(e.target.value)}
             placeholder="Reason for edit (audit trail) — optional"
             className="border bg2" style={{ fontSize: 11, padding: "3px 7px", borderRadius: 4, color: "var(--ink)" }} />
      <div className="row gap-2" style={{ justifyContent: "flex-end" }}>
        <button type="button" onClick={onCancel} className="border bg2 hover-bg" style={{ fontSize: 11, padding: "3px 12px", borderRadius: 4, cursor: "pointer" }}>Cancel</button>
        <button type="submit" disabled={saving} className="btn-gold" style={{ fontSize: 11, padding: "3px 14px", borderRadius: 4, cursor: saving ? "default" : "pointer" }}>{saving ? "Saving…" : "Save row"}</button>
      </div>
    </form>
  );
}


// TxnRowEditor — inline per-row editor for transaction items inside an
// array-of-objects field (top_transactions, line_items, items). Lets the
// reviewer edit category (most common HITL override), description, amount,
// date, and direction. Picks the right category enum based on direction.
function TxnRowEditor({ txn, onCancel, onSave, saving, vendorPk }) {
  const direction0 = txn?.direction || "debit";
  const [direction, setDirection] = useState(direction0);
  const [category, setCategory] = useState(txn?.category || "");
  const [description, setDescription] = useState(txn?.description || txn?.merchant || "");
  const [amount, setAmount] = useState(txn?.amount || "");
  const [date, setDate] = useState(txn?.date || "");
  const [reason, setReason] = useState("");

  const pickerMode = direction === "credit" ? "income" : "expense";

  const submit = (e) => {
    e?.preventDefault();
    if (saving) return;
    // Build the diff — only the fields the reviewer actually changed.
    const updates = {};
    if (category !== (txn?.category || "")) updates.category = category;
    if (description !== (txn?.description || "")) updates.description = description;
    if (amount !== (txn?.amount || "")) updates.amount = amount;
    if (date !== (txn?.date || "")) updates.date = date;
    if (direction !== direction0) updates.direction = direction;
    if (Object.keys(updates).length === 0) {
      onCancel?.();
      return;
    }
    onSave?.(updates, reason);
  };

  return (
    <form onSubmit={submit} className="flex col gap-2" style={{ fontSize: 11 }}>
      <div className="row gap-2" style={{ alignItems: "center", flexWrap: "wrap" }}>
        <div className="row gap-1" style={{ alignItems: "center" }}>
          <span className="ink3" style={{ fontSize: 10, minWidth: 60 }}>direction</span>
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            className="bg1 border"
            style={{ padding: "3px 6px", borderRadius: 3, fontSize: 11, color: "var(--ink)" }}
          >
            <option value="debit">debit</option>
            <option value="credit">credit</option>
            <option value="unknown">unknown</option>
          </select>
        </div>
        <div className="row gap-1" style={{ alignItems: "center", flex: 1, minWidth: 200 }}>
          <span className="ink3" style={{ fontSize: 10, minWidth: 60 }}>category</span>
          <CategoryPicker
            mode={pickerMode}
            vendorPk={vendorPk}
            value={category}
            onChange={setCategory}
            style={{ fontSize: 11, padding: "3px 6px" }}
          />
        </div>
        <div className="row gap-1" style={{ alignItems: "center" }}>
          <span className="ink3" style={{ fontSize: 10, minWidth: 40 }}>date</span>
          <input
            value={date}
            onChange={(e) => setDate(e.target.value)}
            placeholder="YYYY-MM-DD"
            className="bg1 border mono"
            style={{ padding: "3px 6px", borderRadius: 3, fontSize: 11, color: "var(--ink)", width: 110 }}
          />
        </div>
        <div className="row gap-1" style={{ alignItems: "center" }}>
          <span className="ink3" style={{ fontSize: 10, minWidth: 50 }}>amount</span>
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="bg1 border mono"
            style={{ padding: "3px 6px", borderRadius: 3, fontSize: 11, color: "var(--ink)", width: 100, textAlign: "right" }}
          />
        </div>
      </div>
      <div className="row gap-1" style={{ alignItems: "center" }}>
        <span className="ink3" style={{ fontSize: 10, minWidth: 60 }}>description</span>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="bg1 border w-full"
          style={{ padding: "3px 8px", borderRadius: 3, fontSize: 11, color: "var(--ink)", width: "100%" }}
        />
      </div>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Reason for edit (audit trail) — e.g. 'category was wrong; this is Office not Shopping'"
        className="bg1 border w-full"
        style={{ padding: "3px 8px", borderRadius: 3, fontSize: 10, color: "var(--ink2)", width: "100%" }}
      />
      <div className="row gap-2" style={{ justifyContent: "flex-end" }}>
        <button type="button" onClick={onCancel} disabled={saving}
                className="border bg2 hover-bg"
                style={{ padding: "3px 10px", borderRadius: 3, fontSize: 10, cursor: "pointer" }}>
          Cancel
        </button>
        <button type="submit" disabled={saving}
                className="btn-gold"
                style={{ padding: "3px 12px", borderRadius: 3, fontSize: 10, cursor: saving ? "wait" : "pointer" }}>
          {saving ? "Saving…" : "Save row"}
        </button>
      </div>
    </form>
  );
}


// ── CategoryPicker · dynamic dropdown with "+ Add new" inline form ───────
//
// Loads {canonical, global custom, vendor-local custom} from /api/categories.
// Vendor-local only loads when vendorPk is given. Reviewer role can add
// vendor-local only; admin/owner can add global too.
//
// Used by ScalarEditor (top-level receipt category) and TxnRowEditor (per-
// transaction category). The list refreshes after a successful create.

function CategoryPicker({ mode, vendorPk, value, onChange, autoFocus, style }) {
  const { user } = useAuth();
  const canAddGlobal = user?.roles?.includes("admin") || user?.roles?.includes("owner");
  const [cats, setCats] = useState(null);
  const [err, setErr] = useState(null);
  const [adding, setAdding] = useState(false);

  const reload = async (selectName) => {
    setErr(null);
    try {
      const list = await fetchCategories({ mode, vendorPk });
      setCats(list);
      if (selectName) onChange(selectName);
    } catch (e) {
      setErr(e.message);
      setCats([]);
    }
  };
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [mode, vendorPk]);

  const handleSelect = (e) => {
    const v = e.target.value;
    if (v === "__add__") { setAdding(true); return; }
    onChange(v);
  };

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <select
        autoFocus={autoFocus}
        value={value || ""}
        onChange={handleSelect}
        disabled={cats === null}
        className="bg1 border"
        style={{
          padding: "4px 8px", borderRadius: 4, fontSize: 12,
          color: "var(--ink)", outline: "none", width: "100%", ...style,
        }}
      >
        <option value="">{cats === null ? "Loading…" : "— pick a category —"}</option>
        {(cats || []).map(c => (
          <option key={`${c.scope}:${c.pk ?? c.name}`} value={c.name}>
            {c.name}
            {c.scope === "vendor"   ? "  ★ vendor-local" :
             c.scope === "global"   ? "  ✦ firm-wide"   : ""}
          </option>
        ))}
        <option value="__add__" style={{ fontStyle: "italic" }}>+ Add new category…</option>
      </select>
      {err && (
        <div className="ink3" style={{ fontSize: 10, marginTop: 4, color: "#D8625E" }}>
          Could not load categories: {err}
        </div>
      )}
      {adding && (
        <AddCategoryForm
          mode={mode}
          vendorPk={vendorPk}
          canAddGlobal={canAddGlobal}
          onCancel={() => setAdding(false)}
          onCreated={async (name) => {
            setAdding(false);
            await reload(name);
          }}
        />
      )}
    </div>
  );
}


function AddCategoryForm({ mode, vendorPk, canAddGlobal, onCancel, onCreated }) {
  const [name, setName] = useState("");
  // Reviewers (canAddGlobal=false) are locked to vendor scope when a vendor
  // is present. When no vendor context (admin viewing an unscoped doc),
  // we force global anyway.
  const defaultScope = vendorPk == null ? "global" : "vendor";
  const [scope, setScope] = useState(defaultScope);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const canSubmit = name.trim().length >= 2 && !busy;

  const submit = async (e) => {
    e?.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setErr(null);
    try {
      const payload = {
        name: name.trim(),
        mode,
        scope,
        ...(scope === "vendor" ? { vendorPk } : {}),
      };
      const created = await createCategory(payload);
      onCreated(created.name);
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="border rounded-md p-2 mt-2"
          style={{ background: "rgba(139,127,214,0.08)", borderColor: "rgba(139,127,214,0.45)", fontSize: 11 }}>
      <div className="upper ink3 mb-2" style={{ fontSize: 10, color: "#8B7FD6", fontWeight: 600 }}>
        Add new {mode} category
      </div>
      <input
        autoFocus
        value={name}
        onChange={e => setName(e.target.value)}
        placeholder="Category name (e.g. Stripe Fees)"
        maxLength={128}
        className="bg1 border"
        style={{
          padding: "4px 8px", borderRadius: 4, fontSize: 12, color: "var(--ink)",
          outline: "none", width: "100%", marginBottom: 6,
        }}
      />
      <div className="row gap-3 mb-2" style={{ alignItems: "center", flexWrap: "wrap" }}>
        <span className="ink3" style={{ fontSize: 10 }}>Scope</span>
        {vendorPk != null && (
          <label className="row" style={{ gap: 4, alignItems: "center", cursor: "pointer", fontSize: 11 }}>
            <input type="radio" name="scope" checked={scope === "vendor"}
                   onChange={() => setScope("vendor")} style={{ accentColor: "#8B7FD6" }}/>
            <span>★ This vendor only</span>
          </label>
        )}
        <label className="row" style={{
          gap: 4, alignItems: "center", cursor: canAddGlobal ? "pointer" : "not-allowed",
          fontSize: 11, opacity: canAddGlobal ? 1 : 0.5,
        }}
               title={!canAddGlobal ? "Only admins or owners can add firm-wide categories" : ""}>
          <input type="radio" name="scope" checked={scope === "global"}
                 onChange={() => setScope("global")}
                 disabled={!canAddGlobal}
                 style={{ accentColor: "#8B7FD6" }}/>
          <span>✦ All vendors (firm-wide)</span>
        </label>
      </div>
      {err && (
        <div style={{ fontSize: 10, color: "#D8625E", marginBottom: 6 }}>{err}</div>
      )}
      <div className="row gap-2" style={{ justifyContent: "flex-end" }}>
        <button type="button" onClick={onCancel} disabled={busy}
                className="border bg2 hover-bg"
                style={{ padding: "3px 10px", borderRadius: 4, fontSize: 10, cursor: "pointer" }}>
          Cancel
        </button>
        <button type="submit" disabled={!canSubmit}
                className="btn-gold"
                style={{ padding: "3px 12px", borderRadius: 4, fontSize: 10, cursor: canSubmit ? "pointer" : "not-allowed" }}>
          {busy ? "Saving…" : "Save category"}
        </button>
      </div>
    </form>
  );
}


function ScalarEditor({ label, value, onCancel, onSave, saving, docType, vendorPk }) {
  const [val, setVal] = useState(value == null ? "" : String(value));
  const [reason, setReason] = useState("");
  const submit = (e) => {
    e?.preventDefault();
    if (saving) return;
    onSave?.(val, reason);
  };
  // Category fields get a dynamic dropdown (with +Add) rather than free-text.
  // Picks expense vs income mode from the doc type so revenue invoices show
  // the income vocab.
  const isIncomeMode = label === "revenue_category"
    || (label === "category" && ["revenue_invoice", "customer_payment", "sales_receipt"].includes(docType));
  const isCategoryField = label === "category" || label === "revenue_category";
  return (
    <form onSubmit={submit} className="border rounded-md p-2 mb-2 mt-1"
          style={{ background: "rgba(200,160,76,0.08)", borderColor: "rgba(200,160,76,0.45)" }}>
      <div className="row mb-2" style={{ gap: 8, alignItems: "center" }}>
        <span className="ink3" style={{ minWidth: 110, fontSize: 11, fontWeight: 600 }}>{label}</span>
        {isCategoryField ? (
          <CategoryPicker
            mode={isIncomeMode ? "income" : "expense"}
            vendorPk={vendorPk}
            value={val}
            onChange={setVal}
            autoFocus
          />
        ) : (
          <input
            autoFocus
            value={val}
            onChange={(e) => setVal(e.target.value)}
            className="bg1 border"
            style={{
              padding: "4px 8px", borderRadius: 4, fontSize: 12, color: "var(--ink)",
              outline: "none", flex: 1,
            }}
          />
        )}
      </div>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Reason for edit (recommended — recorded in audit trail)"
        className="bg1 border w-full"
        style={{
          padding: "4px 8px", borderRadius: 4, fontSize: 11, color: "var(--ink2)",
          outline: "none", width: "100%", marginBottom: 6,
        }}
      />
      <div className="row gap-2" style={{ justifyContent: "flex-end" }}>
        <button type="button" onClick={onCancel} disabled={saving}
                className="border bg2 hover-bg"
                style={{ padding: "3px 10px", borderRadius: 4, fontSize: 10, cursor: "pointer" }}>
          Cancel
        </button>
        <button type="submit" disabled={saving}
                className="btn-gold"
                style={{ padding: "3px 12px", borderRadius: 4, fontSize: 10, cursor: saving ? "wait" : "pointer" }}>
          {saving ? "Saving…" : "Save edit"}
        </button>
      </div>
    </form>
  );
}


// ── Markdown tab ──────────────────────────────────────────────────────────

// Module-level cache · `docId → body` — survives MarkdownTab unmounts so
// switching tabs in DocumentChatPanel doesn't lose the converted markdown
// or trigger a second LLM call (free-tier OpenRouter rate-limits these to
// ~1/min). Bust via `invalidateMarkdownCache(docId)` after Re-extract
// (called from the parent) so stale entries don't survive a re-classify.
// (MARKDOWN_CACHE moved to MarkdownTab.jsx — its only user — after the module split.)

