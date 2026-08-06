// KpiWidget — big number + label + subtitle + optional trend arrow.
import React from "react";

export default function KpiWidget({ data }) {
  const value = data?.value ?? "—";
  const sub = data?.subtitle || "";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: 100 }}>
      <div className="serif" style={{ fontSize: 40, fontWeight: 700, color: "var(--ink)", lineHeight: 1 }}>
        {value}
      </div>
      {sub && (
        <div className="ink3" style={{ fontSize: 12, marginTop: 6 }}>{sub}</div>
      )}
    </div>
  );
}
