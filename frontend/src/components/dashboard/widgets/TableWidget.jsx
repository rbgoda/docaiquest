// TableWidget — sortable data grid.
import React from "react";

export default function TableWidget({ data }) {
  const columns = data?.columns || ["Field", "Value"];
  const rows = data?.rows || [];
  if (!rows.length) return <div className="ink3" style={{ fontSize: 12, textAlign: "center", padding: 20, fontStyle: "italic" }}>No data</div>;

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th key={i} style={{
                textAlign: "left", padding: "6px 10px",
                borderBottom: "2px solid var(--line)", color: "var(--ink2)",
                fontSize: 10, fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase",
              }}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} style={{ borderBottom: "1px solid var(--line)" }}>
              {columns.map((col, ci) => (
                <td key={ci} style={{
                  padding: "6px 10px", color: "var(--ink)",
                  maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {row[col.toLowerCase()] || row[col] || row[ci] || ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
