// DuplicateReviewModal · side-by-side comparison of a flagged duplicate.
//
// Why this exists: the reconciler flags a duplicate purely from the
// extracted facts (vendor + amount + date all match). The reviewer needs
// to *see* the two source receipts to decide whether the flag is real or
// a false positive. This modal renders both images at the same scale,
// pulls the match signals out of the edge's metadata into a labelled
// comparison table, and exposes three terminal actions:
//
//   - Keep both → dismiss the duplicate edge (false positive)
//   - Delete A   → remove receipt A entirely (cascades graph entities)
//   - Delete B   → remove receipt B entirely
//
// Reachable from:
//   - The ⚠ Likely duplicate banner on each receipt's chat panel ("Review")
//   - The Expenses tab's Duplicate findings card (eventual per-row Review)

import React, { useState } from "react";
import { Pill } from "../components/Shell.jsx";
import Icon from "../components/Icon.jsx";
import { useConfirm } from "../components/ConfirmDialog.jsx";
import {
  documentFileUrl, deleteDocument, dismissDuplicate,
} from "../api";


export default function DuplicateReviewModal({ pair, onClose, onResolved }) {
  const confirmDialog = useConfirm();
  // pair shape (from /api/graph/reconcile/duplicates):
  //   { relationPk, confidence, metadata: { vendor, amount, a_date, b_date,
  //     day_delta, signals: [...] }, a: {docId, docPk, name}, b: {...} }
  const [busy, setBusy] = useState(null);  // 'dismiss' | 'delete-a' | 'delete-b' | null
  const [error, setError] = useState(null);

  // ESC closes for keyboard parity with other overlays.
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && busy == null) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  const meta = pair?.metadata || {};
  const pct = pair?.confidence != null ? Math.round(pair.confidence * 100) : null;

  const handleDismiss = async () => {
    if (busy) return;
    setBusy("dismiss"); setError(null);
    try {
      await dismissDuplicate(pair.relationPk);
      onResolved?.({ kind: "dismiss" });
      onClose();
    } catch (e) {
      setError(`Dismiss failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (side) => {
    if (busy) return;
    const target = side === "a" ? pair.a : pair.b;
    const ok = await confirmDialog({
      title: `Delete ${target.name}?`,
      body: "This removes the receipt + all its graph entities. The other receipt stays. This cannot be undone.",
      confirmLabel: "Delete receipt",
      destructive: true,
    });
    if (!ok) return;
    setBusy(`delete-${side}`); setError(null);
    try {
      await deleteDocument(target.docId);
      onResolved?.({ kind: "delete", side, docId: target.docId });
      onClose();
    } catch (e) {
      setError(`Delete failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && busy == null) onClose(); }}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.62)",
        zIndex: 80, display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
    >
      <div className="bg1 border rounded-xl" style={{
        width: "min(1200px, 100%)",
        height: "min(820px, calc(100vh - 64px))",
        display: "flex", flexDirection: "column", overflow: "hidden",
        boxShadow: "0 16px 48px rgba(0,0,0,0.5)",
      }}>
        {/* Header */}
        <div className="row between p-4 border-b" style={{ alignItems: "center", background: "var(--bg2)", flex: "0 0 auto" }}>
          <div>
            <div className="upper" style={{ fontSize: 10, letterSpacing: 0.6, color: "#D8625E" }}>
              ⚠ Duplicate review
            </div>
            <div className="row gap-2 mt-1" style={{ alignItems: "center" }}>
              <span className="serif font-semibold" style={{ fontSize: 16 }}>
                {meta.vendor || "Unknown vendor"}
              </span>
              <Pill color="rose">{pct != null ? `${pct}% match` : "match"}</Pill>
            </div>
            <div className="ink3 mt-1" style={{ fontSize: 11 }}>
              Two receipts share vendor, amount, and date. Reviewer decides whether this is a duplicate filing or a legitimate pair.
            </div>
          </div>
          <button onClick={onClose}
                  disabled={busy != null}
                  className="hover-bg ink3"
                  style={{ padding: 8, borderRadius: 4, fontSize: 16, lineHeight: 1, cursor: busy != null ? "wait" : "pointer" }}>×</button>
        </div>

        {/* Match signals strip */}
        <div className="row p-3 gap-3 border-b" style={{ background: "var(--bg2)", flex: "0 0 auto", flexWrap: "wrap" }}>
          <SignalChip label="Vendor"   value={meta.vendor} matched/>
          <SignalChip label="Amount"   value={meta.amount} matched/>
          <SignalChip
            label="Date"
            value={meta.day_delta === 0 ? meta.a_date : `${meta.a_date} ↔ ${meta.b_date}`}
            matched={meta.day_delta === 0}
            tooltip={meta.day_delta != null ? `${meta.day_delta}-day gap` : null}
          />
          <SignalChip
            label="Claimant"
            value={meta.claimant || "not extracted on either"}
            matched={meta.claimant != null}
            muted={meta.claimant == null}
          />
        </div>

        {/* Side-by-side images */}
        <div style={{ flex: "1 1 0", overflow: "hidden", display: "grid", gridTemplateColumns: "1fr 1px 1fr" }}>
          <ReceiptColumn
            label="A"
            doc={pair.a}
            date={meta.a_date}
            onDelete={() => handleDelete("a")}
            busy={busy === "delete-a"}
            disabled={busy != null}
          />
          <div style={{ background: "var(--line)" }}/>
          <ReceiptColumn
            label="B"
            doc={pair.b}
            date={meta.b_date}
            onDelete={() => handleDelete("b")}
            busy={busy === "delete-b"}
            disabled={busy != null}
          />
        </div>

        {/* Footer */}
        {error && (
          <div className="px-3 py-2 border-t" style={{ background: "rgba(216,98,94,0.18)", fontSize: 11 }}>
            {error}
          </div>
        )}
        <div className="row between p-3 border-t" style={{ background: "var(--bg2)", flex: "0 0 auto", alignItems: "center" }}>
          <div className="ink3" style={{ fontSize: 11 }}>
            ESC to close · Choose Delete on whichever receipt should be removed, or Keep both if this is a legitimate pair.
          </div>
          <div className="row gap-2">
            <button
              onClick={handleDismiss}
              disabled={busy != null}
              className="border bg1 hover-bg"
              style={{ padding: "5px 14px", borderRadius: 4, fontSize: 11, cursor: busy != null ? "wait" : "pointer" }}
            >
              {busy === "dismiss" ? "Dismissing…" : "Keep both (false positive)"}
            </button>
            <button
              onClick={onClose}
              disabled={busy != null}
              className="border bg1 hover-bg"
              style={{ padding: "5px 14px", borderRadius: 4, fontSize: 11, cursor: busy != null ? "wait" : "pointer" }}
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}


