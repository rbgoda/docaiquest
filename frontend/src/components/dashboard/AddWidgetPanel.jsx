// AddWidgetPanel — slide-out panel listing available widgets by module category.
// User clicks a widget to add it to the dashboard.

import React, { useState } from "react";

const MODULE_ICONS = {
  finance: "💰", expense: "💳", accounting: "🧾", identity: "🛡", health: "🩺",
};
const MODULE_LABELS = {
  finance: "Finance & Investments", expense: "Expense Tracking",
  accounting: "Accounting & Payables", identity: "Identity & Compliance", health: "Health & Lab Results",
};
const KIND_LABELS = {
  kpi: "Stat card", donut: "Donut chart", bars: "Bar chart", trend: "Trend line",
  table: "Data table", feed: "Alert feed", comparison: "Comparison", heatmap: "Heatmap",
};

export default function AddWidgetPanel({ onClose, onAdd, builtinWidgets, aiWidgets, onProposeAi }) {
  const [aiLoading, setAiLoading] = useState(false);
  const propose = async () => { setAiLoading(true); await onProposeAi?.(); setAiLoading(false); };

  // Group by module
  const grouped = new Map();
  for (const w of builtinWidgets || []) {
    const m = w.module || "other";
    if (!grouped.has(m)) grouped.set(m, []);
    grouped.get(m).push(w);
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.4)", zIndex: 300, display: "flex", justifyContent: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()} className="bg1" style={{
        width: 380, maxWidth: "90vw", height: "100%", overflowY: "auto",
        borderLeft: "1px solid var(--line)", padding: "20px 18px",
      }}>
        <div className="row between" style={{ alignItems: "center", marginBottom: 16 }}>
          <h2 className="serif" style={{ fontSize: 18, margin: 0 }}>Add Widget</h2>
          <button onClick={onClose} style={iconBtn}>✕</button>
        </div>

        {/* AI propose button */}
        <button onClick={propose} disabled={aiLoading}
          className="border bg2 hover-bg w-full" style={{
            padding: "10px 14px", borderRadius: 10, cursor: "pointer", marginBottom: 18,
            fontSize: 13, color: "var(--ink)", textAlign: "left",
          }}>
          <span style={{ marginRight: 8 }}>✨</span>
          {aiLoading ? "Thinking…" : "Suggest widgets with AI"}
        </button>

        {/* AI-proposed widgets */}
        {(aiWidgets || []).length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div className="upper ink3" style={{ fontSize: 10, letterSpacing: ".1em", marginBottom: 8 }}>AI suggestions</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {aiWidgets.map((w, i) => (
                <WidgetOption key={w.id || i} widget={w} onAdd={onAdd} />
              ))}
            </div>
          </div>
        )}

        {/* Built-in widgets by module */}
        {[...grouped.entries()].map(([mod, widgets]) => (
          <div key={mod} style={{ marginBottom: 16 }}>
            <div className="upper ink3" style={{ fontSize: 10, letterSpacing: ".1em", marginBottom: 8 }}>
              {MODULE_ICONS[mod] || "📊"} {MODULE_LABELS[mod] || mod}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {widgets.map((w, i) => (
                <WidgetOption key={w.id || i} widget={w} onAdd={onAdd} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function WidgetOption({ widget, onAdd }) {
  return (
    <button onClick={() => onAdd?.(widget)} className="border bg2 hover-bg"
      style={{ padding: "10px 12px", borderRadius: 8, cursor: "pointer", textAlign: "left",
               border: "1px solid var(--line)", background: "var(--bg1)", width: "100%" }}>
      <div className="row gap-2" style={{ alignItems: "center" }}>
        <span style={{ fontSize: 13 }}>{KIND_LABELS[widget.kind] || widget.kind}</span>
        {widget.source === "ai" && (
          <span className="upper mono" style={{ fontSize: 8, padding: "2px 5px", borderRadius: 4,
            background: "rgba(139,127,214,0.15)", color: "#8B7FD6" }}>AI</span>
        )}
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)", marginTop: 2 }}>{widget.title}</div>
      <div className="ink3" style={{ fontSize: 11, marginTop: 2 }}>
        {(widget.config?.docTypes || []).slice(0, 3).join(", ")}
      </div>
    </button>
  );
}

const iconBtn = { width: 32, height: 32, borderRadius: 8, background: "var(--bg2)", border: "1px solid var(--line)", cursor: "pointer", fontSize: 14, color: "var(--ink2)" };
