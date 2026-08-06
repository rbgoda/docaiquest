// ComparisonWidget — side-by-side KPI pair (this vs last, actual vs expected).
import React from "react";

export default function ComparisonWidget({ data }) {
  const left = data?.left || { label: "—", value: "—" };
  const right = data?.right || { label: "—", value: "—" };
  const match = data?.match;

  return (
    <div style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
      <div className="bg2 border rounded-lg" style={{ flex: 1, padding: "14px 16px", textAlign: "center" }}>
        <div className="upper ink3" style={{ fontSize: 10, letterSpacing: ".1em" }}>{left.label}</div>
        <div className="serif" style={{ fontSize: 22, fontWeight: 700, color: "var(--ink)", marginTop: 6 }}>{left.value}</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", color: "var(--ink3)", fontSize: 18 }}>=</div>
      <div className="bg2 border rounded-lg" style={{ flex: 1, padding: "14px 16px", textAlign: "center" }}>
        <div className="upper ink3" style={{ fontSize: 10, letterSpacing: ".1em" }}>{right.label}</div>
        <div className="serif" style={{ fontSize: 22, fontWeight: 700, color: "var(--ink)", marginTop: 6 }}>{right.value}</div>
      </div>
      {match != null && (
        <div style={{ position: "absolute", bottom: 8, right: 8, fontSize: 11, color: match ? "var(--emerald)" : "var(--rose)" }}>
          {match ? "✓ Matches" : "✗ Doesn't match"}
        </div>
      )}
    </div>
  );
}
