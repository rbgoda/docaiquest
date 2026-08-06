// TrendWidget — SVG line/area chart over time.
import React from "react";

export default function TrendWidget({ data }) {
  const points = data?.points || [];
  if (points.length < 2) return <div className="ink3" style={{ fontSize: 12, textAlign: "center", padding: 20, fontStyle: "italic" }}>Not enough data for a trend</div>;

  const w = 400, h = 180, pad = { top: 16, right: 16, bottom: 28, left: 50 };
  const pw = w - pad.left - pad.right;
  const ph = h - pad.top - pad.bottom;

  const vals = points.map(p => p.value);
  const minV = Math.min(0, ...vals);
  const maxV = Math.max(...vals, 1);
  const range = maxV - minV || 1;

  const xScale = (i) => pad.left + (i / Math.max(points.length - 1, 1)) * pw;
  const yScale = (v) => pad.top + ph - ((v - minV) / range) * ph;

  const linePath = points.map((p, i) =>
    `${i === 0 ? "M" : "L"}${xScale(i)},${yScale(p.value)}`
  ).join(" ");

  const areaPath = linePath +
    ` L${xScale(points.length - 1)},${pad.top + ph}` +
    ` L${xScale(0)},${pad.top + ph} Z`;

  // Y-axis labels
  const yTicks = 4;
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => {
    const v = minV + (range / yTicks) * i;
    return { v, y: yScale(v) };
  });

  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ maxWidth: "100%" }}>
      {/* Grid lines */}
      {yLabels.map((t, i) => (
        <g key={i}>
          <line x1={pad.left} x2={w - pad.right} y1={t.y} y2={t.y}
            stroke="var(--line)" strokeWidth={0.5} />
          <text x={pad.left - 6} y={t.y + 4} textAnchor="end"
            style={{ fontSize: 9, fill: "var(--ink3)" }}>
            {t.v > 1000 ? `${(t.v / 1000).toFixed(0)}k` : t.v}
          </text>
        </g>
      ))}

      {/* Area fill */}
      <path d={areaPath} fill="rgba(59,130,246,0.12)" />

      {/* Line */}
      <path d={linePath} fill="none" stroke="#3B82F6" strokeWidth={2} />

      {/* Dots */}
      {points.map((p, i) => (
        <circle key={i} cx={xScale(i)} cy={yScale(p.value)} r={3} fill="#3B82F6" />
      ))}

      {/* X-axis labels (show ~5 evenly spaced) */}
      {points.filter((_, i) => i % Math.max(1, Math.floor(points.length / 5)) === 0 || i === points.length - 1).map((p, _, arr) => {
        const idx = points.indexOf(p);
        return (
          <text key={idx} x={xScale(idx)} y={h - 6} textAnchor="middle"
            style={{ fontSize: 8, fill: "var(--ink3)" }}>
            {p.date.slice(0, 7)}
          </text>
        );
      })}
    </svg>
  );
}
