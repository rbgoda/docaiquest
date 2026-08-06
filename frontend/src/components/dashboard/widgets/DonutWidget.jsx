// DonutWidget — SVG donut/ring chart with legend.
import React from "react";

const COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#06B6D4", "#F97316"];

export default function DonutWidget({ data }) {
  const segments = data?.segments || [];
  if (!segments.length) return <Empty label="No data" />;

  const total = segments.reduce((s, seg) => s + (seg.value || 0), 0) || 1;
  const size = 140, r = 55, stroke = 16, cx = size / 2, cy = size / 2;
  const circ = 2 * Math.PI * r;
  let offset = 0;

  return (
    <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
        {segments.map((seg, i) => {
          const pct = seg.value / total;
          const dash = pct * circ;
          const segEl = (
            <circle key={i} cx={cx} cy={cy} r={r} fill="none"
              stroke={COLORS[i % COLORS.length]} strokeWidth={stroke}
              strokeDasharray={`${dash} ${circ - dash}`}
              strokeDashoffset={-offset}
              style={{ transition: "stroke-dasharray 0.3s" }}
              transform={`rotate(-90 ${cx} ${cy})`}
            />
          );
          offset += dash;
          return segEl;
        })}
        <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central"
          style={{ fontSize: 16, fontWeight: 700, fill: "var(--ink)" }}>
          {data.total != null ? (typeof data.total === "number" && data.total > 1000
            ? `$${(data.total / 1000).toFixed(0)}k` : data.total) : total}
        </text>
      </svg>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 140 }}>
        {segments.map((seg, i) => (
          <div key={i} className="row gap-2" style={{ alignItems: "center" }}>
            <span style={{ width: 10, height: 10, borderRadius: 3, background: COLORS[i % COLORS.length], flexShrink: 0 }} />
            <span className="truncate" style={{ fontSize: 12, color: "var(--ink)", flex: 1 }}>{seg.label}</span>
            <span className="mono ink3" style={{ fontSize: 11 }}>{seg.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Empty({ label }) {
  return <div className="ink3" style={{ fontSize: 12, textAlign: "center", padding: 20, fontStyle: "italic" }}>{label}</div>;
}
