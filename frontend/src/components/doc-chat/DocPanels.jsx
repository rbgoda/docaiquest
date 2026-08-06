// Extracted from DocumentChatPanel.jsx.
import MiniMarkdown from "./MiniMarkdown.jsx";
import { addFieldFromRegion, fetchDocChunks, fetchGraphDuplicates, fetchGraphPayments, fetchRecallGaps, fetchRelatedDocuments, patchDocChunk } from "../../api";
import { useApiResource } from "../../api/useApi.js";
import DuplicateReviewModal from "../../views/DuplicateReviewModal.jsx";
import { ErrorState, LoadingState, Pill } from "../Shell.jsx";
import { useEffect, useMemo, useRef, useState } from "react";

function ChunksTab({ doc, onCite, focusedChunkPk }) {
  const { data, loading, error } = useApiResource(() => fetchDocChunks(doc.id), [doc.id]);
  const [chunks, setChunks] = useState(null);
  useEffect(() => { if (data?.chunks) setChunks(data.chunks); }, [data]);
  const [editing, setEditing] = useState(null);   // chunk pk being edited
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(null);
  const [selectedPk, setSelectedPk] = useState(null);  // which chunk is highlighted
  const cardRefs = useRef({});  // chunk pk → DOM node for scroll-into-view

  // When a citation is clicked in chat (or onCite fires from outside), scroll
  // the matching chunk card into view and highlight it.
  useEffect(() => {
    if (focusedChunkPk != null && cardRefs.current[focusedChunkPk]) {
      setSelectedPk(focusedChunkPk);
      cardRefs.current[focusedChunkPk].scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusedChunkPk]);

  if (loading) return <LoadingState label="Loading chunks…" />;
  if (error)   return <ErrorState message={error} />;
  const list = chunks || [];
  if (list.length === 0) {
    return <div className="p-4 ink3 text-sm" style={{ fontStyle: "italic" }}>
      No chunks yet — this document hasn't been chunked/embedded, or is still processing.
    </div>;
  }

  const update = (pk, patch) => setChunks(cs => cs.map(c => c.pk === pk ? { ...c, ...patch } : c));
  const toggle = async (c) => {
    setBusy(c.pk);
    try { const r = await patchDocChunk(doc.id, c.pk, { disabled: !c.disabled }); update(c.pk, { disabled: r.disabled }); }
    catch { /* ignore */ } finally { setBusy(null); }
  };
  const saveEdit = async (c) => {
    if (!draft.trim() || draft === c.text) { setEditing(null); return; }
    setBusy(c.pk);
    try { const r = await patchDocChunk(doc.id, c.pk, { text: draft }); update(c.pk, { text: r.text }); setEditing(null); }
    catch { /* stay open */ } finally { setBusy(null); }
  };
  const nOff = list.filter(c => c.disabled).length;

  return (
    <div className="p-4" style={{ overflow: "auto" }}>
      <div className="mb-3">
        <div className="upper ink3" style={{ fontSize: 10 }}>Retrieval chunks {selectedPk ? `— #${selectedPk} selected` : ""}</div>
        <div className="ink3 mt-1" style={{ fontSize: 11 }}>
          {list.length} chunk{list.length === 1 ? "" : "s"} feed search{nOff ? ` · ${nOff} excluded` : ""}.
          Click one to locate it on the page; edit to correct + re-embed; toggle to exclude from search.
        </div>
      </div>
      <div className="flex col gap-2">
        {list.map(c => (
          <div key={c.pk} ref={(el) => { cardRefs.current[c.pk] = el; }} className="bg2 border rounded-md p-3"
               style={{
                 opacity: c.disabled ? 0.5 : 1,
                 borderColor: selectedPk === c.pk ? "#E2BC68" : (c.disabled ? "var(--line)" : undefined),
                 borderWidth: selectedPk === c.pk ? "2px" : undefined,
                 background: selectedPk === c.pk ? "rgba(226,188,104,0.22)" : undefined,
                 boxShadow: selectedPk === c.pk ? "0 0 6px rgba(200,160,76,0.5)" : undefined,
                 transition: "border-color 0.15s, background 0.15s, box-shadow 0.15s",
               }}>
            <div className="row between" style={{ alignItems: "center", gap: 8, marginBottom: 6 }}>
              <div className="row gap-2" style={{ alignItems: "center" }}>
                <span className="mono ink3" style={{ fontSize: 10 }}>#{c.index}</span>
                <Pill color="neutral">p.{c.page}</Pill>
                {c.kind !== "text" && <Pill color="neutral">{c.kind}</Pill>}
                {c.disabled && <Pill color="rose">excluded</Pill>}
              </div>
              <div className="row gap-1">
                {c.bbox && (
                  <button onClick={() => {
                    if (onCite) {
                      setSelectedPk(c.pk);
                      onCite({ page: c.page, bbox: c.bbox, chunkPk: c.pk, quote: (c.text||'').slice(0,120) }, 0);
                    }
                  }}
                          title="Highlight on the document" className="border bg2"
                          style={{ fontSize: 10, padding: "3px 8px", borderRadius: 4, cursor: "pointer" }}>◎ locate</button>
                )}
                {editing !== c.pk && (
                  <button onClick={() => { setEditing(c.pk); setDraft(c.text); }} className="border bg2"
                          style={{ fontSize: 10, padding: "3px 8px", borderRadius: 4, cursor: "pointer" }}>✎ edit</button>
                )}
                <button onClick={() => toggle(c)} disabled={busy === c.pk} className="border bg2"
                        style={{ fontSize: 10, padding: "3px 8px", borderRadius: 4, cursor: "pointer" }}>
                  {c.disabled ? "＋ include" : "✕ exclude"}
                </button>
              </div>
            </div>
            {editing === c.pk ? (
              <div>
                <textarea value={draft} onChange={e => setDraft(e.target.value)} autoFocus
                          className="bg2 border" style={{ width: "100%", boxSizing: "border-box", minHeight: 90,
                          fontSize: 12, padding: 8, borderRadius: 4, color: "var(--ink)", resize: "vertical" }} />
                <div className="row gap-1 mt-2">
                  <button onClick={() => saveEdit(c)} disabled={busy === c.pk} className="btn-gold"
                          style={{ fontSize: 11, padding: "4px 12px", borderRadius: 4, cursor: "pointer" }}>
                    {busy === c.pk ? "Saving…" : "Save + re-embed"}</button>
                  <button onClick={() => setEditing(null)} className="border bg2"
                          style={{ fontSize: 11, padding: "4px 12px", borderRadius: 4, cursor: "pointer" }}>Cancel</button>
                </div>
              </div>
            ) : c.kind === "table" ? (
              // Table chunks carry GitHub-flavoured Markdown tables — render them as
              // real tables (via MiniMarkdown) instead of raw pipes (feedback: chunks
              // not in proper table format). Scroll wide tables inside their own box.
              <div onClick={() => {
                     if (c.bbox && onCite) {
                       setSelectedPk(c.pk);
                       onCite({ page: c.page, bbox: c.bbox, chunkPk: c.pk, quote: (c.text||'').slice(0,120) }, 0);
                     }
                   }}
                   style={{ fontSize: 12, cursor: c.bbox ? "pointer" : "default", overflowX: "auto" }}>
                <MiniMarkdown source={c.text} />
              </div>
            ) : (
              <div onClick={() => {
                     if (c.bbox && onCite) {
                       setSelectedPk(c.pk);
                       onCite({ page: c.page, bbox: c.bbox, chunkPk: c.pk, quote: (c.text||'').slice(0,120) }, 0);
                     }
                   }}
                   style={{ fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", cursor: c.bbox ? "pointer" : "default" }}>
                {c.text}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function LinkedTab({ doc }) {
  const { data, loading, error } = useApiResource(() => fetchRelatedDocuments(doc.id), [doc.id]);
  if (loading) return <LoadingState label="Finding duplicates + related documents…" />;
  if (error)   return <ErrorState message={error}/>;

  const dups = data?.duplicates || [];
  const related = data?.related || [];
  const openDoc = (docId) => window.dispatchEvent(
    new CustomEvent("docaiq:select-doc", { detail: { docId } }));

  if (dups.length === 0 && related.length === 0) {
    return (
      <div className="p-4 ink3 text-sm" style={{ fontStyle: "italic" }}>
        No duplicates or related documents found. Duplicates are same-type copies (matching
        number, or same issuer + amount + date); related documents share a person or organization.
      </div>
    );
  }

  return (
    <div className="p-4" style={{ overflow: "auto" }}>
      {dups.length > 0 && (
        <div className="mb-4">
          <div className="upper ink3 mb-2" style={{ fontSize: 10 }}>⚠ Possible duplicates</div>
          <div className="flex col gap-2">
            {dups.map(d => (
              <button key={d.id} onClick={() => openDoc(d.id)}
                      className="bg2 border rounded-md p-3 text-left"
                      style={{ borderColor: "var(--amber, #E2BC68)", cursor: "pointer", width: "100%" }}>
                <div className="row between" style={{ alignItems: "center", gap: 8 }}>
                  <div className="font-medium" style={{ fontSize: 13 }}>{d.name}</div>
                  <Pill color={d.confidence >= 0.9 ? "rose" : "amber"}>
                    {Math.round((d.confidence || 0) * 100)}% match
                  </Pill>
                </div>
                <div className="ink3 mt-1" style={{ fontSize: 11 }}>{d.reason}</div>
              </button>
            ))}
          </div>
        </div>
      )}
      {related.length > 0 && (
        <div>
          <div className="upper ink3 mb-2" style={{ fontSize: 10 }}>Related documents</div>
          <div className="ink3 mb-2" style={{ fontSize: 11 }}>
            {related.length} document{related.length === 1 ? "" : "s"} share a person or organization with this one
          </div>
          <div className="flex col gap-2">
            {related.map(r => (
              <button key={r.id} onClick={() => openDoc(r.id)}
                      className="bg2 border rounded-md p-3 text-left"
                      style={{ cursor: "pointer", width: "100%" }}>
                <div className="font-medium" style={{ fontSize: 13 }}>{r.name}</div>
                {r.shared?.length > 0 && (
                  <div className="row gap-1 mt-2" style={{ flexWrap: "wrap" }}>
                    {r.shared.map((s, i) => (
                      <span key={i} className="border" style={{ fontSize: 10, padding: "1px 7px", borderRadius: 999 }}>{s}</span>
                    ))}
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ── Reconciliation banner — shows duplicate / paid-by-transaction signals ─
// Fetches the graph reconcile endpoints scoped to this doc's vendor and
// flags the doc with a red duplicate warning or a green reconciled badge.
// ── ReviewStatusActions · doc-level sign-off pill + Approve/Exception ────
//
// Lives in the chat-panel header so the reviewer signs off without
// leaving the doc. Three states cycle:
//   pending   → grey pill + Approve + Exception buttons
//   reviewed  → green pill "✓ Reviewed by X · today" + Reopen button
//   exception → red   pill "! Exception"           + Reopen button
//
// Re-clicking Approve on a reviewed doc just refreshes the timestamp.
function ReviewStatusActions({ status, busy, onMark, reviewedBy, reviewedAt }) {
  const formatted = reviewedAt
    ? new Date(reviewedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : null;
  if (status === "reviewed") {
    return (
      <>
        <span title={reviewedBy ? `Reviewed by ${reviewedBy}` : "Reviewed"}
              className="row gap-1"
              style={{
                padding: "3px 10px", borderRadius: 14, fontSize: 11,
                background: "rgba(63,164,122,0.18)", color: "#3FA47A",
                fontWeight: 600, border: "1px solid rgba(63,164,122,0.4)",
                alignItems: "center", whiteSpace: "nowrap",
              }}>
          ✓ Reviewed{formatted ? ` · ${formatted}` : ""}
        </span>
        <button
          onClick={() => onMark?.("pending")}
          disabled={busy}
          title="Reopen — back to pending"
          className="border bg2 hover-bg ink3"
          style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: busy ? "wait" : "pointer" }}>
          Reopen
        </button>
      </>
    );
  }
  if (status === "exception") {
    return (
      <>
        <span title={reviewedBy ? `Flagged by ${reviewedBy}` : "Flagged exception"}
              className="row gap-1"
              style={{
                padding: "3px 10px", borderRadius: 14, fontSize: 11,
                background: "rgba(216,98,94,0.18)", color: "#D8625E",
                fontWeight: 600, border: "1px solid rgba(216,98,94,0.4)",
                alignItems: "center", whiteSpace: "nowrap",
              }}>
          ! Exception{formatted ? ` · ${formatted}` : ""}
        </span>
        <button
          onClick={() => onMark?.("pending")}
          disabled={busy}
          title="Reopen — back to pending"
          className="border bg2 hover-bg ink3"
          style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: busy ? "wait" : "pointer" }}>
          Reopen
        </button>
      </>
    );
  }
  // pending
  return (
    <>
      <button
        onClick={() => onMark?.("reviewed")}
        disabled={busy}
        title="Mark this document as reviewed — clean for audit"
        className="hover-bg"
        style={{
          padding: "4px 12px", borderRadius: 4, fontSize: 11, cursor: busy ? "wait" : "pointer",
          background: "rgba(63,164,122,0.18)", color: "#3FA47A",
          border: "1px solid rgba(63,164,122,0.55)", fontWeight: 600,
        }}>
        {busy ? "…" : "✓ Mark reviewed"}
      </button>
      <button
        onClick={() => onMark?.("exception")}
        disabled={busy}
        title="Mark exception — capture a reason for the audit trail"
        className="hover-bg"
        style={{
          padding: "4px 12px", borderRadius: 4, fontSize: 11, cursor: busy ? "wait" : "pointer",
          background: "rgba(216,98,94,0.10)", color: "#D8625E",
          border: "1px solid rgba(216,98,94,0.55)", fontWeight: 600,
        }}>
        ! Exception
      </button>
    </>
  );
}


function ReconcileBanner({ doc }) {
  const [reviewPair, setReviewPair] = useState(null);

  const vendorPk = doc.vendorPk;
  // Only fetch when this doc is a receipt or bank statement — the only
  // types reconcile produces edges for. Avoids needless calls on other docs.
  const isReceiptish = ["receipt", "expense_claim"].includes(doc.docType);
  const isBankish = ["bank_statement", "audited_financial_statement"].includes(doc.docType);
  const { data: duplicates } = useApiResource(
    () => isReceiptish ? fetchGraphDuplicates(vendorPk) : Promise.resolve([]),
    [doc.id, vendorPk]
  );
  const { data: payments } = useApiResource(
    () => (isReceiptish || isBankish) ? fetchGraphPayments(vendorPk) : Promise.resolve([]),
    [doc.id, vendorPk]
  );

  const dupPair = useMemo(() => {
    if (!duplicates) return null;
    return duplicates.find(p => p.a?.docId === doc.id || p.b?.docId === doc.id);
  }, [duplicates, doc.id]);
  const paid = useMemo(() => {
    if (!payments) return null;
    return payments.find(p => p.receipt?.docId === doc.id);
  }, [payments, doc.id]);

  if (!dupPair && !paid) return null;

  return (
    <>
      {dupPair && (() => {
        const other = dupPair.a?.docId === doc.id ? dupPair.b : dupPair.a;
        const meta = dupPair.metadata || {};
        const pct = dupPair.confidence != null ? Math.round(dupPair.confidence * 100) : null;
        return (
          <div className="row between px-3 py-2" style={{
            background: "rgba(216,98,94,0.18)",
            borderBottom: "1px solid rgba(216,98,94,0.45)",
            fontSize: 11, lineHeight: 1.4, alignItems: "center", gap: 8,
          }}>
            <div style={{ minWidth: 0 }}>
              ⚠ Likely duplicate of <span className="mono">{other?.docId}</span>{" "}
              <span className="ink3">({other?.name})</span>
              {pct != null && <span className="ml-2">· {pct}% confident</span>}
              <div className="ink3 mt-1" style={{ fontSize: 10 }}>
                Match signals: {(meta.signals || []).join(", ")} ·
                amount {meta.amount}
                {meta.day_delta != null ? ` · ${meta.day_delta}-day gap` : ""}
              </div>
            </div>
            <button
              onClick={() => setReviewPair(dupPair)}
              title="Open side-by-side review"
              style={{
                padding: "4px 12px", borderRadius: 4, fontSize: 11,
                border: "1px solid rgba(216,98,94,0.55)",
                background: "rgba(216,98,94,0.20)",
                color: "#D8625E", fontWeight: 600,
                cursor: "pointer", whiteSpace: "nowrap",
              }}
            >
              Review →
            </button>
          </div>
        );
      })()}
      {paid && (() => {
        const meta = paid.metadata || {};
        const pct = paid.confidence != null ? Math.round(paid.confidence * 100) : null;
        return (
          <div className="px-3 py-2" style={{
            background: "rgba(63,164,122,0.18)",
            borderBottom: "1px solid rgba(63,164,122,0.45)",
            fontSize: 11, lineHeight: 1.4,
          }}>
            ✓ Reconciled to bank transaction on {meta.transaction_date || "—"}
            {pct != null && <span className="ml-2">· {pct}% confident</span>}
            <div className="ink3 mt-1" style={{ fontSize: 10 }}>
              {meta.txn_description || "transaction"} · cleared{" "}
              {meta.days_after != null ? `${meta.days_after} day(s) after receipt` : "in the same statement period"}
              {paid.bankStatement?.docId && (
                <>
                  {" · source "}
                  <span className="mono">{paid.bankStatement.docId}</span>
                </>
              )}
            </div>
          </div>
        );
      })()}
      {reviewPair && (
        <DuplicateReviewModal
          pair={reviewPair}
          onClose={() => setReviewPair(null)}
          onResolved={() => {
            // Hard refresh — the dup banner won't re-render with the same
            // edge after a dismissal / delete, and the doc list above
            // needs to pick up the deletion. Cheaper than wiring callbacks
            // through 4 levels of parents.
            setReviewPair(null);
            window.location.reload();
          }}
        />
      )}
    </>
  );
}


// ── Why-this-needs-review banner · M28 ───────────────────────────────────
//
// Renders ABOVE the doc body when the doc is `pending` and the backend
// returned one or more review_reasons. Each reason has {code, severity,
// message, hint}. Severity drives the color and the leading icon.
//
// When the doc is already reviewed (by human or by `ai-auto`), this banner
// disappears — the ReviewStatusActions pill in the header takes over.

const SEVERITY_STYLE = {
  block: { color: "#D8625E", bg: "rgba(216,98,94,0.10)", border: "rgba(216,98,94,0.45)", icon: "✗" },
  warn:  { color: "#E0A23B", bg: "rgba(224,162,59,0.10)", border: "rgba(224,162,59,0.45)", icon: "!" },
  info:  { color: "var(--ink3)", bg: "var(--bg2)",        border: "var(--line)",          icon: "i" },
};

// Recall-gap: structured-looking spans the extractor missed. Lazy scan → list of
// candidate fields, each locatable on the page and one-click addable (region→field, reusing
// the located box). Best-effort; false positives are cheap to ignore.
function RecallGapsPanel({ doc, onCite, onDocUpdated }) {
  const [gaps, setGaps] = useState(null);   // null = not scanned yet
  const [loading, setLoading] = useState(false);
  const [busyIdx, setBusyIdx] = useState(-1);
  const scan = async () => {
    setLoading(true);
    try { const r = await fetchRecallGaps(doc.id); setGaps(r.gaps || []); }
    catch { setGaps([]); }
    finally { setLoading(false); }
  };
  const norm = (bb) => (bb && bb.page_w && bb.page_h)
    ? [bb.x0 / bb.page_w, bb.y0 / bb.page_h, bb.x1 / bb.page_w, bb.y1 / bb.page_h]
    : (bb ? [bb.x0, bb.y0, bb.x1, bb.y1] : null);
  const add = async (g, i) => {
    if (!g.bbox) return;
    setBusyIdx(i);
    try {
      const fresh = await addFieldFromRegion(doc.id, {
        label: `${g.kind}_${i + 1}`, page: g.bbox.page || 1, bbox: norm(g.bbox),
      });
      if (onDocUpdated) onDocUpdated(fresh);
      setGaps((gs) => gs.filter((_, j) => j !== i));
    } catch { /* ignore */ } finally { setBusyIdx(-1); }
  };
  return (
    <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--line)", fontSize: 12 }}>
      {gaps == null ? (
        <button onClick={scan} disabled={loading} className="border bg2"
                style={{ fontSize: 11, padding: "4px 10px", borderRadius: 4, cursor: "pointer" }}>
          {loading ? "Scanning…" : "🔍 Scan for missed fields"}
        </button>
      ) : gaps.length === 0 ? (
        <span className="ink3">No obvious missed fields — every structured value is in the schema.</span>
      ) : (
        <div>
          <div className="ink3" style={{ marginBottom: 6 }}>
            Possibly missed ({gaps.length}) — structured values not in the schema:
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {gaps.map((g, i) => (
              <div key={i} className="row gap-1" style={{ alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 9, textTransform: "uppercase", color: "var(--gold)", minWidth: 46 }}>{g.kind}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={g.value}>{g.value}</span>
                {g.bbox && (
                  <button onClick={() => onCite && onCite({ page: g.bbox.page || 1, bbox: g.bbox })}
                          title="Locate on the page" className="ink3 hover-bg"
                          style={{ border: "none", background: "none", cursor: "pointer", fontSize: 12 }}>◎</button>
                )}
                {g.bbox && (
                  <button onClick={() => add(g, i)} disabled={busyIdx === i} title="Add as a field"
                          className="border bg2" style={{ fontSize: 9, padding: "1px 7px", borderRadius: 4, cursor: "pointer" }}>
                    {busyIdx === i ? "…" : "+ add"}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Framing fix (trust `state`): a parsed-but-unstructured doc (e.g. an unsupported type
// like a 49-page deed) is NOT "low accuracy" — its content is fully captured, it just has
// no schema. Reframe it as a positive affordance pointing at the whole-doc Markdown export,
// instead of letting it read as a failed extraction.
function UnstructuredNotice({ doc, onViewMarkdown }) {
  if (doc?.trust?.state !== "unstructured") return null;
  return (
    <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg2)", fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span className="upper" style={{ fontSize: 10, letterSpacing: 0.6, fontWeight: 600, color: "var(--ink3)" }}>
          Parsed · not structured
        </span>
        <span className="ink3" style={{ flex: 1, minWidth: 160 }}>
          This document type isn’t structured yet — the content is fully captured, not a low-accuracy result.
          View or download the whole document as Markdown.
        </span>
        <button onClick={onViewMarkdown} className="border bg2"
                style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          View as Markdown
        </button>
      </div>
    </div>
  );
}

function WhyReviewBanner({ doc, status }) {
  const allReasons = doc?.reviewReasons || [];
  // `info`-severity reasons (e.g. "doctype_unsupported" for statements)
  // are bookkeeping — don't shout them at the reviewer. The banner only
  // shows when there's a genuine warn/block to act on.
  const reasons = allReasons.filter(r => r.severity !== "info");
  if (status !== "pending" || reasons.length === 0) return null;
  const worst = reasons.some(r => r.severity === "block") ? "block"
               : reasons.some(r => r.severity === "warn") ? "warn"
               : "info";
  const s = SEVERITY_STYLE[worst];
  return (
    <div style={{
      padding: "10px 14px",
      borderBottom: `1px solid ${s.border}`,
      background: s.bg,
      fontSize: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span className="upper" style={{
          fontSize: 10, letterSpacing: 0.6, color: s.color, fontWeight: 600,
        }}>
          Why this needs review
        </span>
        <span className="ink3" style={{ fontSize: 10 }}>
          {reasons.length} signal{reasons.length === 1 ? "" : "s"} from the AI · resolve to unblock auto-approve
        </span>
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
        {reasons.map((r, i) => {
          const rs = SEVERITY_STYLE[r.severity] || SEVERITY_STYLE.info;
          return (
            <li key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
              <span className="mono" style={{
                color: rs.color, fontWeight: 700, fontSize: 11,
                minWidth: 14, textAlign: "center", lineHeight: "16px",
              }}>
                {rs.icon}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: rs.color, fontWeight: 500 }}>{r.message}</div>
                {r.hint && (
                  <div className="ink3" style={{ fontSize: 11, marginTop: 1 }}>
                    {r.hint}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}


// ── Markdown fallback viewer for office/binary docs (docx/pptx/…) ─────────
// The browser + PDF.js can't render Word/PowerPoint. Rather than fail, we show
// the document's EXTRACTED markdown (POST /documents/{id}/markdown — DocAIQ
// already materializes it from the parsed text). Not pixel-faithful, but the
// full content is readable. (Faithful layout would need a server-side
// LibreOffice→PDF render of the original; tracked as a follow-up.)

export { ChunksTab, LinkedTab, ReviewStatusActions, ReconcileBanner, RecallGapsPanel, UnstructuredNotice, WhyReviewBanner };
