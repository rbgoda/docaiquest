// FieldsTab — unified tab merging the editable field table + Schema/JSON view
// behind a simple toggle. Replaces the separate "Fields" pane + "Schema" on-demand tab.
//
// Props:
//   doc            — the document object (id, extractedFields)
//   onCite         — bbox jump callback
//   onDocUpdated   — called after field edits
//   locatedField   — field name to highlight (from chat citations)
//   revealed       — PII reveal state (busts JSON cache)
import React, { useState } from "react";
import MiniFieldsList from "./MiniFieldsList.jsx";
import { JsonTab } from "./JsonTab.jsx";

export default function FieldsTab({ doc, onCite, onDocUpdated, locatedField, revealed, activeBlockIds = [], fieldBlockMap = {} }) {
  const [view, setView] = useState("table"); // "table" | "json"

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      {/* Toggle pills */}
      <div className="row gap-2" style={{
        padding: "6px 10px", borderBottom: "1px solid var(--line)", flexShrink: 0,
        background: "var(--bg2)",
      }}>
        <button onClick={() => setView("table")}
          className={view === "table" ? "btn-gold" : "border bg1"}
          style={{ padding: "5px 14px", borderRadius: 14, fontSize: 12, cursor: "pointer", fontWeight: 600 }}>
          📋 Table
        </button>
        <button onClick={() => setView("json")}
          className={view === "json" ? "btn-gold" : "border bg1"}
          style={{ padding: "5px 14px", borderRadius: 14, fontSize: 12, cursor: "pointer", fontWeight: 600 }}>
          {"{ } JSON"}
        </button>
      </div>
      {/* Content */}
      <div style={{ flex: 1, overflow: "auto", padding: 10, minHeight: 0 }}>
        {view === "table" ? (
          <MiniFieldsList doc={doc} onCite={onCite} onDocUpdated={onDocUpdated} locatedField={locatedField} activeBlockIds={activeBlockIds} fieldBlockMap={fieldBlockMap} />
        ) : (
          <JsonTab docId={doc.id} extractedFields={doc.extractedFields} revealed={revealed} />
        )}
      </div>
    </div>
  );
}
