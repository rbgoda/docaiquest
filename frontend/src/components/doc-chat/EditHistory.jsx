// EditHistory · audit-trail list of every HITL field edit on a document.
// Extracted from views/DocumentChatPanel.jsx (TODO #29). Pure leaf —
// fetches its own data via useApiResource, renders the timeline.
import React from "react";
import { fetchEditHistory } from "../../api";
import { useApiResource } from "../../api/useApi.js";

export default function EditHistory({ docId }) {
  const { data, loading, error } = useApiResource(() => fetchEditHistory(docId), [docId]);
  if (loading) return <div className="ink3 mt-2 text-xs">Loading history…</div>;
  if (error) return <div className="text-xs mt-2" style={{ color: "#D8625E" }}>{error}</div>;
  if (!data || data.length === 0) return <div className="ink3 mt-2 text-xs" style={{ fontStyle: "italic" }}>No edits yet.</div>;
  return (
    <div className="mt-2" style={{ fontSize: 11 }}>
      {data.map((h) => (
        <div key={h.pk} className="border-l-2 pl-2 mb-2"
             style={{ borderColor: "rgba(200,160,76,0.4)" }}>
          <div className="mono ink2">{h.fieldPath}</div>
          <div className="ink3" style={{ fontSize: 10 }}>
            {h.editedBy} · {h.editedAt ? new Date(h.editedAt).toLocaleString() : ""}
          </div>
          <div className="mt-1">
            <span style={{ color: "#D8625E", textDecoration: "line-through" }}>
              {h.originalValue || "(empty)"}
            </span>
            {" → "}
            <span style={{ color: "#3FA47A" }}>{h.newValue || "(empty)"}</span>
          </div>
          {h.reason && <div className="ink3 mt-1" style={{ fontStyle: "italic", fontSize: 10 }}>"{h.reason}"</div>}
        </div>
      ))}
    </div>
  );
}
