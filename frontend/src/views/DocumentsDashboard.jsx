// M46 · Documents System · per-user home dashboard (enterprise edition).
//
// Workspace overview for the standalone Documents product: a KPI strip, then a
// chat-first two-column body — a slim left rail (document-type breakdown +
// recent activity) and a wide "Ask your documents" chat that fills the right
// column. Plus a toolbar (search + CSV export). Every figure + answer is scoped
// to the signed-in user (owner-scoped backend).
import React, { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon.jsx";
import RichMessage from "../components/RichMessage.jsx";
import SmartVisuals from "../components/doc-chat/SmartVisuals.jsx";
import ChatFeedback from "../components/ChatFeedback.jsx";
import { ArtifactBar, StepTrace, Citations } from "../components/AgentExtras.jsx";
import { useApiResource } from "../api/useApi.js";
import { fetchDocumentsDashboard, fetchConsent, acceptConsent } from "../api/documents";
import { fetchWorkspaceChat, postWorkspaceChatMessage, fetchDocument, uploadDocument, clearWorkspaceChat } from "../api";
import { useConfirm } from "../components/ConfirmDialog.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import DocumentViewer from "../components/DocumentViewer.jsx";
import DragDivider from "../components/DragDivider.jsx";
import AlertBar from "../components/AlertBar.jsx";
import { LoadingState, ErrorState } from "../components/Shell.jsx";
import { useIsMobile } from "../useIsMobile.js";

const PILLS = [
  "Summarize all my documents",
  "What dates or deadlines are coming up?",
  "List all amounts and totals",
  "What types of documents do I have?",
  "Find any risks or red flags",
];

const STATUS_COLOR = {
  ready: "#3FA47A", processing: "#E0A23B", pending: "#8B7FD6",
  failed: "#D8625E", unknown: "var(--ink3)",
};
const BAR_COLORS = ["#E2BC68", "#8B7FD6", "#3FA47A", "#E0A23B", "#D8625E", "#6A93C8", "#C8A04C"];

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"]; let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

function exportCsv(d) {
  // Guard against CSV/formula injection: a cell that Excel/Sheets would evaluate
  // as a formula (leading = + - @, or tab/CR) gets a leading apostrophe so it's
  // treated as text. Doc names / types come from user documents, so this matters.
  const esc = (c) => {
    let s = String(c ?? "");
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return `"${s.replace(/"/g, '""')}"`;
  };
  const rows = [
    ["DocAIQuest · Documents workspace overview"], [],
    ["Metric", "Value"],
    ["Total documents", d.totalDocs], ["Ready", d.readyDocs],
    ["Pages processed", d.totalPages], ["Searchable chunks", d.totalChunks],
    ["Records extracted", d.recordsExtracted], ["Questions asked", d.questionsAsked],
    ["Avg confidence", d.avgConfidence ?? ""], ["PII-protected", d.piiProtected],
    ["Indexed text", fmtBytes(d.indexedBytes)], [],
    ["Document type", "Count"], ...(d.byType || []).map((t) => [t.type, t.count]), [],
    ["Recent document", "Status", "Type", "Source"],
    ...(d.recent || []).map((r) => [r.name, r.status, r.docType || "", r.source || "upload"]),
  ];
  const csv = rows.map((r) => r.map(esc).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "documents-overview.csv";
  a.click();
}

function Kpi({ icon, label, value, sub, accent, dense }) {
  const c = accent || "var(--gold2)";
  // Phones: a single-line pill — [chip] value · label — half the height.
  if (dense) {
    return (
      <div className="card-soft kpi-card kpi-dense" style={{
        position: "relative", overflow: "hidden", display: "flex", alignItems: "center", gap: 6, minWidth: 0,
        background: `linear-gradient(180deg, color-mix(in srgb, ${c} 8%, var(--bg1)), var(--bg1) 62%)`,
      }}>
        <span aria-hidden style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, background: c }} />
        <span className="kpi-chip" style={{ width: 17, height: 17, borderRadius: 5, flex: "0 0 auto", display: "grid", placeItems: "center", color: c, background: `color-mix(in srgb, ${c} 16%, transparent)` }}>
          <Icon name={icon} size={11} />
        </span>
        <span className="serif" style={{ fontSize: 15, lineHeight: 1, color: c, flex: "0 0 auto" }}>{value}</span>
        <span className="upper" style={{ fontSize: 7.5, letterSpacing: ".02em", color: "var(--ink3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{label}</span>
      </div>
    );
  }
  return (
    <div className="card-soft kpi-card" style={{
      padding: 16, flex: "1 1 0", minWidth: 140, position: "relative", overflow: "hidden",
      // a faint accent wash + a colored left edge give each stat its own identity
      background: `linear-gradient(180deg, color-mix(in srgb, ${c} 7%, var(--bg1)), var(--bg1) 62%)`,
    }}>
      <span aria-hidden style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, background: c }} />
      <div className="kpi-head row gap-2" style={{ alignItems: "center" }}>
        <span className="kpi-chip" style={{ width: 26, height: 26, borderRadius: 8, flex: "0 0 auto", display: "grid", placeItems: "center", color: c, background: `color-mix(in srgb, ${c} 16%, transparent)` }}>
          <Icon name={icon} size={14} />
        </span>
        <span className="upper" style={{ fontSize: 9, letterSpacing: ".08em", color: "var(--ink3)" }}>{label}</span>
      </div>
      <div className="serif" style={{ fontSize: 26, lineHeight: 1.1, marginTop: 8, color: c }}>{value}</div>
      {sub && <div className="ink3" style={{ fontSize: 11, marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function Panel({ title, children, action }) {
  return (
    <div className="bg1 border rounded-xl" style={{ padding: 18 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <span className="upper ink3" style={{ fontSize: 10, letterSpacing: ".08em" }}>{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

function Bars({ items, labelKey = "type", valueKey = "count" }) {
  const max = Math.max(1, ...items.map((t) => t[valueKey]));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      {items.map((t, i) => (
        <div key={t[labelKey] + i}>
          <div className="row" style={{ justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
            <span className="ink2" style={{ textTransform: "capitalize" }}>{String(t[labelKey]).replace(/_/g, " ")}</span>
            <span className="mono ink3">{t[valueKey]}</span>
          </div>
          <div style={{ height: 6, background: "var(--bg3)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ width: `${(t[valueKey] / max) * 100}%`, height: "100%", background: BAR_COLORS[i % BAR_COLORS.length], borderRadius: 3 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function AskPanel({ onOpenDocument, onCollapse, mobile }) {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [promptsOpen, setPromptsOpen] = useState(false);   // ⚡ pre-prompt popover
  const [uploading, setUploading] = useState(false);       // + Add · inline upload from chat
  const [processing, setProcessing] = useState([]);        // [{id,name,status}] · live ingest tray
  const scroller = useRef(null);
  const fileRef = useRef(null);
  const confirmDialog = useConfirm();

  useEffect(() => { fetchWorkspaceChat(null).then((t) => setMessages(t.messages || [])).catch(() => {}); }, []);
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; }, [messages, busy, processing]);

  // Poll uploaded docs until the worker flips them ready/failed, so the tray's
  // animated dots reflect REAL ingest state. Restarts only when the set of
  // in-flight ids changes (not on every status tick), then stops when none remain.
  const inFlightKey = processing.filter((p) => p.status === "pending" || p.status === "processing")
    .map((p) => p.id).sort().join(",");
  useEffect(() => {
    if (!inFlightKey) return;
    let cancelled = false;
    const tick = async () => {
      const ids = inFlightKey.split(",");
      const updates = await Promise.all(ids.map(async (id) => {
        try { const d = await fetchDocument(id); return [id, d.ingestionStatus]; }
        catch { return [id, null]; }
      }));
      if (cancelled) return;
      setProcessing((prev) => prev.map((p) => {
        const u = updates.find(([id]) => id === p.id);
        return u && u[1] ? { ...p, status: u[1] } : p;
      }));
    };
    const handle = setInterval(tick, 4000);
    tick();
    return () => { cancelled = true; clearInterval(handle); };
  }, [inFlightKey]);

  // Auto-dismiss ready/failed chips a few seconds after they settle so the tray
  // doesn't accumulate (the doc stays in the library either way).
  useEffect(() => {
    if (!processing.some((p) => p.status === "ready" || p.status === "failed")) return;
    const t = setTimeout(() => setProcessing((prev) =>
      prev.filter((p) => p.status === "pending" || p.status === "processing")), 8000);
    return () => clearTimeout(t);
  }, [processing]);

  // + Add · upload documents without leaving the chat. Consent-
  // gated like the Documents tab; on success the doc lands in the live processing
  // tray (animated dots) below, which polls until it's ready to ask about.
  const handleAdd = () => fileRef.current?.click();
  const handleFiles = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";                 // allow re-selecting the same file later
    if (!files.length || uploading) return;
    // One-time compliance acknowledgement (personal/health data + free-plan training).
    try {
      const c = await fetchConsent();
      if (!c.personalData) {
        const ok = await confirmDialog({
          title: "Before you upload",
          body: `Your documents may contain personal or special-category (health) data. ${productName} processes them to extract and answer questions; redacted text may be sent to AI providers. By continuing you acknowledge this and consent to that processing.`,
          confirmLabel: "I acknowledge & continue",
        });
        if (!ok) return;
        await acceptConsent("personal_data");
      }
      if (c.modelTrainingRequired) {
        const ok = await confirmDialog({
          title: "Free plan — how your data is used",
          body: `On the free plan, your uploaded documents may be used to help improve ${productName}'s AI models (for example, learning better field schemas). Paid plans keep your data private and are never used for training. By continuing on the free plan you consent to this use.`,
          confirmLabel: "I agree — continue on Free",
        });
        if (!ok) return;
        await acceptConsent("model_training");
      }
    } catch { /* consent check best-effort; the backend still gates the upload */ }

    setUploading(true); setErr(null);
    const added = [], failed = [];
    for (const file of files) {
      try {
        const created = await uploadDocument(file);
        added.push({ id: created.id, name: created.name || file.name, status: created.ingestionStatus || "pending" });
      }
      catch (ex) { failed.push(file.name); }
    }
    setUploading(false);
    if (added.length) {
      setProcessing((prev) => {
        const seen = new Set(prev.map((x) => x.id));
        return [...prev, ...added.filter((a) => a.id && !seen.has(a.id))];
      });
    }
    if (failed.length) setErr(`Couldn't upload: ${failed.join(", ")}`);
  };

  const handleReset = async () => {
    if (busy || uploading || !messages.length) return;
    const ok = await confirmDialog({
      title: "Reset chat?",
      body: "This clears your entire conversation history. Your documents are not affected.",
      confirmLabel: "Reset chat",
    });
    if (!ok) return;
    try { await clearWorkspaceChat(null); setMessages([]); setErr(null); }
    catch (ex) { setErr(ex.message || "Couldn't reset the chat"); }
  };

  const ask = async (q) => {
    const question = (q ?? text).trim();
    if (!question || busy) return;
    setText(""); setErr(null);
    setMessages((m) => [...m, { id: `u-${Date.now()}`, role: "user", text: question }]);
    setBusy(true);
    try {
      await postWorkspaceChatMessage(null, question);
      // Refetch so the AI reply (and the optimistic user bubble) carry their
      // real numeric PKs — ChatFeedback only renders for persisted messages.
      const fresh = await fetchWorkspaceChat(null);
      setMessages(fresh.messages || []);
    }
    catch (e) { setErr(e.message || "Couldn't get an answer"); }
    finally { setBusy(false); }
  };

  return (
    <div className="card-soft ask-panel" style={{ padding: mobile ? 13 : 18, display: "flex", flexDirection: "column", height: "100%", minHeight: mobile ? 0 : 520, minWidth: 0, maxWidth: "100%", overflow: "hidden" }}>
      <div className="row between" style={{ alignItems: "center", marginBottom: mobile ? 9 : 12 }}>
        <span className="row gap-2" style={{ alignItems: "center" }}>
          <Icon name="chat" size={15} style={{ color: "var(--gold2)" }} />
          <span className="serif" style={{ fontSize: 16 }}>Ask your documents</span>
        </span>
        <span className="row gap-2" style={{ alignItems: "center" }}>
          <button onClick={handleReset} disabled={busy || uploading || !messages.length}
            title="Reset chat" aria-label="Reset chat" className="border bg2 hover-bg row gap-1"
            style={{ height: 26, padding: "0 9px", borderRadius: 6, cursor: messages.length ? "pointer" : "default",
                     fontSize: 11, color: "var(--ink2)", alignItems: "center", opacity: (busy || uploading || !messages.length) ? 0.45 : 1 }}>
            <Icon name="refresh" size={12} />{!mobile && <span>Reset</span>}
          </button>
          {onCollapse && !mobile && (
            <button onClick={onCollapse} title="Collapse chat" className="border bg2 hover-bg"
              style={{ width: 26, height: 26, borderRadius: 6, cursor: "pointer", fontSize: 14, color: "var(--ink2)", lineHeight: 1 }}>«</button>
          )}
        </span>
      </div>
      <div ref={scroller} className="bg2 border rounded-md" style={{ flex: "1 1 0", overflowY: "auto", overflowX: "hidden", minWidth: 0, padding: 12, marginBottom: 10, minHeight: mobile ? 0 : 200 }}>
        {messages.length === 0 && !busy ? (
          <div className="ink3" style={{ fontSize: 12, fontStyle: "italic" }}>Ask anything across your documents — tap <b>+</b> to add a document, <b>⚡</b> for suggested prompts, or just type your own.</div>
        ) : messages.map((m) => (
          <div key={m.id} style={{ marginBottom: 12, textAlign: m.role === "user" ? "right" : "left" }}>
            <div className="upper ink4" style={{ fontSize: 9, letterSpacing: ".08em", marginBottom: 3 }}>{m.role === "user" ? "You" : productName}</div>
            <div className={`bubble ${m.role === "user" ? "bubble-you" : "bubble-ai"}`} style={{ fontSize: 13 }}>
              {m.role === "user" ? <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span> : <RichMessage content={m.text} />}
            </div>
            {m.role !== "user" && <SmartVisuals content={m.text} />}
            {m.role !== "user" && <Citations items={m.citations} onOpen={onOpenDocument} />}
            {m.role !== "user" && <ArtifactBar artifacts={m.artifacts} />}
            {m.role !== "user" && <StepTrace steps={m.trace} />}
            {m.role !== "user" && <ChatFeedback messageId={m.id} />}
          </div>
        ))}
        {busy && <div className="ink3 row" style={{ fontSize: 12, alignItems: "center" }}>Thinking<span className="thinking-dots"><span/><span/><span/></span></div>}
        {uploading && <div className="ink3 row" style={{ fontSize: 12, alignItems: "center" }}>Uploading<span className="thinking-dots"><span/><span/><span/></span></div>}
      </div>
      {/* Live ingest tray — animated dots while the worker processes an uploaded doc. */}
      {processing.length > 0 && (
        <div className="anim-fade" style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
          {processing.map((p) => {
            const done = p.status === "ready", bad = p.status === "failed";
            return (
              <div key={p.id} className="bg2 border rounded-md" style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 10px", fontSize: 12 }}>
                <Icon name={bad ? "alert" : done ? "check" : "file"} size={13}
                  style={{ flex: "0 0 auto", color: bad ? "#D8625E" : done ? "#3FA47A" : "var(--gold2)" }} />
                <span className="ink2" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
                {done ? <span style={{ color: "#3FA47A", flex: "0 0 auto" }}>Ready — ask away</span>
                  : bad ? <span style={{ color: "#D8625E", flex: "0 0 auto" }}>Couldn't process</span>
                  : <span className="ink3 row" style={{ alignItems: "center", flex: "0 0 auto" }}>Processing<span className="thinking-dots"><span/><span/><span/></span></span>}
              </div>
            );
          })}
        </div>
      )}
      {err && <div style={{ fontSize: 11, color: "#D8625E", marginBottom: 8 }} className="mono">{err}</div>}
      <div style={{ position: "relative", flex: "0 0 auto" }}>
        {promptsOpen && (
          <>
            {/* tap-away backdrop */}
            <div onClick={() => setPromptsOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 20 }} />
            <div className="card-soft anim-fade" style={{ position: "absolute", bottom: "100%", left: 0, right: 0, marginBottom: 8, padding: 11, borderRadius: 14, zIndex: 21 }}>
              <div className="upper ink4" style={{ fontSize: 8, letterSpacing: ".08em", marginBottom: 8 }}>Suggested prompts</div>
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                {PILLS.map((p) => (
                  <button key={p} type="button" onClick={() => { setPromptsOpen(false); ask(p); }} disabled={busy}
                    className="border bg2 hover-bg" style={{ fontSize: mobile ? 13 : 12, padding: mobile ? "8px 13px" : "6px 12px", borderRadius: 999, color: "var(--ink2)", cursor: "pointer" }}>{p}</button>
                ))}
              </div>
            </div>
          </>
        )}
        <form onSubmit={(e) => { e.preventDefault(); ask(); }} className="row gap-2">
          <input ref={fileRef} type="file" multiple onChange={handleFiles}
            accept="application/pdf,.pdf,.docx,.xlsx,.csv,.txt,.eml,image/*" style={{ display: "none" }} />
          <button type="button" onClick={handleAdd} disabled={uploading} title="Add documents" aria-label="Add documents"
            className="border bg2 hover-bg" style={{ flex: "0 0 auto", width: mobile ? 44 : 40, height: mobile ? 44 : 40, borderRadius: 999, display: "grid", placeItems: "center", padding: 0, color: "var(--gold2)", opacity: uploading ? 0.5 : 1 }}>
            <Icon name={uploading ? "refresh" : "plus"} size={mobile ? 20 : 18} />
          </button>
          <button type="button" onClick={() => setPromptsOpen((o) => !o)} title="Suggested prompts" aria-label="Suggested prompts"
            className="border bg2 hover-bg" style={{ flex: "0 0 auto", width: mobile ? 44 : 40, height: mobile ? 44 : 40, borderRadius: 999, display: "grid", placeItems: "center", padding: 0, color: promptsOpen ? "var(--gold2)" : "var(--ink2)" }}>
            <Icon name="zap" size={mobile ? 18 : 16} />
          </button>
          <input value={text} onChange={(e) => setText(e.target.value)} onFocus={() => setPromptsOpen(false)}
            placeholder={mobile ? "Ask your documents…" : "Ask across all your documents…"}
            className="bg2 border" style={{ flex: 1, minWidth: 0, padding: mobile ? "12px 14px" : "10px 13px", borderRadius: 999, fontSize: mobile ? 15 : 13, color: "var(--ink)", outline: "none" }} />
          <button type="submit" disabled={busy || !text.trim()} title="Ask" aria-label="Ask" className="btn-gold"
            style={{ flex: "0 0 auto", width: mobile ? 44 : 40, height: mobile ? 44 : 40, borderRadius: 999, display: "grid", placeItems: "center", padding: 0, opacity: busy || !text.trim() ? 0.5 : 1 }}>
            <Icon name="send" size={mobile ? 19 : 17} />
          </button>
        </form>
      </div>
    </div>
  );
}

export default function DocumentsDashboard({ onOpenDocuments, onOpenConnectors }) {
  const { user, productName } = useAuth();
  const mobile = useIsMobile(820);
  const { data, loading, error } = useApiResource(fetchDocumentsDashboard);
  const [search, setSearch] = useState("");
  const [leftOpen, setLeftOpen] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);
  const [openDocId, setOpenDocId] = useState(null);
  const [focus, setFocus] = useState(null);
  const [libW, setLibW] = useState(262);   // draggable library width
  const [docW, setDocW] = useState(480);    // draggable document-pane width
  const name = (user?.name || user?.email || "").split("@")[0];

  // Open a document in the right pane — from a recent-activity click (string id) or a chat
  // citation click (object with docId + field for the pulse).
  const openDoc = (idOrCite) => {
    if (typeof idOrCite === "string") { setOpenDocId(idOrCite); setFocus(null); }
    else if (idOrCite && idOrCite.docId) {
      setOpenDocId(idOrCite.docId);
      setFocus({ field: idOrCite.field, page: idOrCite.page, at: Date.now() });
    }
  };

  if (loading) return <div className="ink3" style={{ fontSize: 13, padding: 20 }}>Loading your workspace…</div>;
  if (error) return <div className="mono" style={{ fontSize: 12, color: "#D8625E", padding: 20 }}>{typeof error === "string" ? error : error?.message || "Something went wrong."}</div>;

  const d = data || {};

  // ── Mobile: a single-column "app screen" — compact header, a 3-col stat grid
  // that never scrolls sideways, then the chat fills the rest of the viewport with
  // its own internal scroll (one scroll, input pinned). No side panes, no dividers. */
  if (mobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10, height: "100%", minHeight: 0, minWidth: 0, maxWidth: "100%", overflowX: "hidden" }}>
        {/* no big "Workspace · <name>" title on phones — it ate the top third; the app-bar
            already shows who you are. Straight to the stats + chat. */}
        <div className="kpi-grid" style={{ flex: "0 0 auto", display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 7, minWidth: 0 }}>
          <Kpi dense icon="file" label="Docs" value={d.totalDocs ?? 0} />
          <Kpi dense icon="layers" label="Pages" value={d.totalPages ?? 0} accent="#8B7FD6" />
          <Kpi dense icon="database" label="Data" value={(d.totalChunks ?? 0).toLocaleString()} accent="#3FA47A" />
          <Kpi dense icon="hash" label="Rows" value={(d.recordsExtracted ?? 0).toLocaleString()} accent="#E2BC68" />
          <Kpi dense icon="chat" label="Q&A" value={d.questionsAsked ?? 0} accent="#E0A23B" />
          <Kpi dense icon="check" label="Conf" value={d.avgConfidence != null ? `${Math.round(d.avgConfidence * 100)}%` : "—"} accent="#6A93C8" />
        </div>
        <AlertBar onOpenDocument={openDoc} />
        <div style={{ flex: "1 1 0", minHeight: 0, minWidth: 0, display: "flex" }}>
          <AskPanel onOpenDocument={openDoc} mobile />
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* header + toolbar */}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="serif" style={{ fontSize: 26, lineHeight: 1.1 }}>
            Workspace overview{name ? <> · <em className="italic ink2" style={{ fontWeight: 400 }}>{name}</em></> : ""}
          </h1>
          <p className="ink3" style={{ fontSize: 13, marginTop: 4 }}>Private to you — only you can see what's here.</p>
        </div>
        <div className="row gap-2" style={{ alignItems: "center" }}>
          <form onSubmit={(e) => { e.preventDefault(); onOpenDocuments?.(); }} className="row gap-2">
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search documents…"
              className="bg2 border" style={{ width: 200, padding: "8px 12px", borderRadius: 8, fontSize: 13, color: "var(--ink)", outline: "none" }} />
          </form>
          <button onClick={() => exportCsv(d)} className="border bg2 hover-bg row gap-2" style={{ padding: "8px 12px", borderRadius: 8, fontSize: 13, alignItems: "center" }}>
            <Icon name="download" size={14} /> Export
          </button>
          <button onClick={onOpenDocuments} className="btn-gold row gap-2" style={{ padding: "8px 14px", borderRadius: 8, fontSize: 13, alignItems: "center" }}>
            <Icon name="file" size={14} /> Documents
          </button>
        </div>
      </div>

      {/* KPI strip */}
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <Kpi icon="file" label="Documents" value={d.totalDocs ?? 0} sub={`${d.readyDocs ?? 0} ready`} />
        <Kpi icon="layers" label="Pages" value={d.totalPages ?? 0} sub="processed" accent="#8B7FD6" />
        <Kpi icon="database" label="Knowledge base" value={(d.totalChunks ?? 0).toLocaleString()} sub={fmtBytes(d.indexedBytes)} accent="#3FA47A" />
        <Kpi icon="hash" label="Records" value={(d.recordsExtracted ?? 0).toLocaleString()} sub="rows extracted" accent="#E2BC68" />
        <Kpi icon="chat" label="Questions" value={d.questionsAsked ?? 0} sub="asked" accent="#E0A23B" />
        <Kpi icon="check" label="Avg confidence" value={d.avgConfidence != null ? `${Math.round(d.avgConfidence * 100)}%` : "—"} sub="extraction" accent="#6A93C8" />
      </div>

      {/* Alert bar — compact, collapsible, below stats */}
      <AlertBar onOpenDocument={openDoc} />

      {/* workspace body · collapsible library (types + recent) | chat | document */}
      <div style={{ display: "flex", gap: 16, alignItems: "stretch", minHeight: 540 }}>
        {/* LEFT · one merged, collapsible library */}
        {leftOpen ? (
          <aside className="bg1 border rounded-xl" style={{ width: libW, flex: `0 0 ${libW}px`, display: "flex", flexDirection: "column", gap: 14, padding: 14, overflow: "auto", maxHeight: "calc(100vh - 210px)" }}>
            <div className="row between" style={{ alignItems: "center" }}>
              <span className="serif" style={{ fontSize: 15 }}>Library</span>
              <button onClick={() => setLeftOpen(false)} title="Hide library" className="border bg2 hover-bg"
                style={{ width: 26, height: 26, borderRadius: 6, cursor: "pointer", fontSize: 14, color: "var(--ink2)", lineHeight: 1 }}>«</button>
            </div>
            <div>
              <div className="upper ink4" style={{ fontSize: 9, letterSpacing: ".08em", marginBottom: 9 }}>Document types</div>
              {(d.byType || []).length === 0
                ? <div className="ink3" style={{ fontSize: 12, fontStyle: "italic" }}>No classified documents yet.</div>
                : <Bars items={d.byType} />}
            </div>
            <div style={{ borderTop: "1px solid var(--line)", paddingTop: 13 }}>
              <div className="row between" style={{ alignItems: "center", marginBottom: 7 }}>
                <span className="upper ink4" style={{ fontSize: 9, letterSpacing: ".08em" }}>Recent activity</span>
                <button onClick={onOpenDocuments} className="mono" style={{ background: "none", border: "none", cursor: "pointer", fontSize: 10, color: "var(--gold2)" }}>View all →</button>
              </div>
              {(d.recent || []).length === 0 ? (
                <div className="ink3" style={{ fontSize: 12, fontStyle: "italic" }}>No documents yet.</div>
              ) : d.recent.map((r, i) => (
                <button key={r.id} onClick={() => openDoc(r.id)} title={`Open ${r.name}`}
                  className="row hover-bg" style={{ justifyContent: "space-between", alignItems: "center", textAlign: "left", width: "100%", padding: "8px 6px", background: openDocId === r.id ? "rgba(200,160,76,0.12)" : "none", border: "none", cursor: "pointer", borderTop: i ? "1px solid var(--line)" : "none" }}>
                  <div className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
                    <Icon name="file" size={13} />
                    <span className="truncate" style={{ fontSize: 12.5, color: "var(--ink)" }}>{r.name}</span>
                  </div>
                  <span style={{ width: 7, height: 7, borderRadius: 999, flexShrink: 0, background: STATUS_COLOR[r.status] || "var(--ink3)" }} title={r.status} />
                </button>
              ))}
            </div>
          </aside>
        ) : (
          <button onClick={() => setLeftOpen(true)} title="Show library" className="bg1 border rounded-xl hover-bg"
            style={{ width: 32, flex: "0 0 32px", cursor: "pointer", fontSize: 15, color: "var(--ink2)" }}>»</button>
        )}

        {/* drag to resize the library */}
        {leftOpen && <DragDivider getWidth={() => libW} setWidth={setLibW} min={190} max={460} />}

        {/* MIDDLE · chat (collapsible — only when a document is open to fall back to) */}
        {(chatOpen || !openDocId) ? (
          <div style={{ flex: openDocId ? "1 1 0" : "1 1 100%", minWidth: 280, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <AskPanel onOpenDocument={openDoc} onCollapse={openDocId ? () => setChatOpen(false) : undefined} />
          </div>
        ) : (
          <button onClick={() => setChatOpen(true)} title="Show chat" className="bg1 border rounded-xl hover-bg"
            style={{ width: 34, flex: "0 0 34px", cursor: "pointer", fontSize: 14, color: "var(--ink2)", display: "flex", alignItems: "center", justifyContent: "center" }}>💬</button>
        )}

        {/* drag to resize the document pane */}
        {openDocId && <DragDivider getWidth={() => docW} setWidth={setDocW} min={320} max={1100} invert />}

        {/* RIGHT · the opened document (fills when the chat is collapsed) */}
        {openDocId && (
          <div className="bg1 border rounded-xl" style={{
            ...(chatOpen ? { width: docW, flex: `0 0 ${docW}px` } : { flex: "1 1 0" }),
            minWidth: 320, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div className="row between border-b" style={{ alignItems: "center", padding: "9px 12px", flex: "0 0 auto" }}>
              <span className="serif row gap-2" style={{ fontSize: 14, alignItems: "center" }}><Icon name="file" size={13} /> Document</span>
              <button onClick={() => { setOpenDocId(null); setFocus(null); }} className="ink3" title="Close"
                style={{ background: "none", border: "none", cursor: "pointer", fontSize: 14 }}>✕</button>
            </div>
            <div style={{ flex: "1 1 0", minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <DashDocPane docId={openDocId} focus={focus} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Opens a document in the right pane with its field-highlight boxes; `focus` (from a clicked
// citation) pulses the exact field.
function DashDocPane({ docId, focus }) {
  const { data: doc, loading, error } = useApiResource(() => fetchDocument(docId), [docId]);
  if (loading) return <LoadingState label="Opening document…" />;
  if (error) return <ErrorState message={error} />;
  if (!doc) return <div className="ink3" style={{ padding: 20, fontSize: 13 }}>Couldn't open this document.</div>;
  return (
    <DocumentViewer doc={doc} highlights={[]} focusedHl={null} setFocusedHl={() => {}}
                    focusField={focus?.field} focusKey={focus?.at} />
  );
}
