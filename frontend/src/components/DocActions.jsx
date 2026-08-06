// DocActions — icon-row rendered in the "Actions" column.
// Extracted from views/AllDocuments.jsx (refactoring Phase 2b).

export default function DocActions({ doc, matchedCount, busy, onDelete, onRematch, onAttach, onCreateAlert, canManage = true, auditActions = true, isDocsProduct = false }) {
  const isUnmatched = matchedCount === 0;
  const isOther = !doc.docType || ["other", "unknown", "document"].includes(doc.docType);
  const stop = (e) => e.stopPropagation();
  const btn = (title, glyph, onClick, color = "var(--ink2)", disabled = false) => (
    <button
      onClick={(e) => { stop(e); onClick(); }}
      disabled={busy || disabled}
      title={title}
      className="hover-bg"
      style={{
        padding: "2px 6px", borderRadius: 4, fontSize: 13, lineHeight: 1.2,
        background: "transparent", border: "1px solid var(--line)",
        color, cursor: busy || disabled ? "not-allowed" : "pointer",
        opacity: busy ? 0.5 : 1,
      }}
    >{glyph}</button>
  );
  return (
    <div className="row gap-1" style={{ justifyContent: "flex-end" }}>
      {isDocsProduct && canManage && btn(
        "Create alert rule for this document",
        "🔔", () => onCreateAlert?.(doc), "var(--gold2)"
      )}
      {isDocsProduct && canManage && isOther && doc.ingestionStatus === "ready" && btn(
        "Reclassify · AI will re-examine this document's type",
        "🔄", onRematch, "var(--violet)"
      )}
      {auditActions && canManage && isUnmatched && doc.ingestionStatus === "ready" && btn(
        "Re-run matcher · re-fire AI to look for matching requirements",
        "↻", onRematch
      )}
      {auditActions && canManage && isUnmatched && btn(
        "Attach to a requirement manually",
        "🔗", onAttach
      )}
      {btn(
        "Delete · falls back to archive if a closed audit references this",
        "🗑", onDelete, "#D8625E"
      )}
    </div>
  );
}
