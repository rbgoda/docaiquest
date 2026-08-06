// WidgetConfigPanel — slide-out panel for configuring a widget's scope:
// which document types, specific documents, timeframe, chart type, size.

import React, { useState } from "react";

const SIZE_OPTIONS = ["small", "medium", "large", "full"];
const KIND_OPTIONS = ["kpi", "donut", "bars", "trend", "table", "feed", "comparison", "heatmap"];
const MONTHS_OPTIONS = [
  { value: 0, label: "All time" },
  { value: 3, label: "Last 3 months" },
  { value: 6, label: "Last 6 months" },
  { value: 12, label: "Last 12 months" },
  { value: 24, label: "Last 2 years" },
];

export default function WidgetConfigPanel({ widget, onClose, onSave }) {
  const [title, setTitle] = useState(widget?.title || "");
  const [kind, setKind] = useState(widget?.kind || "kpi");
  const [size, setSize] = useState(widget?.size || "medium");
  const [months, setMonths] = useState(widget?.config?.months || 0);
  const [docTypes, setDocTypes] = useState((widget?.config?.docTypes || []).join(", "));

  const save = () => {
    onSave?.({
      ...widget,
      title: title || widget.title,
      kind,
      size,
      config: {
        ...(widget?.config || {}),
        docTypes: docTypes.split(",").map(s => s.trim()).filter(Boolean),
        months,
      },
    });
    onClose?.();
  };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.4)", zIndex: 300, display: "flex", justifyContent: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()} className="bg1" style={{
        width: 380, maxWidth: "90vw", height: "100%", overflowY: "auto",
        borderLeft: "1px solid var(--line)", padding: "20px 18px",
        display: "flex", flexDirection: "column", gap: 16,
      }}>
        <div className="row between" style={{ alignItems: "center" }}>
          <h2 className="serif" style={{ fontSize: 18, margin: 0 }}>Configure Widget</h2>
          <button onClick={onClose} style={{ width: 32, height: 32, borderRadius: 8, background: "var(--bg2)", border: "1px solid var(--line)", cursor: "pointer", fontSize: 14, color: "var(--ink2)" }}>✕</button>
        </div>

        <Field label="Title">
          <input value={title} onChange={(e) => setTitle(e.target.value)}
            className="border bg2" style={inputStyle} />
        </Field>

        <Field label="Chart type">
          <select value={kind} onChange={(e) => setKind(e.target.value)}
            className="border bg2" style={inputStyle}>
            {KIND_OPTIONS.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        </Field>

        <Field label="Size">
          <div className="row gap-2">
            {SIZE_OPTIONS.map(s => (
              <button key={s} onClick={() => setSize(s)} style={{
                padding: "5px 12px", borderRadius: 999, fontSize: 12, cursor: "pointer",
                border: size === s ? "2px solid var(--gold2)" : "1px solid var(--line)",
                background: size === s ? "var(--bgGold)" : "var(--bg2)",
                color: size === s ? "var(--gold2)" : "var(--ink2)",
                fontWeight: size === s ? 600 : 400,
              }}>{s}</button>
            ))}
          </div>
        </Field>

        <Field label="Timeframe">
          <select value={months} onChange={(e) => setMonths(Number(e.target.value))}
            className="border bg2" style={inputStyle}>
            {MONTHS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>

        <Field label="Document types (comma-separated)">
          <input value={docTypes} onChange={(e) => setDocTypes(e.target.value)}
            placeholder="e.g. bank_statement, invoice"
            className="border bg2" style={inputStyle} />
          <div className="ink3" style={{ fontSize: 10, marginTop: 4 }}>
            Leave empty to include all document types.
          </div>
        </Field>

        <div style={{ flex: 1 }} />

        <div className="row gap-3" style={{ justifyContent: "flex-end" }}>
          <button onClick={onClose} className="border bg2" style={btnStyle}>Cancel</button>
          <button onClick={save} style={{ ...btnStyle, background: "var(--gold2)", color: "#1a1408", border: "none" }}>Save</button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label className="upper" style={{ fontSize: 10, color: "var(--ink2)", letterSpacing: ".1em", fontWeight: 600 }}>{label}</label>
      {children}
    </div>
  );
}

const inputStyle = { width: "100%", padding: "8px 10px", borderRadius: 8, fontSize: 13, color: "var(--ink)" };
const btnStyle = { padding: "8px 18px", borderRadius: 999, fontSize: 13, cursor: "pointer", fontWeight: 600 };
