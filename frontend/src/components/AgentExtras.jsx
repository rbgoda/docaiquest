// M51 · shared UI for the agentic workspace chat — rendered under an AI answer.
//   · ArtifactBar — ⬇ download buttons for tool outputs (CSV).
//   · StepTrace   — collapsible "what the assistant did" tool trace.
// Used by BOTH cross-doc chat surfaces (DocumentsDashboard AskPanel +
// AllDocuments WorkspaceChat) so they never drift.
import React, { useState } from "react";

export function downloadArtifact(a) {
  let blob;
  if (a.encoding === "base64") {
    // Binary artifact (e.g. .xlsx) — decode base64 → bytes.
    const bin = atob(a.content || "");
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    blob = new Blob([bytes], { type: a.mime || "application/octet-stream" });
  } else {
    blob = new Blob([a.content || ""], { type: a.mime || "text/csv;charset=utf-8" });
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = a.filename || (a.type === "xlsx" ? "export.xlsx" : "extract.csv");
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function ArtifactBar({ artifacts }) {
  if (!artifacts || artifacts.length === 0) return null;
  return (
    <div className="row gap-1 mt-1" style={{ flexWrap: "wrap" }}>
      {artifacts.map((a, i) => (
        <button key={i} onClick={() => downloadArtifact(a)} className="btn-gold"
          style={{ fontSize: 11, padding: "4px 11px", borderRadius: 10, cursor: "pointer" }}
          title="Download">
          ⬇ Download {a.filename || "extract.csv"}
        </button>
      ))}
    </div>
  );
}

// Citations — the audit trail under a RAG answer. Each chip names its source
// (document · page); clicking opens the doc when an onOpen handler is given,
// otherwise it reveals the exact quoted text the answer was grounded on.
export function Citations({ items, onOpen }) {
  const [openIdx, setOpenIdx] = useState(null);
  if (!items || items.length === 0) return null;
  const active = openIdx != null ? items[openIdx] : null;
  return (
    <div className="mt-1">
      <div className="row gap-1" style={{ flexWrap: "wrap", alignItems: "center" }}>
        <span className="ink3" style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: ".06em" }}>
          Sources
        </span>
        {items.map((c, i) => (
          <button key={i}
            onClick={() => onOpen && c.docId ? onOpen(c) : setOpenIdx(openIdx === i ? null : i)}
            title={onOpen ? `Open ${c.docName || c.docId}` : (c.quote || "Show source text")}
            className="border bg1 ink2 mono"
            style={{ fontSize: 10, padding: "2px 7px", borderRadius: 10, cursor: "pointer" }}>
            {(c.docName || c.docId || "source")}{c.page != null ? ` · p${c.page}` : ""}
          </button>
        ))}
      </div>
      {active && active.quote && (
        <div className="bg2 border rounded mt-1" style={{ padding: "6px 8px", fontSize: 11, color: "var(--ink2)" }}>
          <span style={{ fontStyle: "italic" }}>“{active.quote}”</span>
          <div className="ink3 mono mt-1" style={{ fontSize: 9 }}>
            — {active.docName || active.docId} · p{active.page}
          </div>
        </div>
      )}
    </div>
  );
}

export function StepTrace({ steps }) {
  const [open, setOpen] = useState(false);
  if (!steps || steps.length === 0) return null;
  const ICON = { ok: "✓", error: "✕", confirm: "?" };
  const COLOR = { ok: "var(--emerald)", error: "var(--rose)", confirm: "var(--amber)" };
  return (
    <div className="mt-1">
      <button onClick={() => setOpen(o => !o)} className="ink3 mono"
        style={{ background: "none", border: "none", fontSize: 10, cursor: "pointer", padding: 0 }}>
        🛠 {steps.length} step{steps.length === 1 ? "" : "s"} {open ? "▾" : "▸"}
      </button>
      {open && (
        <div className="bg1 border rounded mt-1" style={{ padding: "6px 8px" }}>
          {steps.map((s, i) => (
            <div key={i} className="row gap-2" style={{ alignItems: "baseline", fontSize: 11, padding: "2px 0" }}>
              <span style={{ color: COLOR[s.status] || "var(--ink3)" }}>{ICON[s.status] || "•"}</span>
              <span className="mono ink3" style={{ minWidth: 96, flex: "0 0 auto" }}>{s.tool}</span>
              <span className="ink2 grow">{s.summary}</span>
              {s.ms != null && <span className="ink3 mono" style={{ fontSize: 10, flex: "0 0 auto" }}>{s.ms}ms</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