function SignalChip({ label, value, matched, muted, tooltip }) {
  const bg = matched ? "rgba(216,98,94,0.20)" :
             muted   ? "var(--bg1)" :
                       "rgba(63,164,122,0.18)";
  const borderColor = matched ? "rgba(216,98,94,0.55)" :
                      muted   ? "var(--line)" :
                                "rgba(63,164,122,0.4)";
  return (
    <div title={tooltip || undefined}
         className="row gap-2"
         style={{
           padding: "4px 10px", borderRadius: 14,
           border: `1px solid ${borderColor}`, background: bg,
           fontSize: 11, alignItems: "center",
         }}>
      <span className="ink3" style={{ fontSize: 10 }}>{label}</span>
      <span className="mono">{value || "—"}</span>
      {matched && <span style={{ color: "#D8625E", fontWeight: 700 }}>≡</span>}
    </div>
  );
}


function ReceiptColumn({ label, doc, date, onDelete, busy, disabled }) {
  const src = documentFileUrl(doc.docId);
  return (
    <div className="flex col" style={{ overflow: "hidden", minHeight: 0 }}>
      <div className="row between p-3 border-b" style={{ alignItems: "center", flex: "0 0 auto" }}>
        <div style={{ minWidth: 0 }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span className="serif font-semibold" style={{ fontSize: 14 }}>Receipt {label}</span>
            <span className="mono ink3 text-xs">{doc.docId}</span>
          </div>
          <div className="ink2 truncate mt-1" style={{ fontSize: 12, maxWidth: 380 }}>{doc.name}</div>
          <div className="ink3 mt-1" style={{ fontSize: 10 }}>dated {date || "—"}</div>
        </div>
        <button
          onClick={onDelete}
          disabled={disabled}
          title={`Delete ${doc.name}`}
          style={{
            padding: "5px 12px", borderRadius: 4, fontSize: 11,
            border: "1px solid rgba(216,98,94,0.55)",
            background: "rgba(216,98,94,0.10)",
            color: "#D8625E", fontWeight: 600,
            cursor: disabled ? "wait" : "pointer",
          }}
        >
          {busy ? "Deleting…" : "Delete this one"}
        </button>
      </div>
      <div style={{ flex: "1 1 0", overflow: "auto", padding: 16, display: "flex", justifyContent: "center", background: "#0d0d0e" }}>
        <img
          src={src}
          alt={doc.name}
          style={{
            maxWidth: "100%", height: "auto", borderRadius: 6,
            boxShadow: "0 2px 12px rgba(0,0,0,0.4)",
          }}
        />
      </div>
    </div>
  );
}
