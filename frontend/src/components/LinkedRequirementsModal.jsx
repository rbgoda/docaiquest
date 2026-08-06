// LinkedRequirementsModal — displays all requirements linked to a document
// in a flat, scrollable table grouped by framework.
// Extracted from views/AllDocuments.jsx (refactoring Phase 2b).

import React, { useMemo } from "react";
import { Pill } from "./Shell.jsx";
import Modal from "./Modal.jsx";
import { prettyType } from "../format.js";

const thStyle = {
  padding: "10px 14px",
  textAlign: "left",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.08,
  fontWeight: 600,
  color: "var(--ink3)",
  borderBottom: "1px solid var(--line)",
  whiteSpace: "nowrap",
};
const tdStyle = {
  padding: "10px 14px",
  verticalAlign: "middle",
};


export default function LinkedRequirementsModal({ doc, reqs, onClose }) {
  // Group requirements by framework label. Sort groups by their
  // highest-confidence entry desc so the most confident framework
  // matches surface first.
  const grouped = useMemo(() => {
    const g = new Map();
    for (const r of reqs) {
      const key = r.group || "Unfiled";
      if (!g.has(key)) g.set(key, []);
      g.get(key).push(r);
    }
    for (const items of g.values()) {
      items.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    }
    return Array.from(g.entries()).sort((a, b) => {
      const ma = Math.max(...a[1].map(r => r.confidence || 0));
      const mb = Math.max(...b[1].map(r => r.confidence || 0));
      return mb - ma;
    });
  }, [reqs]);

  const jumpToReview = (reqId) => {
    try {
      localStorage.setItem("docaiq.deeplink",
        JSON.stringify({ view: "review", requirementId: reqId, fromDocId: doc.id, ts: Date.now() }));
    } catch {}
    window.dispatchEvent(new CustomEvent("docaiq:deeplink",
      { detail: { view: "review", requirementId: reqId, fromDocId: doc.id } }));
    onClose();
  };

  return (
    <Modal open onClose={onClose} maxWidth={1040}
      panelStyle={{ display: "flex", flexDirection: "column", overflow: "hidden",
                    height: "min(720px, calc(100vh - 64px))", boxShadow: "0 16px 48px rgba(0,0,0,0.5)" }}>
      <div style={{ display: "flex", flexDirection: "column", overflow: "hidden", height: "100%" }}>
        {/* Header */}
        <div className="row between p-4 border-b" style={{ alignItems: "center", background: "var(--bg2)", flex: "0 0 auto" }}>
          <div style={{ minWidth: 0 }}>
            <div className="upper ink3" style={{ fontSize: 10, letterSpacing: 0.6 }}>
              Linked requirements
            </div>
            <div className="row gap-2 mt-1" style={{ alignItems: "center", minWidth: 0 }}>
              <span className="serif font-semibold truncate" style={{ maxWidth: 560, fontSize: 16 }}>
                {doc.name}
              </span>
              {doc.docType && (
                <Pill color="violet">{prettyType(doc.docType)}</Pill>
              )}
            </div>
            <div className="ink3 mt-1" style={{ fontSize: 11 }}>
              {reqs.length} requirement{reqs.length === 1 ? "" : "s"} satisfied across {grouped.length} framework group{grouped.length === 1 ? "" : "s"}
            </div>
          </div>
          <button onClick={onClose}
                  className="hover-bg ink3"
                  style={{ padding: 8, borderRadius: 4, fontSize: 16, lineHeight: 1, cursor: "pointer" }}>×</button>
        </div>

        {/* Single flat table with framework separator rows */}
        <div style={{ flex: "1 1 0", overflow: "auto", minHeight: 0 }}>
          {reqs.length === 0 ? (
            <div className="ink3 text-sm p-6" style={{ textAlign: "center", fontStyle: "italic" }}>
              No requirements are linked to this document.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg2)", zIndex: 1 }}>
                <tr>
                  <th style={{...thStyle, width: 56}}>Status</th>
                  <th style={{...thStyle, width: 150}}>Requirement</th>
                  <th style={{...thStyle}}>Title</th>
                  <th style={{...thStyle, width: 90, textAlign: "right"}}>Conf</th>
                  <th style={{...thStyle, width: 120, textAlign: "right"}}>Action</th>
                </tr>
              </thead>
              <tbody>
                {grouped.map(([groupLabel, items]) => (
                  <React.Fragment key={groupLabel}>
                    {/* Framework separator row */}
                    <tr>
                      <td colSpan={5} style={{
                        background: "rgba(200,160,76,0.08)",
                        borderTop: "1px solid rgba(200,160,76,0.25)",
                        borderBottom: "1px solid rgba(200,160,76,0.25)",
                        padding: "8px 14px",
                      }}>
                        <span className="upper" style={{
                          fontSize: 10, letterSpacing: 0.8, fontWeight: 700,
                          color: "var(--gold2)",
                        }}>
                          {groupLabel}
                        </span>
                        <span className="ink3 ml-3" style={{ fontSize: 10 }}>
                          {items.length} requirement{items.length === 1 ? "" : "s"}
                        </span>
                      </td>
                    </tr>
                    {items.map(r => {
                      const pct = r.confidence != null ? Math.round(r.confidence * 100) : null;
                      const pctColor = pct == null ? "neutral" : pct >= 85 ? "emerald" : pct >= 60 ? "amber" : "rose";
                      const statusColor = r.status === "ok" ? "emerald" :
                                          r.status === "warn" ? "amber" :
                                          r.status === "miss" ? "rose" : "neutral";
                      const statusChar = r.status === "ok" ? "✓" : r.status === "miss" ? "✗" : r.status === "warn" ? "!" : "·";
                      return (
                        <tr key={r.id} className="hover-bg" style={{ borderBottom: "1px solid var(--line)" }}>
                          <td style={tdStyle}>
                            <Pill color={statusColor}>{statusChar}</Pill>
                          </td>
                          <td style={{...tdStyle}} className="mono font-medium">{r.id}</td>
                          <td style={tdStyle}>
                            <div style={{ lineHeight: 1.4 }}>{r.title}</div>
                            {r.subtitle && (
                              <div className="ink3 mt-1" style={{ fontSize: 11 }}>{r.subtitle}</div>
                            )}
                          </td>
                          <td style={{...tdStyle, textAlign: "right"}}>
                            {pct != null ? <Pill color={pctColor}>{pct}%</Pill> : <span className="ink3">—</span>}
                          </td>
                          <td style={{...tdStyle, textAlign: "right"}}>
                            <button
                              onClick={() => jumpToReview(r.id)}
                              className="btn-gold"
                              style={{ padding: "4px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}
                            >
                              Review →
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="row between p-3 border-t" style={{ background: "var(--bg2)", flex: "0 0 auto", alignItems: "center" }}>
          <div className="ink3" style={{ fontSize: 11 }}>
            ESC to close · Click any "Review →" to open that requirement
          </div>
          <button onClick={onClose}
                  className="border bg1 hover-bg"
                  style={{ padding: "5px 14px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
            Close
          </button>
        </div>
      </div>
    </Modal>
  );
}
