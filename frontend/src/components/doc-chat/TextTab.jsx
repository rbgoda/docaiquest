// TextTab — unified tab merging Chunks + Rendered Markdown behind a simple toggle.
// Replaces the separate "Chunks" primary tab + "Markdown" on-demand tab.
//
// Props:
//   doc      — the document object (id)
//   onCite   — bbox jump callback (passed to ChunksTab)
//   revealed — PII reveal state (passed to MarkdownTab)
import React, { useState } from "react";
import { ChunksTab } from "./DocPanels.jsx";
import { MarkdownTab } from "./MarkdownTab.jsx";

export default function TextTab({ doc, onCite, focusedChunkPk, revealed, activeBlockIds = [], blockFieldMap = {} }) {
  const [view, setView] = useState("chunks"); // "chunks" | "rendered"

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      {/* Toggle pills */}
      <div className="row gap-2" style={{
        padding: "6px 10px", borderBottom: "1px solid var(--line)", flexShrink: 0,
        background: "var(--bg2)",
      }}>
        <button onClick={() => setView("chunks")}
          className={view === "chunks" ? "btn-gold" : "border bg1"}
          style={{ padding: "5px 14px", borderRadius: 14, fontSize: 12, cursor: "pointer", fontWeight: 600 }}>
          🧩 Chunks
        </button>
        <button onClick={() => setView("rendered")}
          className={view === "rendered" ? "btn-gold" : "border bg1"}
          style={{ padding: "5px 14px", borderRadius: 14, fontSize: 12, cursor: "pointer", fontWeight: 600 }}>
          📄 Markdown
        </button>
      </div>
      {/* Content — each child handles its own data fetching + internal overflow */}
      <div style={{ flex: 1, minHeight: 0 }}>
        {view === "chunks" ? (
          <ChunksTab doc={doc} onCite={onCite} focusedChunkPk={focusedChunkPk} />
        ) : (
          <MarkdownTab docId={doc.id} doc={doc} revealed={revealed} onCite={onCite} activeBlockIds={activeBlockIds} blockFieldMap={blockFieldMap} />
        )}
      </div>
    </div>
  );
}
