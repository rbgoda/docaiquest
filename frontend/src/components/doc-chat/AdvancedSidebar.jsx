import React, { useEffect, useState } from "react";
import GraphTab from "./GraphTab.jsx";
import ErrorBoundary from "../../views/ErrorBoundary.jsx";
import FieldsTab from "./FieldsTab.jsx";

// Shared quality-data hook — used by both the Advanced sidebar and DocStatsBar.
export function useQuality(docId) {
  const [q, setQ] = useState(null);
  useEffect(() => {
    if (!docId) return;
    let c = false;
    fetch(`/api/documents/${encodeURIComponent(docId)}/quality`)
      .then(r => r.json()).then(d => { if (!c) setQ(d); }).catch(() => {});
    return () => { c = true; };
  }, [docId]);
  return q;
}

// M47 · Unified Advanced Sidebar — replaces all capsule rows and complex layouts.
// One button in top bar. Opens a left sidebar with: Fields, Chunks, Schema,
// Linked, Markdown, Re-extract, Highlight, JSON, layout presets, document expand.
export function AdvancedSidebar({ doc, onCite, onClose, onDocUpdated, onReclassify, reclassifying,
  layout, setLayout, docExpanded, setDocExpanded, width, onResize, locatedField, activeBlockIds = [], fieldBlockMap = {} }) {
  const [tab, setTab] = useState("fields");
  const TABS = [
    ["fields", "📋 Fields"],
    ["graph", "🕸 Graph"],
    ["linked", "🔗 Linked"],
  ];

  return (
    <div style={{ flex: "1 1 0", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
      {/* Tab bar */}
      <div className="row border-b" style={{ flexShrink: 0, padding: "4px 8px", background: "var(--bg2)", gap: 2 }}>
        {TABS.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            style={{ padding: "5px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer",
              background: tab === id ? "var(--gold2)" : "transparent",
              color: tab === id ? "var(--ink)" : "var(--ink3)",
              border: "none", fontWeight: tab === id ? 600 : 400, whiteSpace: "nowrap" }}>
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: 10 }}>
        {tab === "fields" && <FieldsTab doc={doc} onCite={onCite} onDocUpdated={onDocUpdated} locatedField={locatedField} revealed={!!doc.piiRevealed} activeBlockIds={activeBlockIds} fieldBlockMap={fieldBlockMap} />}
        {tab === "graph" && <ErrorBoundary><GraphTab doc={doc} /></ErrorBoundary>}
        {tab === "linked" && <MiniLinkedView doc={doc} onCite={onCite} />}
      </div>
    </div>
  );
}

function MiniLinkedView({ doc, onCite }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!doc?.id) return;
    Promise.all([
      fetch(`/api/documents/${encodeURIComponent(doc.id)}/related`).then(r=>r.json()).catch(()=>({docs:[]})),
    ]).then(([rel]) => setData(rel)).catch(()=>{});
  }, [doc?.id]);
  if (!data) return <div className="ink3" style={{fontSize:12, padding:20, textAlign:"center"}}>Loading…</div>;
  const docs = data.docs || [];
  if (!docs.length) return <div className="ink3" style={{fontSize:12, padding:20, textAlign:"center"}}>No related documents.</div>;
  return docs.map((d,i) => (
    <div key={i} className="border rounded-md mb-2" style={{padding:"7px 9px", background:"var(--bg2)", fontSize:11, color:"var(--ink)"}}>
      <div style={{fontWeight:600}}>{d.name||d.id}</div>
      <div className="ink3" style={{fontSize:10}}>{d.docType||""} · {d.relation||"related"}</div>
    </div>
  ));
}
