// HeatmapWidget — color-coded matrix (parameter × time), for lab results etc.
import React from "react";

const CELL_COLORS = {
  normal:    "rgba(16,185,129,0.25)",
  borderline: "rgba(245,158,11,0.25)",
  abnormal:  "rgba(239,68,68,0.25)",
};

export default function HeatmapWidget({ data }) {
  const rows = data?.rows || [];
  const columns = data?.columns || [];
  if (!rows.length) return <div className="ink3" style={{ fontSize: 12, textAlign: "center", padding: 20, fontStyle: "italic" }}>No data</div>;

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
        <thead>
          <tr>
            <th style={thStyle}>Parameter</th>
            {columns.map((col, i) => (
              <th key={i} style={{ ...thStyle, textAlign: "center" }}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              <td style={tdStyle}>
                <span style={{ fontWeight: 500 }}>{row.parameter || row.label || ""}</span>
                {row.unit && <span className="ink3" style={{ fontSize: 10, marginLeft: 4 }}>{row.unit}</span>}
              </td>
              {(row.cells || row.values || []).map((cell, ci) => {
                const status = cell?.status || cell || "normal";
                const val = cell?.value ?? (typeof cell === "string" ? cell : "—");
                return (
                  <td key={ci} style={{
                    ...tdStyle, textAlign: "center",
                    background: CELL_COLORS[status] || "transparent",
                    borderRadius: 4, fontWeight: status === "abnormal" ? 600 : 400,
                    color: status === "abnormal" ? "var(--rose)" : status === "borderline" ? "#E0A23B" : "var(--ink)",
                  }}>
                    {val}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const thStyle = {
  textAlign: "left", padding: "5px 8px", borderBottom: "2px solid var(--line)",
  color: "var(--ink2)", fontSize: 10, fontWeight: 600, letterSpacing: ".03em",
  textTransform: "uppercase",
};
const tdStyle = {
  padding: "5px 8px", borderBottom: "1px solid var(--line)", color: "var(--ink)",
};
