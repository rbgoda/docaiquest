// BarsWidget — horizontal ranked bar chart.
import React from "react";

const COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#06B6D4", "#F97316"];

export default function BarsWidget({ data }) {
  const bars = data?.bars || [];
  if (!bars.length) return <div className="ink3" style={{ fontSize: 12, textAlign: "center", padding: 20, fontStyle: "italic" }}>No data</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {bars.map((bar, i) => (
        <div key={i} className="row gap-3" style={{ alignItems: "center" }}>
          <span className="truncate" style={{ width: 100, fontSize: 12, color: "var(--ink)", flexShrink: 0, textAlign: "right" }}>
            {bar.label}
          </span>
          <div style={{ flex: 1, height: 18, background: "var(--bg2)", borderRadius: 4, overflow: "hidden" }}>
            <div style={{
              width: `${Math.max(bar.pct || 2, 2)}%`, height: "100%",
              background: COLORS[i % COLORS.length], borderRadius: 4,
              transition: "width 0.3s",
            }} />
          </div>
          <span className="mono ink3" style={{ fontSize: 11, flexShrink: 0, width: 60 }}>
            {typeof bar.value === "number" ? (bar.value > 1000 ? `$${(bar.value / 1000).toFixed(0)}k` : `$${bar.value}`) : bar.value}
          </span>
        </div>
      ))}
    </div>
  );
}
