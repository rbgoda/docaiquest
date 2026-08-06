// AttachRequirementModal — pick a requirement to attach the current doc to.
// Extracted from views/AllDocuments.jsx (refactoring Phase 2b).

import React, { useMemo, useState } from "react";
import Modal from "./Modal.jsx";

export default function AttachRequirementModal({ doc, requirements, onClose, onPick }) {
  const [filter, setFilter] = useState("");

  // Group by framework. Filter by id/title/group.
  const grouped = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const filtered = (requirements || []).filter(r => {
      if (!q) return true;
      return r.id.toLowerCase().includes(q) ||
             (r.title || "").toLowerCase().includes(q) ||
             (r.group || "").toLowerCase().includes(q);
    });
    const map = new Map();
    for (const r of filtered) {
      const g = r.group || "—";
      if (!map.has(g)) map.set(g, []);
      map.get(g).push(r);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [requirements, filter]);

  return (
    <Modal open onClose={onClose} maxWidth={720}
      panelStyle={{ display: "flex", flexDirection: "column", overflow: "hidden",
                    maxHeight: "calc(100vh - 32px)" }}>
        <div className="row between p-4 border-b" style={{ alignItems: "flex-start" }}>
          <div className="min0">
            <div className="serif font-semibold text-lg">Attach document to a requirement</div>
            <div className="ink3 text-sm mt-1 truncate" title={doc.name}>
              {doc.name} <span className="mono ink4">· {doc.id}</span>
            </div>
          </div>
          <button onClick={onClose}
                  style={{ background: "none", border: "none", fontSize: 18, color: "var(--ink3)", cursor: "pointer", padding: "0 4px" }}>
            ×
          </button>
        </div>
        <div className="p-3 border-b">
          <input
            autoFocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search by id, title, or framework…"
            className="bg2 border"
            style={{ width: "100%", padding: "8px 10px", borderRadius: 4, fontSize: 13, color: "var(--ink)", outline: "none" }}
          />
        </div>
        <div className="grow overflow-auto">
          {grouped.length === 0 && (
            <div className="ink3 p-6 text-sm" style={{ textAlign: "center", fontStyle: "italic" }}>
              {filter ? `No requirement matches "${filter}".` : "No requirements available."}
            </div>
          )}
          {grouped.map(([group, items]) => (
            <div key={group}>
              <div className="ink3" style={{
                fontSize: 10, padding: "6px 14px", textTransform: "uppercase", letterSpacing: ".08em",
                background: "var(--bg2)", borderBottom: "1px solid var(--line)",
              }}>{group}</div>
              {items.map(r => {
                const hasOther = r.docId && r.docId !== doc.id;
                return (
                  <button key={r.id}
                          onClick={() => onPick(r.id)}
                          className="row hover-bg"
                          style={{
                            width: "100%", padding: "10px 14px", gap: 10, alignItems: "center",
                            background: "transparent", border: "none", borderBottom: "1px solid var(--line)",
                            textAlign: "left", cursor: "pointer", fontFamily: "inherit",
                          }}>
                    <span className="mono text-xs ink3" style={{ minWidth: 90 }}>{r.id}</span>
                    <span className="text-sm grow truncate">{r.title}</span>
                    {hasOther && (
                      <span className="ink3" style={{ fontSize: 10, fontStyle: "italic" }}>
                        currently → {r.docId}
                      </span>
                    )}
                    <span className="ink3" style={{ fontSize: 16 }}>›</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
    </Modal>
  );
}
