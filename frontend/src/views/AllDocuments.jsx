import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../components/Icon.jsx";
import { Pill, LoadingState, ErrorState } from "../components/Shell.jsx";
import ErrorBoundary from "./ErrorBoundary.jsx";
import { prettyType } from "../format.js";
import { fetchDocuments, uploadDocument,
         deleteDocument, archiveDocument, rematchDocument,
         attachDocumentToRequirement, fetchSupportedTypes, reviewDocument } from "../api";
import { repullDocument, reclassifyOther, fetchGroups, shareDocToGroup, fetchGroupDocuments, fetchConsent, acceptConsent, searchDocuments, bulkDocuments } from "../api/documents";  // M46 · documents-only api
import { fetchDrivePickerConfig, importDriveFile, fetchIdentityGraph } from "../api";
import GraphTab from "../components/doc-chat/GraphTab.jsx";
import { kindColor, kindLabel } from "../components/doc-chat/graphConstants.js";
import { openDrivePicker } from "../lib/drivePicker.js";
import { useApiResource } from "../api/useApi.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useConfirm } from "../components/ConfirmDialog.jsx";
import DocumentChatPanel from "./DocumentChatPanel.jsx";
import { useIsMobile } from "../useIsMobile.js";
import AlertRuleModal from "../components/AlertRuleModal.jsx";
import AttachRequirementModal from "../components/AttachRequirementModal.jsx";
import DocActions from "../components/DocActions.jsx";
import DocumentDetailPanel from "../components/DocumentDetailPanel.jsx";
import LinkedRequirementsModal from "../components/LinkedRequirementsModal.jsx";


// Format the real upload time (created_at). Relative for recent, absolute date beyond a week.
function fmtUploaded(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const diff = Date.now() - d.getTime(), day = 86400000;
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < day) return `${Math.floor(diff / 3600000)}h ago`;
  if (diff < 7 * day) return `${Math.floor(diff / day)}d ago`;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// localStorage keys for persisted pane widths so user's resize choices
// survive a page refresh.
const LS_CHAT_W    = "docaiq.docchat.chatWidth";


function loadInt(key, fallback) {
  if (typeof localStorage === "undefined") return fallback;
  const v = parseInt(localStorage.getItem(key) || "", 10);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}
function saveInt(key, v) {
  try { if (typeof localStorage !== "undefined") localStorage.setItem(key, String(v)); } catch {}
}

// `docsOverride` lets callers (e.g. the Vendor Portal Documents tab)
// pass a pre-scoped map of documents instead of refetching the whole
// tenant list. When provided, AllDocuments skips its own fetch and
// renders against the override — keyed-by-id_external like /api/documents.

// AllDocuments · the reviewer's cross-vendor flat list of every document
// in the tenant. Today's Documents UI lives inside the per-vendor portal —
// good for working ONE vendor at a time, useless when you've uploaded a
// folder of 200 KYC packets and want to see them all in a table.
//
// Columns:
//   • Filename
//   • Doc type (classifier output, M11.6) — chip + confidence
//   • Status (pending / processing / ready / failed)
//   • Linked requirement count (if matcher attached it to anything)
//   • Extracted-fields preview (KYC Phase 1 output)
//   • Uploaded by + when
//
// Click a row to expand a side panel with full metadata + extracted JSON.

const STATUS_COLORS = {
  pending:    "amber",
  processing: "violet",
  ready:      "green",
  failed:     "rose",
};


export default function AllDocuments({ docsOverride = null, onDocsChanged, vendorPk = null, openDocId = null, onOpenGroups = () => {} }) {
  const { hasRole, config } = useAuth();
  const isDocsProduct = config?.product === "documents";
  // M46 · the Documents tab has scope tabs: "personal" + one per group. The
  // group rail comes from the user's groups (a removed member loses the tab).
  const [myGroups, setMyGroups] = useState([]);
  const reloadGroups = () => { if (isDocsProduct) fetchGroups().then(r => setMyGroups(r.groups || [])).catch(() => {}); };
  useEffect(() => { reloadGroups(); }, [isDocsProduct]);
  const [activeScope, setActiveScope] = useState("personal");  // "personal" | <groupId>
  // If the active group vanished (member removed / group deleted), fall back.
  useEffect(() => {
    if (activeScope !== "personal" && !myGroups.some(g => g.id === activeScope)) setActiveScope("personal");
  }, [myGroups, activeScope]);

  // Scope-aware loader: personal → /documents?scope=personal · group → /groups/{id}/documents.
  const loadScoped = () => {
    if (docsOverride !== null) return Promise.resolve(docsOverride);
    if (!isDocsProduct || activeScope === "personal") {
      return fetchDocuments(isDocsProduct ? "personal" : undefined);
    }
    return fetchGroupDocuments(activeScope).then(r => {
      const m = {}; (r.documents || []).forEach(d => { m[d.id] = d; }); return m;
    });
  };
  const fetched = useApiResource(loadScoped, [docsOverride, activeScope, isDocsProduct]);
  const { data: docsMap, loading, error, setData: setDocsMap } = fetched;
  const reloadDocs = async () => { try { setDocsMap(await loadScoped()); } catch { /* keep */ } };
  const [requirements, setRequirements] = useState([]);
  const [typeFilter, setTypeFilter] = useState("all");
  const [showAddPopup, setShowAddPopup] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const mobile = useIsMobile();
  const [paneW, setPaneW] = useState(920);   // resizable width of the docked detail pane (px)
  const [listCollapsed, setListCollapsed] = useState(false);  // hide the list to give the detail full width
  // Drag the pane's left edge — dragging LEFT grows the pane (shrinks the list) and vice versa.
  const startPaneResize = (e) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = paneW;
    const move = (ev) => setPaneW(Math.max(440, Math.min(window.innerWidth - 360, w0 + (x0 - ev.clientX))));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.cursor = ""; document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none";
  };

  const confirmDialog = useConfirm();
  // M29.2 · per-doc action state. canAct gates the icon column to users
  // who can mutate; vendors don't see it.
  const canAct = hasRole("admin", "reviewer");
  // Vendors can delete their OWN uploads (e.g. uploaded by mistake). The
  // backend restricts a vendor to their own docs via the M17 vendor clause,
  // so showing Delete here is safe; Re-match / Attach stay admin/reviewer-only.
  const canDelete = hasRole("admin", "reviewer", "vendor");
  // { docId } when the attach-to-requirement picker is open.
  const [attachFor, setAttachFor] = useState(null);
  // Per-doc transient flag while an action is in flight — disables that
  // doc's icons so double-clicks can't fire the same mutation twice.
  const [busyDocId, setBusyDocId] = useState(null);
  // Toast feedback after async actions (rematch returns 202 + nothing
  // visible until the worker drains; reviewer needs confirmation it fired).
  const [toast, setToast] = useState(null);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  // Mutators · helpers wired to each action icon.
  const updateDoc = (id, patch) => {
    if (docsOverride !== null) onDocsChanged?.(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }));
    else setDocsMap?.(prev => ({ ...(prev || {}), [id]: { ...(prev?.[id] || {}), ...patch } }));
  };
  const removeDoc = (id) => {
    if (docsOverride !== null) onDocsChanged?.(prev => { const n = { ...prev }; delete n[id]; return n; });
    else setDocsMap?.(prev => { const n = { ...(prev || {}) }; delete n[id]; return n; });
  };
  const handleDelete = async (d) => {
    const ok = await confirmDialog({
      title: `Delete "${d.name}"?`,
      body: "Hard delete · removes the file + cascades orphan refs. If a closed audit references it, you'll be offered to archive instead.",
      confirmLabel: "Delete document",
      destructive: true,
    });
    if (!ok) return;
    setBusyDocId(d.id);
    try {
      await deleteDocument(d.id);
      removeDoc(d.id);
      setToast({ kind: "ok", text: `Deleted "${d.name}".` });
    } catch (e) {
      const isClosedRef = e?.status === 409 && e?.body?.detail?.code === "doc_referenced_by_closed_audit";
      if (isClosedRef) {
        const refs = e.body.detail.closedAudits || [];
        const archiveOk = await confirmDialog({
          title: "Hard-delete refused — archive instead?",
          body: `This doc is referenced by ${refs.length} closed audit${refs.length === 1 ? "" : "s"} (${refs.slice(0, 3).join(", ")}). Archive hides it without breaking history.`,
          confirmLabel: "Archive document",
        });
        if (archiveOk) {
          const updated = await archiveDocument(d.id);
          updateDoc(d.id, updated);
          setToast({ kind: "ok", text: `Archived "${d.name}".` });
        }
      } else {
        setToast({ kind: "err", text: e.message });
      }
    } finally {
      setBusyDocId(null);
    }
  };
  // M46 · re-pull a connector doc's original after a retention purge.
  const handleRepull = async (d) => {
    setBusyDocId(d.id);
    try {
      const updated = await repullDocument(d.id);
      updateDoc(d.id, updated);
      setToast({ kind: "ok", text: `Re-pulled the original of "${d.name}".` });
    } catch (e) {
      setToast({ kind: "err", text: e.message });
    } finally {
      setBusyDocId(null);
    }
  };
  const handleRematch = async (d) => {
    setBusyDocId(d.id);
    try {
      await rematchDocument(d.id);
      setToast({ kind: "ok", text: `Matcher re-queued for "${d.name}". Watch the Reqs matched column refresh as it drains.` });
    } catch (e) {
      setToast({ kind: "err", text: e.message });
    } finally {
      setBusyDocId(null);
    }
  };
  const handleAttachConfirm = async (docId, requirementId) => {
    setBusyDocId(docId);
    try {
      const updated = await attachDocumentToRequirement(docId, requirementId);
      updateDoc(docId, updated);
      // Locally bump the requirement so reqsByDoc rebuilds with this link.
      setRequirements?.(prev => (prev || []).map(r =>
        r.id === requirementId ? { ...r, docId, status: r.status === "todo" || r.status === "miss" ? "warn" : r.status } : r
      ));
      setAttachFor(null);
      setToast({ kind: "ok", text: `Attached "${updated.name}" to ${requirementId}.` });
    } catch (e) {
      setToast({ kind: "err", text: e.message });
    } finally {
      setBusyDocId(null);
    }
  };

  // Upload state · drives the button label + progress chip.
  const fileInputRef = useRef(null);
  const [uploadProgress, setUploadProgress] = useState(null); // { done, total, errors[] } | null
  const isUploading = uploadProgress !== null && uploadProgress.done < uploadProgress.total;

  // M46 · supported upload formats (single source of truth from the backend) —
  // drives the file-picker accept filter + the "Supported formats" hint.
  const [supported, setSupported] = useState(null);
  useEffect(() => { fetchSupportedTypes().then(setSupported).catch(() => {}); }, []);
  const acceptAttr = supported?.accept
    || "application/pdf,.pdf,.docx,.xlsx,.csv,.txt,.eml,image/*";

  // Floating overlay closes on ESC for a familiar keyboard shortcut.
  useEffect(() => {
    if (!selectedId) return;
    const onKey = (e) => { if (e.key === "Escape") { setSelectedId(null); setListCollapsed(false); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId]);

  // Deep-link: when opened with a target document (e.g. from an Intelligence
  // alert), open that document's panel. `selected` resolves once docs load.
  useEffect(() => {
    if (openDocId) setSelectedId(openDocId);
  }, [openDocId]);

  // Linked tab: clicking a duplicate / related document opens it in place.
  useEffect(() => {
    const onSelect = (e) => { if (e?.detail?.docId) setSelectedId(e.detail.docId); };
    window.addEventListener("docaiq:select-doc", onSelect);
    return () => window.removeEventListener("docaiq:select-doc", onSelect);
  }, []);

  // Poll-for-status logic lives further down, after `docs` is computed —
  // it depends on `docs.some(...)` and the const declaration order matters
  // (referencing `docs` before its declaration throws ReferenceError and
  // crashes the component).

  // Merge a freshly-uploaded doc into both this component's local cache AND
  // (if VendorPortal passed it) the parent's documents map.
  const mergeDoc = (newDoc) => {
    const merger = (prev) => ({ ...(prev || {}), [newDoc.id]: newDoc });
    if (docsOverride !== null) {
      onDocsChanged?.(merger);   // parent owns the state; notify
    }
    setDocsMap?.(merger);        // keep local view in sync immediately
  };

  const handleUploadClick = () => fileInputRef.current?.click();

  // M53 · Import from Drive (Google Picker). drive.file scope can't see files the
  // user dropped into Drive directly — the Picker lets them explicitly grant + import one.
  const [importing, setImporting] = useState(false);
  const handleImportFromDrive = async () => {
    if (importing) return;
    setImporting(true);
    try {
      const cfg = await fetchDrivePickerConfig();
      if (!cfg?.enabled) {
        setToast({ kind: "err", text: "Import from Drive isn't set up yet (admin: add a Picker API key)." });
        return;
      }
      const fileId = await openDrivePicker(cfg);
      if (!fileId) return;  // cancelled
      setToast({ kind: "ok", text: "Importing from Drive…" });
      const r = await importDriveFile(fileId);
      setToast({ kind: "ok", text: r.status === "exists"
        ? `"${r.name}" is already in your documents.`
        : `Importing "${r.name}" — processing…` });
      reloadDocs();
    } catch (e) {
      setToast({ kind: "err", text: (e && e.message) || "Drive import failed — is Google Drive connected?" });
    } finally {
      setImporting(false);
    }
  };

  // A5 · semantic content search across the user's docs.
  const [searchResults, setSearchResults] = useState(null);  // null = idle
  const [searching, setSearching] = useState(false);
  const runContentSearch = async (q) => {
    if (!q.trim()) { setSearchResults(null); return; }
    setSearching(true);
    try { const r = await searchDocuments(q.trim()); setSearchResults(r.results || []); }
    catch { setSearchResults([]); }
    finally { setSearching(false); }
  };

  // Unified search: toggles between content search ("Search contents…") and
  // cross-document entity graph ("Entity graph…"). One input, two modes.
  const searchInputRef = useRef(null);
  const [searchMode, setSearchMode] = useState("content"); // "content" | "graph"
  const [entityGraphLoading, setEntityGraphLoading] = useState(false);
  const [entityGraphData, setEntityGraphData] = useState(null);
  const [entityGraphError, setEntityGraphError] = useState(null);
  const runEntityGraphSearch = async (q) => {
    if (!q.trim()) return;
    setEntityGraphLoading(true);
    setEntityGraphError(null);
    setEntityGraphData(null);
    setGraphSelectedEntity(null);
    try {
      const result = await fetchIdentityGraph(q.trim());
      if (result.found) {
        setEntityGraphData(result);
      } else {
        setEntityGraphError(`No entity matching "${q.trim()}" found across your documents.`);
      }
    } catch (err) {
      setEntityGraphError(err.message || "Search failed");
    }
    setEntityGraphLoading(false);
  };
  const [graphSelectedEntity, setGraphSelectedEntity] = useState(null);
  // Re-center the graph on a different entity (clicked from the detail card)
  const handleReCenter = (entityName) => {
    setGraphSelectedEntity(null);
    runEntityGraphSearch(entityName);
  };
  const handleUnifiedSearch = (q) => {
    if (searchMode === "graph") {
      // Graph mode: open the docked panel with GraphTab (handles its own search)
      setEntityGraphData({ _open: true });
      setEntityGraphError(null);
      return;
    }
    runContentSearch(q);
  };

  const handleFilesSelected = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";   // reset so the same file can be re-selected later
    if (!files.length) return;

    // §compliance · one-time acknowledgement before the first upload that
    // documents may contain personal / health data.
    if (isDocsProduct) {
      try {
        const c = await fetchConsent();
        if (!c.personalData) {
          const ok = await confirmDialog({
            title: "Before you upload",
            body: "Your documents may contain personal or special-category (health) data. DocAIQuest processes them to extract and answer questions; redacted text may be sent to AI providers. By continuing you acknowledge this and consent to that processing.",
            confirmLabel: "I acknowledge & continue",
          });
          if (!ok) return;
          await acceptConsent("personal_data");
        }
        // Free plan only · uploads may be used to improve our AI models.
        if (c.modelTrainingRequired) {
          const ok = await confirmDialog({
            title: "Free plan — how your data is used",
            body: "On the free plan, your uploaded documents may be used to help improve DocAIQuest's AI models (for example, learning better field schemas). Paid plans keep your data private and are never used for training. By continuing on the free plan you consent to this use.",
            confirmLabel: "I agree — continue on Free",
          });
          if (!ok) return;
          await acceptConsent("model_training");
        }
      } catch { /* if the consent check fails, the backend still gates the upload */ }
    }

    // 4-way parallel uploads, matching the existing folder-upload behaviour.
    const CONCURRENCY = 4;
    const total = files.length;
    let done = 0;
    const errors = [];
    setUploadProgress({ done: 0, total, errors: [] });
    let cursor = 0;
    const runner = async () => {
      while (cursor < total) {
        const myIdx = cursor++;
        const file = files[myIdx];
        try {
          const created = await uploadDocument(file, { vendorPk });
          mergeDoc({
            // Newly-uploaded docs come back without all the optional fields
            // populated yet (status will go pending → processing → ready
            // via the worker). Fill defaults so the row renders cleanly.
            ingestionStatus: "pending",
            docType: null,
            ...created,
          });
        } catch (err) {
          errors.push({ file: file.name, message: err.message });
        }
        done++;
        setUploadProgress(p => p && ({ ...p, done, errors }));
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, total) }, runner),
    );
    // Leave the success badge visible for a moment so the user sees it land.
    setTimeout(() => setUploadProgress(null), 3000);
  };

  // /api/documents returns an object keyed by id_external. Flatten + sort newest first.
  const docs = useMemo(() => {
    if (!docsMap) return [];
    return Object.entries(docsMap).map(([id, d]) => ({ ...d, id })).reverse();
  }, [docsMap]);

  // M46 · self-learning classification · suggest re-typing the "other" docs.
  const otherDocs = useMemo(
    () => docs.filter(d => !d.docType || ["other", "unknown", "document"].includes(d.docType)),
    [docs]);
  const [reclassifying, setReclassifying] = useState(false);
  const [reclassMsg, setReclassMsg] = useState(null);
  const onReclassifyOther = async () => {
    setReclassifying(true); setReclassMsg(null);
    try {
      const r = await reclassifyOther();
      setReclassMsg(`Re-classified ${r.reclassified} of ${r.scanned} document${r.scanned === 1 ? "" : "s"}.`);
      await reloadDocs();
    } catch (e) {
      setReclassMsg(e.message || "Re-classify failed");
    } finally { setReclassifying(false); }
  };

  // M46 · share a doc into one or more groups (checkbox multi-select). The menu
  // is rendered in a portal with position:fixed so the doc-list's overflow:auto
  // ancestors can't clip it.
  const [shareForDoc, setShareForDoc] = useState(null);     // doc id whose picker is open
  const [shareDraft, setShareDraft] = useState([]);         // checked group ids in the open picker
  const [sharePos, setSharePos] = useState(null);           // {top, right} viewport coords
  const [shareSaving, setShareSaving] = useState(false);
  const openSharePicker = (d, e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setSharePos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    setShareForDoc(d.id);
    setShareDraft(d.groupIds || []);
  };
  // Close the floating picker on scroll/resize (fixed menu won't follow flow).
  useEffect(() => {
    if (!shareForDoc) return;
    const close = () => setShareForDoc(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); };
  }, [shareForDoc]);
  const toggleShareGroup = (gid) =>
    setShareDraft(prev => prev.includes(gid) ? prev.filter(x => x !== gid) : [...prev, gid]);
  const onShareToGroup = async (docId) => {
    setShareSaving(true);
    try {
      await shareDocToGroup(docId, shareDraft);
      setShareForDoc(null);
      setToast({ kind: "ok", text: shareDraft.length ? "Sharing updated." : "Removed from all groups." });
      reloadGroups();        // doc counts changed
      await reloadDocs();    // membership may have changed the current scope
    } catch (e) { setToast({ kind: "err", text: e.message || "Share failed" }); }
    finally { setShareSaving(false); }
  };
  // A4 · bulk selection + actions.
  const [bulkSel, setBulkSel] = useState(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [alertRuleOpen, setAlertRuleOpen] = useState(false);
  const [alertDoc, setAlertDoc] = useState(null); // single doc for per-row alert creation
  const actIcon = { width: 30, height: 30, borderRadius: 6, cursor: "pointer", display: "grid", placeItems: "center", padding: 0 };
  const toggleSelect = (id) => setBulkSel(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const clearSelection = () => setBulkSel(new Set());
  const runBulk = async (action, groupId) => {
    const ids = [...bulkSel];
    if (!ids.length) return;
    if (action === "delete") {
      const ok = await confirmDialog({ title: `Delete ${ids.length} document(s)?`, body: "This permanently deletes the selected documents and their chat/extractions. Drive originals are untouched.", confirmLabel: "Delete", destructive: true });
      if (!ok) return;
    }
    setBulkBusy(true);
    try {
      const r = await bulkDocuments(action, ids, groupId);
      setToast({ kind: "ok", text: `${action === "delete" ? "Deleted" : action === "share" ? "Shared" : "Reclassified"} ${r.count} document(s).` });
      clearSelection();
      reloadGroups();
      await reloadDocs();
    } catch (e) { setToast({ kind: "err", text: e.message || "Bulk action failed" }); }
    finally { setBulkBusy(false); }
  };
  // M46 · approve a processed doc as accurate (the gate before sharing).
  const onApprove = async (d) => {
    setBusyDocId(d.id);
    try {
      const updated = await reviewDocument(d.id, { status: "reviewed" });
      updateDoc(d.id, updated);
      setToast({ kind: "ok", text: `Approved "${d.name}" as accurate.` });
    } catch (e) { setToast({ kind: "err", text: e.message || "Approve failed" }); }
    finally { setBusyDocId(null); }
  };

  // Poll for status transitions whenever any doc is still pending or
  // processing. The worker flips pending → processing → ready (or failed)
  // asynchronously; without this poll the UI shows whatever status was
  // optimistically set at upload and never refreshes. Stops itself when
  // all docs are in terminal states.
  const hasInFlight = useMemo(
    () => docs.some(d => d.ingestionStatus === "pending" || d.ingestionStatus === "processing"),
    [docs],
  );
  // Ref tracks the previous fetch result so the diff compares to the LAST
  // fetched data, not whatever stale closure value the effect captured at
  // its mount. Without this the diff sees stale state, always reports
  // changed, and the flicker comes back.
  const lastFetchRef = useRef(null);
  useEffect(() => {
    if (!hasInFlight) return;
    if (docsOverride !== null && !onDocsChanged) return;
    let cancelled = false;
    // Diff helper · returns true iff any doc's interesting fields changed.
    // Skipping setState when nothing changed preserves object identity for
    // every consumer (chat panel, expenses tab, the floating overlay), so
    // they don't re-render every 4s and the +Upload button stops flickering.
    const docChanged = (prev, next) => {
      const a = prev || {};
      const b = next || {};
      const aKeys = Object.keys(a);
      const bKeys = Object.keys(b);
      if (aKeys.length !== bKeys.length) return true;
      for (const k of bKeys) {
        const x = a[k], y = b[k];
        if (!x) return true;
        if (x.ingestionStatus !== y.ingestionStatus) return true;
        if (x.docType !== y.docType) return true;
        if ((x.docTypeConfidence || 0) !== (y.docTypeConfidence || 0)) return true;
        if (JSON.stringify(x.extractedFields) !== JSON.stringify(y.extractedFields)) return true;
      }
      return false;
    };
    const tick = async () => {
      try {
        const fresh = await loadScoped();
        if (cancelled) return;
        if (!docChanged(lastFetchRef.current, fresh)) return;
        lastFetchRef.current = fresh;
        if (docsOverride !== null) onDocsChanged?.(() => fresh);
        else setDocsMap?.(() => fresh);
      } catch {
        /* transient — next tick will retry */
      }
    };
    const handle = setInterval(tick, 4000);
    tick();   // fire an immediate refresh too
    return () => { cancelled = true; clearInterval(handle); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasInFlight, docsOverride]);

  // Build doc_id → list of matched-requirement records (id, title, status,
  // confidence). The full object — not just the id — so the inline popover
  // can show titles + confidence without a second fetch.
  const reqsByDoc = useMemo(() => {
    const m = new Map();
    for (const r of (requirements || [])) {
      if (!r.docId) continue;
      if (!m.has(r.docId)) m.set(r.docId, []);
      m.get(r.docId).push({
        id: r.id,
        title: r.title,
        status: r.status,
        confidence: r.confidence,
        group: r.group,
      });
    }
    return m;
  }, [requirements]);

  // Which doc's "Reqs matched" popover is currently open. Single-target —
  // opening another closes the first. Null = nothing open.
  const [openReqsFor, setOpenReqsFor] = useState(null);
  // M51 · active tag filter (click a tag chip to narrow the list to that tag).
  const [tagFilter, setTagFilter] = useState(null);

  // Doc-type filter chip set (distinct types from the actual data).
  const docTypes = useMemo(() => {
    const set = new Set();
    for (const d of docs) {
      if (d.docType) set.add(d.docType);
    }
    return ["all", "unclassified", ...Array.from(set).sort()];
  }, [docs]);

  if (loading) return <LoadingState label="Loading documents…" />;
  if (error) return <ErrorState message={error} />;

  const filtered = docs.filter(d => {
    if (typeFilter === "unclassified") {
      if (d.docType) return false;
    } else if (typeFilter !== "all") {
      if (d.docType !== typeFilter) return false;
    }
    if (tagFilter && !(d.tags || []).some(t => t === tagFilter)) return false;
    // Content search filter — when search results are active, only show matched docs
    if (searchResults !== null && !searchResults.some(r => r.docId === d.id)) return false;
    return true;
  });

  const selected = selectedId ? docs.find(d => d.id === selectedId) : null;

  // M46 · floating share-to-groups menu (portal + fixed so overflow:auto
  // ancestors can't clip it). Anchored to the clicked share button.
  const sharePicker = (isDocsProduct && shareForDoc && sharePos) ? createPortal(
    <>
      <div onClick={() => setShareForDoc(null)} style={{ position: "fixed", inset: 0, zIndex: 60 }} />
      <div className="bg1 border rounded-md" onClick={(e) => e.stopPropagation()}
        style={{ position: "fixed", top: sharePos.top, right: sharePos.right, zIndex: 61, minWidth: 200, padding: 6, boxShadow: "0 8px 24px rgba(0,0,0,.4)" }}>
        <div className="ink3" style={{ fontSize: 10, padding: "2px 6px 6px" }}>Share to groups</div>
        <div style={{ maxHeight: 240, overflowY: "auto" }}>
          {myGroups.map(g => (
            <label key={g.id} className="row gap-2 hover-bg" style={{ alignItems: "center", padding: "6px 6px", borderRadius: 5, fontSize: 12, cursor: "pointer" }}>
              <input type="checkbox" checked={shareDraft.includes(g.id)} onChange={() => toggleShareGroup(g.id)}
                style={{ accentColor: "var(--gold2)", cursor: "pointer" }} />
              <Icon name="users" size={11}/>
              <span className="truncate" style={{ color: "var(--ink)" }}>{g.name}</span>
            </label>
          ))}
        </div>
        <div className="row between" style={{ alignItems: "center", marginTop: 6, paddingTop: 6, borderTop: "1px solid var(--line)" }}>
          <button onClick={() => setShareForDoc(null)} className="ink3"
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11 }}>Cancel</button>
          <button onClick={() => onShareToGroup(shareForDoc)} disabled={shareSaving} className="btn-gold"
            style={{ padding: "4px 12px", borderRadius: 6, fontSize: 11, opacity: shareSaving ? 0.6 : 1 }}>
            {shareSaving ? "Saving…" : "Apply"}
          </button>
        </div>
      </div>
    </>, document.body) : null;

  return (
    <div className="grow overflow-auto">
      {sharePicker}
      <div style={{
        maxWidth: selected ? "none" : 1500, margin: "0 auto",
        paddingTop: mobile ? 12 : 16, paddingBottom: mobile ? 16 : 24, paddingLeft: mobile ? 12 : 24,
        paddingRight: ((selected || entityGraphData) && !mobile) ? (listCollapsed ? 0 : paneW + 24) : (mobile ? 12 : 24),
      }}>
        {!mobile && (
          <div className="mb-3">
            <h1 className="serif font-semibold tracking-tight" style={{ fontSize: 22, lineHeight: 1.1, margin: 0 }}>
              All documents
              {!selected && (
                <span className="ink3 font-normal ml-3" style={{ fontSize: 13 }}>
                  Classified on upload · click a row to chat with it
                </span>
              )}
            </h1>
          </div>
        )}
        <div className="row mb-3" style={{ alignItems: "center", gap: 8, flexWrap: mobile ? "nowrap" : "wrap" }}>
          <input
            type="file"
            ref={fileInputRef}
            multiple
            accept={acceptAttr}
            style={{ display: "none" }}
            onChange={handleFilesSelected}
          />
          {uploadProgress && (
            <span className="ink2 text-xs mono"
                  style={{ background: "var(--bg2)", border: "1px solid var(--line)", padding: "4px 10px", borderRadius: 14 }}>
              {isUploading
                ? `Uploading ${uploadProgress.done}/${uploadProgress.total}…`
                : `✓ ${uploadProgress.total - uploadProgress.errors.length} uploaded${uploadProgress.errors.length ? `, ${uploadProgress.errors.length} failed` : ""}`}
            </span>
          )}
          {/* Search row: [🔍 Content | 🕸 Entity] + input — one row, both modes */}
          {isDocsProduct && !mobile && !selected && (
            <span style={{ position: "relative", flex: "0 1 auto", display: "flex", alignItems: "center", gap: 6 }}>
              <span className="row" style={{ gap: 2, background: "var(--bg2)", borderRadius: 8, padding: 2, flexShrink: 0 }}>
                <button onClick={() => { setSearchMode("content"); setSearchResults(null); setEntityGraphData(null); setEntityGraphError(null); }}
                  style={{
                    padding: "4px 10px", borderRadius: 6, fontSize: 11, cursor: "pointer",
                    background: searchMode === "content" ? "var(--gold2)" : "transparent",
                    color: searchMode === "content" ? "var(--ink)" : "var(--ink3)",
                    border: "none", fontWeight: searchMode === "content" ? 600 : 400,
                    whiteSpace: "nowrap",
                  }}>
                  🔍 Content
                </button>
                <button onClick={() => { setSearchMode("graph"); setSearchResults(null); setEntityGraphError(null); setEntityGraphData({ _open: true }); }}
                  style={{
                    padding: "4px 10px", borderRadius: 6, fontSize: 11, cursor: "pointer",
                    background: searchMode === "graph" ? "var(--gold2)" : "transparent",
                    color: searchMode === "graph" ? "var(--ink)" : "var(--ink3)",
                    border: "none", fontWeight: searchMode === "graph" ? 600 : 400,
                    whiteSpace: "nowrap",
                  }}>
                  🕸 Entities
                </button>
              </span>
              {searchMode === "graph" ? (
                <button
                  onClick={() => { setEntityGraphData({ _open: true }); setEntityGraphError(null); }}
                  className="btn-gold"
                  style={{ padding: "8px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer", border: "none", lineHeight: 1, whiteSpace: "nowrap" }}>
                  🕸 Open Entity Graph →
                </button>
              ) : (
                <>
                  <input
                    ref={searchInputRef}
                    type="text"
                    placeholder="Search contents…"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleUnifiedSearch(e.currentTarget.value);
                      if (e.key === "Escape") { setSearchResults(null); setEntityGraphData(null); setEntityGraphError(null); }
                    }}
                    className="bg1 border"
                    style={{ padding: "8px 10px", borderRadius: "6px 0 0 6px", fontSize: 12, color: "var(--ink)", outline: "none", width: 160 }}
                  />
                  <button
                    onClick={() => handleUnifiedSearch(searchInputRef.current?.value || "")}
                    className="btn-gold"
                    style={{ padding: "8px 10px", borderRadius: "0 6px 6px 0", fontSize: 12, cursor: "pointer", border: "none", lineHeight: 1 }}
                title="Search">
                →
              </button>
              </>
              )}
              {entityGraphLoading && (
                <span style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)" }}>
                  <span className="ink3" style={{ fontSize: 10 }}>…</span>
                </span>
              )}
              {searchResults !== null && !searching && (
                <span className="row" style={{ gap: 6, alignItems: "center", flexShrink: 0 }}>
                  <span style={{ fontSize: 11, color: "var(--gold2)", fontWeight: 600, whiteSpace: "nowrap" }}>
                    {searchResults.length} result{searchResults.length !== 1 ? "s" : ""}
                  </span>
                  <button onClick={() => { setSearchResults(null); setSearching(false); }}
                    style={{ padding: "2px 8px", borderRadius: 4, fontSize: 10, cursor: "pointer",
                      background: "transparent", border: "1px solid var(--line)", color: "var(--ink3)", whiteSpace: "nowrap" }}>
                    ✕ Clear
                  </button>
                </span>
              )}
              {searching && (
                <span style={{ fontSize: 10, color: "var(--ink3)", flexShrink: 0 }}>Searching…</span>
              )}
            </span>
          )}
          {/* Type dropdown */}
          {docs.length > 0 && (
            <span style={{ flexShrink: 0 }}>
              <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
                className="bg2 border"
                style={{ padding: "7px 8px", borderRadius: 6, fontSize: 11, color: "var(--ink)", outline: "none", cursor: "pointer", maxWidth: 130 }}>
                <option value="all">All types</option>
                <option value="unclassified">Unclassified</option>
                {docTypes.filter(t => t !== "all" && t !== "unclassified").map(t => (
                  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
                ))}
              </select>
            </span>
          )}
          {/* Add documents — merged Upload + Import, opens a popup */}
          <span style={{ position: "relative", flexShrink: 0 }}>
            <button
              onClick={() => setShowAddPopup(v => !v)}
              className="btn-gold row gap-2"
              style={{ padding: mobile ? "10px 14px" : (selected ? "8px 10px" : "8px 14px"), borderRadius: 6, fontSize: 12, cursor: "pointer", whiteSpace: "nowrap" }}
              title="Add documents"
            >
              <Icon name="plus" size={13}/>
              {!selected && (mobile ? "+" : "Add documents")}
            </button>
            {showAddPopup && (
              <div className="bg1 border rounded-lg" style={{
                position: "absolute", top: "110%", left: 0, zIndex: 25, width: 280,
                padding: 12, boxShadow: "0 12px 32px rgba(0,0,0,.45)", display: "flex", flexDirection: "column", gap: 10,
              }}>
                {/* Supported formats */}
                {supported?.types && (
                  <div>
                    <div className="ink3 upper" style={{ fontSize: 9, marginBottom: 5, letterSpacing: ".05em" }}>Supported formats</div>
                    <div className="row gap-1" style={{ flexWrap: "wrap" }}>
                      {supported.types.map(t => (
                        <span key={t.label} className="mono"
                          style={{ fontSize: 9, padding: "2px 7px", borderRadius: 8, background: "var(--bg2)", border: "1px solid var(--line)", color: "var(--ink2)" }}>
                          {t.label}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div style={{ borderTop: "1px solid var(--line)" }} />
                {/* Browse local files */}
                <button
                  onClick={() => { setShowAddPopup(false); handleUploadClick(); }}
                  disabled={isUploading}
                  className="bg2 border hover-bg row gap-2"
                  style={{ padding: "8px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer", textAlign: "left", width: "100%", color: "var(--ink)", alignItems: "center" }}>
                  <Icon name="upload" size={14}/>
                  <span>Browse files</span>
                  <span className="ink4" style={{ fontSize: 10, flex: 1, textAlign: "right" }}>PDF, images, CSV…</span>
                </button>
                {/* Import from Drive */}
                {isDocsProduct && (
                  <button
                    onClick={() => { setShowAddPopup(false); handleImportFromDrive(); }}
                    disabled={importing}
                    className="bg2 border hover-bg row gap-2"
                    style={{ padding: "8px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer", textAlign: "left", width: "100%", color: "var(--ink)", alignItems: "center" }}>
                    <Icon name="cloud" size={14}/>
                    <span>Import from Google Drive</span>
                  </button>
                )}
              </div>
            )}
          </span>
          {isDocsProduct && !mobile && (
            <span className="row" style={{ alignItems: "stretch", border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
              <a href="/api/documents/extractions/export?format=xlsx" download
                 className="bg2 row gap-2" title="Export all extracted fields as an Excel workbook"
                 style={{ padding: selected ? "8px 10px" : "8px 12px", fontSize: 12, cursor: "pointer", textDecoration: "none", color: "var(--ink2)" }}>
                <Icon name="download" size={12}/>{!selected && "Export Excel"}
              </a>
              <a href="/api/documents/extractions/export?format=csv" download
                 className="bg2" title="Export all extracted fields as CSV"
                 style={{ padding: "8px 10px", fontSize: 12, cursor: "pointer", textDecoration: "none", color: "var(--ink3)", borderLeft: "1px solid var(--line)" }}>
                CSV
              </a>
            </span>
          )}
        </div>

        {/* M46 · self-learning · suggest re-typing the uncategorized docs (hidden in
            the split view — the narrow list keeps the toolbar to a couple of lines) */}
        {isDocsProduct && otherDocs.length > 0 && !selected && !mobile && (
          <div className="bg2 border rounded-md row between" style={{ alignItems: "center", padding: "9px 14px", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
            <span className="ink2" style={{ fontSize: 12 }}>
              <Icon name="sparkle" size={12} style={{ color: "var(--gold2)" }}/> <b>{otherDocs.length}</b> document{otherDocs.length === 1 ? " is" : "s are"} uncategorized — let AI re-classify {otherDocs.length === 1 ? "it" : "them"} from {otherDocs.length === 1 ? "its" : "their"} content.
            </span>
            <div className="row gap-3" style={{ alignItems: "center" }}>
              {reclassMsg && <span className="ink3" style={{ fontSize: 11 }}>{reclassMsg}</span>}
              <button onClick={onReclassifyOther} disabled={reclassifying} className="btn-gold"
                style={{ padding: "6px 12px", borderRadius: 6, fontSize: 12, opacity: reclassifying ? 0.6 : 1 }}>
                {reclassifying ? "Re-classifying…" : "Re-classify with AI"}
              </button>
            </div>
          </div>
        )}


        {/* M46 · scope tabs — Personal + one per group. The list + type filter
            below follow the selected tab. Hidden in split view (a doc is open) to
            keep the narrow left panel's header to a single compact row. */}
        {isDocsProduct && !selected && (
          <div className="row gap-2 mb-3" style={{ flexWrap: "wrap", alignItems: "center", borderBottom: "1px solid var(--line)", paddingBottom: 10 }}>
            <button onClick={() => setActiveScope("personal")}
              className={activeScope === "personal" ? "btn-gold" : "border bg2"}
              style={{ padding: "5px 14px", borderRadius: 16, fontSize: 12, cursor: "pointer" }}>Personal</button>
            {myGroups.map(g => (
              <button key={g.id} onClick={() => setActiveScope(g.id)}
                className={activeScope === g.id ? "btn-gold" : "border bg2"}
                style={{ padding: "5px 12px", borderRadius: 16, fontSize: 12, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5 }}>
                <Icon name="users" size={11}/>{g.name}
              </button>
            ))}
          </div>
        )}

        {docs.length === 0 && (
          <div className="bg1 border rounded-xl p-6 ink3 text-sm" style={{ textAlign: "center" }}>
            No documents yet. Click <strong className="ink2">Upload</strong> above to add documents (PDF, image, or CSV) — they’ll be parsed, extracted, and ready to chat with.
          </div>
        )}

        {isDocsProduct && bulkSel.size > 0 && (
          <div className="row gap-1 bg2 border rounded-lg" style={{ alignItems: "center", padding: "6px 10px", marginBottom: 8, flexWrap: "wrap" }}>
            <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: "var(--ink2)", marginRight: 6 }}>{bulkSel.size}</span>
            <button onClick={() => setAlertRuleOpen(true)} disabled={bulkBusy} title="Create alert rule"
              className="border bg1 hover-bg" style={actIcon}>
              <Icon name="bell" size={14} style={{ color: "var(--gold2)" }} />
            </button>
            <button onClick={() => runBulk("delete")} disabled={bulkBusy} title="Delete selected"
              className="border bg1 hover-bg" style={actIcon}>
              <Icon name="x" size={15} style={{ color: "var(--rose)" }} />
            </button>
            <button onClick={() => runBulk("reclassify")} disabled={bulkBusy} title="Reclassify"
              className="border bg1 hover-bg" style={actIcon}>
              <Icon name="refresh" size={14} />
            </button>
            {myGroups.length > 0 && (
              <select disabled={bulkBusy} defaultValue="" title="Share to group"
                onChange={(e) => { if (e.target.value) { runBulk("share", Number(e.target.value)); e.target.value = ""; } }}
                className="bg1 border" style={{ padding: "5px 6px", borderRadius: 6, fontSize: 12, color: "var(--ink)", cursor: "pointer", minWidth: 28 }}>
                <option value=""><Icon name="users" size={13} /></option>
                {myGroups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
            )}
            <button onClick={clearSelection} title="Clear selection"
              className="border bg1 hover-bg" style={{ ...actIcon, marginLeft: "auto" }}>
              <Icon name="x" size={14} style={{ color: "var(--ink3)" }} />
            </button>
          </div>
        )}

        {docs.length > 0 && (
          <div style={{ display: "block" }}>
            {tagFilter && (
              <div className="row gap-2 mb-2" style={{ alignItems: "center", fontSize: 12 }}>
                <span className="ink3">Filtering by tag</span>
                <span className="btn-gold" style={{ fontSize: 10, padding: "1px 8px", borderRadius: 10 }}>{tagFilter}</span>
                <span className="ink3">· {filtered.length} document{filtered.length === 1 ? "" : "s"}</span>
                <button onClick={() => setTagFilter(null)} className="ink2"
                  style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, textDecoration: "underline" }}>
                  Clear
                </button>
              </div>
            )}
            {mobile ? (
              /* Phones: one card per document (no fixed-column table). Same tap-to-open + bulk select. */
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {filtered.map(d => {
                  const isSel = selectedId === d.id;
                  const t = d.trust || {};
                  const pct = t.score != null ? Math.round(t.score * 100) : null;
                  const accColor = pct == null ? "neutral" : pct >= 80 ? "emerald" : pct >= 60 ? "amber" : "rose";
                  return (
                    <div key={d.id}
                      onClick={() => { const sel = d.id === selectedId ? null : d.id; setSelectedId(sel); setListCollapsed(false); }}
                      className="card-soft" style={{ padding: 12, cursor: "pointer", background: isSel ? "var(--bg2)" : undefined }}>
                      <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
                        {isDocsProduct && (
                          <input type="checkbox" checked={bulkSel.has(d.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggleSelect(d.id)}
                            style={{ accentColor: "var(--gold2)", cursor: "pointer", flexShrink: 0, marginTop: 3 }} />
                        )}
                        <span style={{ flexShrink: 0, marginTop: 1, color: "var(--gold2)" }}><Icon name="file" size={16} /></span>
                        <div style={{ flex: "1 1 0", minWidth: 0 }}>
                          <div className="font-medium" title={d.name} style={{ fontSize: 13.5, lineHeight: 1.3, wordBreak: "break-word",
                            display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{d.name}</div>
                          <div className="row gap-1 mt-2" style={{ flexWrap: "wrap", alignItems: "center", gap: 6 }}>
                            {d.docType && <Pill color="violet">{prettyType(d.docType)}</Pill>}
                            <Pill color={STATUS_COLORS[d.ingestionStatus] || "neutral"}>{d.ingestionStatus || "—"}</Pill>
                            {isDocsProduct && d.ingestionStatus === "ready" && (
                              t.state === "verified"
                                ? <Pill color="emerald">✓ Verified</Pill>
                                : (pct != null && <Pill color={accColor}>{pct}%</Pill>)
                            )}
                          </div>
                          <div className="row mt-2" style={{ alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                            <span className="ink3 text-xs">{fmtUploaded(d.uploadedAt) || d.modified || "—"}</span>
                            {isDocsProduct && d.source === "drive" && (
                              <Icon name="cloud" size={12} style={{ color: "#8B7FD6" }} title="In your Google Drive" />
                            )}
                            {isDocsProduct && d.hasFile && (
                              <Icon name="database" size={11} style={{ color: "var(--ink3)" }} title="Cached on the server" />
                            )}
                          </div>
                        </div>
                        {canDelete && (
                          <div onClick={(e) => e.stopPropagation()} style={{ flexShrink: 0 }}>
                            <DocActions doc={d} matchedCount={reqsByDoc.get(d.id)?.length || 0} busy={busyDocId === d.id}
                              canManage={canAct} auditActions={!isDocsProduct} isDocsProduct={isDocsProduct}
                              onDelete={() => handleDelete(d)} onRematch={() => handleRematch(d)} onAttach={() => setAttachFor(d.id)}
                              onCreateAlert={(doc) => { setAlertDoc(doc); setAlertRuleOpen(true); }} />
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
                {filtered.length === 0 && (
                  <div className="ink3 p-6 text-sm" style={{ textAlign: "center", fontStyle: "italic" }}>
                    No documents match{typeFilter !== "all" ? ` type "${typeFilter}"` : ""}{searchResults !== null ? " the current search" : ""}.
                  </div>
                )}
              </div>
            ) : (
            <div className="bg1 border rounded-xl" style={{ overflow: "visible" }}>
              <div className="row gap-3 p-3 border-b ink3" style={{
                fontSize: 11, textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600,
                background: "var(--bg2)", borderRadius: "12px 12px 0 0",
              }}>
                <div style={{ flex: "2 1 0", minWidth: 0 }}>File</div>
                {!selected && <div style={{ width: 180 }}>Type</div>}
                <div style={{ width: 90 }}>Status</div>
                {isDocsProduct && <div style={{ width: 110 }}>Accuracy</div>}
                {/* M46 · "Reqs matched" is an audit concept — hide in Documents. */}
                {!isDocsProduct && <div style={{ width: 100, textAlign: "right" }}>Reqs matched</div>}
                {!selected && <div style={{ width: 140 }}>Uploaded</div>}
                {/* M46 · Share column — share a doc into one or more groups. */}
                {isDocsProduct && !selected && <div style={{ width: 120 }}>Share</div>}
                {canDelete && !selected && <div style={{ width: 140, textAlign: "right" }}>Actions</div>}
              </div>
              {filtered.map(d => {
                const matchedReqs = reqsByDoc.get(d.id) || [];
                const isSel = selectedId === d.id;
                return (
                  <div key={d.id}
                       onClick={() => { const sel = d.id === selectedId ? null : d.id; setSelectedId(sel); setListCollapsed(false); }}
                       className="row gap-3 p-3 hover-bg"
                       style={{
                         borderBottom: "1px solid var(--line)",
                         cursor: "pointer",
                         background: isSel ? "var(--bg2)" : "transparent",
                         fontSize: 12,
                       }}>
                    {isDocsProduct && (
                      <input type="checkbox" checked={bulkSel.has(d.id)} onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleSelect(d.id)}
                        style={{ accentColor: "var(--gold2)", cursor: "pointer", flexShrink: 0, alignSelf: "center" }} />
                    )}
                    <div style={{ flex: "2 1 0", minWidth: 0 }}>
                      <div className="row gap-2" style={{ alignItems: "center" }}>
                        <div className="font-medium" title={d.name}
                          style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                                   overflow: "hidden", wordBreak: "break-word", lineHeight: 1.3 }}>{d.name}</div>
                        {/* M30.12 · "shared" badge on tenant-shared docs
                            (vendor_pk = NULL — admin uploads or seeded
                            reference data). Helps admin understand that
                            deleting from one vendor's tab removes it
                            from EVERY vendor (intentional). M46 · suppressed in
                            the Documents product, where every doc is per-user
                            owned (not shared) — the badge would be misleading. */}
                        {d.vendorPk == null && !isDocsProduct && (
                          <span className="upper mono" style={{
                            fontSize: 9, padding: "1px 5px", borderRadius: 3,
                            background: "rgba(63,164,122,0.15)", color: "#3FA47A",
                            flexShrink: 0,
                          }} title="Tenant-shared evidence · visible to every vendor; the matcher includes these too">
                            shared
                          </span>
                        )}
                        {/* M46 · storage state · ☁ = in your Google Drive · ▤ = cached on the server */}
                        {isDocsProduct && (d.source === "drive" || d.hasFile) && (
                          <span className="row gap-1" style={{ alignItems: "center", flexShrink: 0 }}>
                            {d.source === "drive" && (
                              <span style={{ display: "flex" }}
                                title={d.hasFile
                                  ? "In your Google Drive · also cached on the server"
                                  : "In your Google Drive · server copy freed (re-opens from Drive)"}>
                                <Icon name="cloud" size={13} style={{ color: "#8B7FD6" }} />
                              </span>
                            )}
                            {d.hasFile && (
                              <span style={{ display: "flex" }} title="Original cached on the server">
                                <Icon name="database" size={12} style={{ color: "var(--ink3)" }} />
                              </span>
                            )}
                            {d.source === "drive" && !d.hasFile && (
                              <button onClick={(e) => { e.stopPropagation(); handleRepull(d); }}
                                disabled={busyDocId === d.id}
                                title="Re-fetch the original from your Drive"
                                style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                                  display: "flex", color: "var(--ink3)" }}>
                                <Icon name={busyDocId === d.id ? "refresh" : "download"} size={12} />
                              </button>
                            )}
                          </span>
                        )}
                        {/* M46 · approve a processed doc as accurate */}
                        {isDocsProduct && d.ingestionStatus === "ready" && (
                          d.reviewStatus === "reviewed" ? (
                            <span className="row" style={{ alignItems: "center", flexShrink: 0, color: "var(--emerald)" }} title="Approved as accurate">
                              <Icon name="check" size={14}/>
                            </span>
                          ) : (
                            <button onClick={(e) => { e.stopPropagation(); onApprove(d); }} disabled={busyDocId === d.id}
                              title="Approve this document as accurate" className="row"
                              style={{ alignItems: "center", background: "none", border: "none", padding: 2, cursor: "pointer", color: "var(--ink3)" }}>
                              <Icon name="check" size={14}/>
                            </button>
                          )
                        )}
                      </div>
                      {/* M51 · tag chips · click to filter the list by a tag. */}
                      {(d.tags || []).length > 0 && (
                        <div className="row gap-1 mt-1" style={{ flexWrap: "wrap" }}>
                          {d.tags.map(t => (
                            <button key={t}
                              onClick={(e) => { e.stopPropagation(); setTagFilter(tagFilter === t ? null : t); }}
                              title={tagFilter === t ? "Clear tag filter" : `Filter by “${t}”`}
                              className={tagFilter === t ? "btn-gold" : "border bg2 ink2"}
                              style={{ fontSize: 10, padding: "1px 7px", borderRadius: 10, cursor: "pointer" }}>
                              {t}
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="mono ink3 text-xs mt-1">{d.id}</div>
                      {/* when the detail pane is open the Type column is hidden — show it here as a chip */}
                      {selected && d.docType && (
                        <div className="mt-1"><Pill color="violet">{prettyType(d.docType)}</Pill></div>
                      )}
                    </div>
                    {!selected && (
                    <div style={{ width: 180 }}>
                      {d.docType ? (
                        <span title={`confidence ${((d.docTypeConfidence || 0) * 100).toFixed(0)}%`}>
                          <Pill color="violet">{prettyType(d.docType)}</Pill>
                          <span className="ink3 mono text-xs ml-2">
                            {((d.docTypeConfidence || 0) * 100).toFixed(0)}%
                          </span>
                        </span>
                      ) : (
                        <span className="ink3 text-xs" style={{ fontStyle: "italic" }}>—</span>
                      )}
                    </div>
                    )}
                    <div style={{ width: 90 }} title={d.ingestionStatus === "failed" ? (d.ingestionError || "Failed") : undefined}>
                      <Pill color={STATUS_COLORS[d.ingestionStatus] || "neutral"}>
                        {d.ingestionStatus || "—"}
                      </Pill>
                      {d.ingestionStatus === "failed" && d.ingestionError && (
                        <div className="ink3 text-xs mt-1 truncate" style={{ maxWidth: 90 }}>
                          {d.ingestionError.slice(0, 40)}
                        </div>
                      )}
                    </div>
                    {isDocsProduct && (
                    <div style={{ width: 110 }}>
                      {(() => {
                        const t = d.trust || {};
                        if (t.state === "verified") return <Pill color="emerald">✓ Verified</Pill>;
                        if (t.state === "unstructured") return <span className="ink3 text-xs" title="Parsed but no schema — view as Markdown">— text only</span>;
                        if (t.score == null || d.ingestionStatus !== "ready") return <span className="ink3">—</span>;
                        const pct = Math.round(t.score * 100);
                        const color = pct >= 80 ? "emerald" : pct >= 60 ? "amber" : "rose";
                        return (
                          <span title="Extraction accuracy · click the ✓ to approve after reviewing">
                            <Pill color={color}>{pct}%</Pill>
                            {d.reviewStatus !== "reviewed" && t.level === "low" && (
                              <span className="ink3 text-xs ml-1" title="Low confidence — review + approve">review</span>
                            )}
                          </span>
                        );
                      })()}
                    </div>
                    )}
                    {!isDocsProduct && (
                    <div style={{ width: 100, textAlign: "right" }}>
                      {matchedReqs.length > 0 ? (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenReqsFor(openReqsFor === d.id ? null : d.id);
                          }}
                          className="mono hover-bg"
                          title="Show matched requirements"
                          style={{
                            padding: "2px 10px", borderRadius: 12, fontSize: 12,
                            border: "1px solid var(--line)", background: "var(--bg2)",
                            color: "var(--ink2)", cursor: "pointer",
                          }}
                        >
                          {matchedReqs.length} ▾
                        </button>
                      ) : (
                        <span className="ink3 mono">0</span>
                      )}
                    </div>
                    )}
                    {!selected && (
                    <div style={{ width: 140 }}>
                      <div className="ink2 text-xs" title={d.uploadedAt || d.modified || ""}>{fmtUploaded(d.uploadedAt) || d.modified || "—"}</div>
                      <div className="mono ink3 text-xs">{d.uploadedBy ? d.uploadedBy.split("@")[0] : "—"}</div>
                    </div>
                    )}
                    {/* M46 · Share — give the option to share this doc into a group.
                        Owners open the group picker; non-owners see a read-only marker;
                        with no groups yet, the button links to the Groups page to create one. */}
                    {isDocsProduct && !selected && (
                      <div style={{ width: 120 }} onClick={(e) => e.stopPropagation()}>
                        {d.ownedByMe !== false ? (
                          myGroups.length > 0 ? (
                            <button
                              onClick={(e) => shareForDoc === d.id ? setShareForDoc(null) : openSharePicker(d, e)}
                              title={(d.groupIds || []).length ? "Shared to groups · click to change" : "Share this document to a group"}
                              className="row hover-bg"
                              style={{
                                alignItems: "center", gap: 6, padding: "3px 10px", borderRadius: 6, fontSize: 12,
                                border: "1px solid var(--line)", background: "transparent", cursor: "pointer",
                                color: (d.groupIds || []).length ? "var(--gold2)" : "var(--ink2)",
                              }}>
                              <Icon name="users" size={13}/>
                              <span>{(d.groupIds || []).length ? `Shared · ${(d.groupIds || []).length}` : "Share"}</span>
                            </button>
                          ) : (
                            <button
                              onClick={() => onOpenGroups()}
                              title="Create a group first, then share documents into it"
                              className="row hover-bg"
                              style={{
                                alignItems: "center", gap: 6, padding: "3px 10px", borderRadius: 6, fontSize: 12,
                                border: "1px solid var(--line)", background: "transparent", cursor: "pointer", color: "var(--ink2)",
                              }}>
                              <Icon name="users" size={13}/>
                              <span>Share</span>
                            </button>
                          )
                        ) : (d.groupIds || []).length > 0 ? (
                          <span title="Shared into this group by its owner — only the owner can change sharing"
                            className="row ink4" style={{ alignItems: "center", gap: 6, fontSize: 12 }}>
                            <Icon name="users" size={13}/><span>Shared</span>
                          </span>
                        ) : (
                          <span className="ink3">—</span>
                        )}
                      </div>
                    )}
                    {canDelete && !selected && (
                      <div style={{ width: 140, textAlign: "right" }}
                           onClick={(e) => e.stopPropagation()}>
                        <DocActions
                          doc={d}
                          matchedCount={matchedReqs.length}
                          busy={busyDocId === d.id}
                          canManage={canAct}
                          auditActions={!isDocsProduct}
                          isDocsProduct={isDocsProduct}
                          onDelete={() => handleDelete(d)}
                          onRematch={() => handleRematch(d)}
                          onAttach={() => setAttachFor(d.id)}
                          onCreateAlert={(doc) => { setAlertDoc(doc); setAlertRuleOpen(true); }}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
              {filtered.length === 0 && (
                <div className="ink3 p-6 text-sm" style={{ textAlign: "center", fontStyle: "italic" }}>
                  No documents match{typeFilter !== "all" ? ` type "${typeFilter}"` : ""}{searchResults !== null ? " the current search" : ""}.
                </div>
              )}
            </div>
            )}

          </div>
        )}
      </div>

      {/* Linked-requirements modal — opened when the user clicks the
          "N ▾" count in the Reqs matched column. Shows a clean grouped
          table by framework so the reviewer can see at a glance which
          requirements are satisfied without leaving the Documents view. */}
      {openReqsFor && (() => {
        const openDoc = docs.find(d => d.id === openReqsFor);
        const openReqs = reqsByDoc.get(openReqsFor) || [];
        if (!openDoc) return null;
        return (
          <LinkedRequirementsModal
            doc={openDoc}
            reqs={openReqs}
            onClose={() => setOpenReqsFor(null)}
          />
        );
      })()}

      {/* M29.2 · attach-to-requirement picker */}
      {attachFor && (() => {
        const doc = docs.find(d => d.id === attachFor);
        if (!doc) return null;
        return (
          <AttachRequirementModal
            doc={doc}
            requirements={requirements || []}
            onClose={() => setAttachFor(null)}
            onPick={(reqId) => handleAttachConfirm(attachFor, reqId)}
          />
        );
      })()}

      {/* M29.2 · floating toast for async action feedback */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 70,
          padding: "10px 16px", borderRadius: 6, fontSize: 12,
          background: toast.kind === "err" ? "rgba(216,98,94,0.12)" : "rgba(63,164,122,0.12)",
          color: toast.kind === "err" ? "#D8625E" : "#3FA47A",
          border: `1px solid ${toast.kind === "err" ? "rgba(216,98,94,0.4)" : "rgba(63,164,122,0.4)"}`,
          maxWidth: 480, boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
        }}>{toast.text}</div>
      )}

      {/* Floating reviewer surface — when a doc is selected we promote the
          chat+viewer to a near-fullscreen overlay so the reviewer gets the
          maximum chat + document real estate. ESC or the × button closes
          it back to the document list. */}
      {/* The document detail (chat + viewer + fields) docks on the RIGHT so the
          list stays visible on the left — a 2-pane workspace. × or ESC closes it. */}
      {selected && (
        <div
          style={{
            position: "fixed",
            top: mobile ? 48 : 57, bottom: 0,
            zIndex: 45,
            background: "var(--bg)",
            borderLeft: "1px solid var(--line)",
            boxShadow: "-10px 0 34px rgba(0,0,0,0.30)",
            display: "flex",
            flexDirection: "column",
            // Mobile: full-screen overlay. Desktop: docked right pane (resizable).
            ...(mobile ? { left: 0, right: 0 } : (listCollapsed ? { left: 44, right: 0 } : { right: 0, width: paneW })),
          }}
        >
          {/* left edge · drag to resize (desktop, list showing) + a « to collapse the list */}
          {!listCollapsed && !mobile && (
            <div onMouseDown={startPaneResize} title="Drag to resize"
              style={{ position: "absolute", left: -5, top: 0, bottom: 0, width: 11, cursor: "col-resize", zIndex: 2 }} />
          )}
          {!listCollapsed && (
            <button onClick={() => setListCollapsed(true)} title="Hide the list (full-width document)"
              className="border bg2 hover-bg"
              style={{ position: "absolute", left: 3, top: "50%", transform: "translateY(-50%)", zIndex: 4,
                       width: 20, height: 42, borderRadius: 6, cursor: "pointer", fontSize: 13, color: "var(--ink2)",
                       lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>«</button>
          )}
          <ErrorBoundary>
            <DocumentChatPanel
              doc={selected}
              onClose={() => { setSelectedId(null); setListCollapsed(false); }}
              onDocUpdated={(fresh) => {
                setDocsMap?.(prev => ({ ...(prev || {}), [fresh.id]: fresh }));
              }}
            />
          </ErrorBoundary>
        </div>
      )}
      {/* Entity graph docked panel — side-by-side with the document list.
          Same pattern as the document detail panel above. */}
      {entityGraphData && !selected && (
        <div
          style={{
            position: "fixed",
            top: mobile ? 48 : 57, bottom: 0,
            zIndex: 44,
            // Mobile: full-screen. Desktop: docked right pane.
            ...(mobile ? { left: 0, right: 0 } : { right: 0, width: paneW }),
            background: "var(--bg1, #0F172A)",
            borderLeft: "1px solid var(--line)",
            boxShadow: "-10px 0 34px rgba(0,0,0,0.30)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Resize handle — desktop only */}
          {!mobile && (
            <div onMouseDown={startPaneResize} title="Drag to resize"
              style={{ position: "absolute", left: -5, top: 0, bottom: 0, width: 11, cursor: "col-resize", zIndex: 2 }} />
          )}
          {/* Slim header — GraphTab has its own search bar */}
          <div className="row between" style={{
            padding: "6px 12px", alignItems: "center", flexShrink: 0,
            borderBottom: "1px solid var(--line)",
            background: "var(--bg2, #1E293B)",
          }}>
            <span className="ink3" style={{ fontSize: 11, fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase" }}>
              🕸 Entity Graph
            </span>
            <button onClick={() => { setEntityGraphData(null); setEntityGraphError(null); setSearchMode("content"); }}
              className="ink3 hover-bg"
              style={{ background: "none", border: "1px solid var(--line)", borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>
              ✕ Close
            </button>
          </div>
          {/* GraphTab with Tree / Graph / List modes — replaces ForceGraph-only panel */}
          <div style={{ flex: 1, minHeight: 0 }}>
            <GraphTab doc={null} />
          </div>
        </div>
      )}
      {/* Entity graph error banner (no graph to show) */}
      {entityGraphError && !selected && (
        <div style={{
          position: "fixed", top: 57, right: 0, zIndex: 44,
          background: "color-mix(in srgb, var(--rose) 12%, var(--bg1))",
          border: "1px solid var(--rose, #D8625E)",
          borderRight: "none", borderRadius: "8px 0 0 8px",
          padding: "10px 16px", fontSize: 12, color: "var(--rose, #D8625E)",
          maxWidth: 360,
        }}>
          <div className="row between" style={{ alignItems: "center", gap: 12 }}>
            <span>{entityGraphError}</span>
            <button onClick={() => setEntityGraphError(null)}
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink3)", fontSize: 14 }}>✕</button>
          </div>
        </div>
      )}
      {/* collapsed · a slim strip on the far left to bring the document list back.
          Desktop only — on mobile the detail is full-screen with its own segmented
          control + close, and this fixed 44px strip would overlap/clip it (feedback pk 24). */}
      {selected && listCollapsed && !mobile && (
        <button onClick={() => setListCollapsed(false)} title="Show the document list"
          className="hover-bg"
          style={{ position: "fixed", left: 0, top: 57, bottom: 0, width: 44, zIndex: 46,
                   background: "var(--bg1)", borderRight: "1px solid var(--line)", cursor: "pointer",
                   color: "var(--ink2)", fontSize: 17, display: "flex", alignItems: "center", justifyContent: "center" }}>»</button>
      )}

      {/* Alert rule modal — bulk or single-doc */}
      {alertRuleOpen && (
        <AlertRuleModal
          onClose={(created) => {
            setAlertRuleOpen(false);
            setAlertDoc(null);
            if (created) {
              clearSelection();
              setToast({ kind: "ok", text: "Alert rule created — check your Dashboard" });
            }
          }}
          selectedDocs={alertDoc ? [alertDoc] : docs.filter(d => bulkSel.has(d.id))}
        />
      )}
    </div>
  );
}


// Full-modal table listing every requirement satisfied by a document,
// grouped by framework (KYC / KYB / SOC2 / ISO / etc derived from the
// `group` field). Reviewer sees confidence + status + title at a glance
// across all linked requirements without leaving the Documents view.

