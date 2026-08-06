// Analytics — an on-demand dashboard *builder*. Pick a theme (Financial, Expense,
// …); it lists the documents of that theme's types as a tick-to-include checklist;
// building aggregates the selected docs' already-extracted fields into a dashboard
// (metric cards + inline-SVG bars/trend + tables). Doc-type → theme mapping and all
// aggregation live server-side (app/analytics_themes.py); this view just renders.
import React, { useState, useMemo, useEffect } from "react";
import { useApiResource } from "../api/useApi.js";
import { fetchAnalyticsDashboards, buildAnalyticsDashboard, fetchAnalyticsInsights } from "../api/documents";
import { LoadingState, ErrorState } from "../components/Shell.jsx";
import { openPrintReport } from "./analyticsPrint.js";
import { prettyType } from "../format.js";

const ACCENT = "#3FA47A";
const GOLD = "#E0A23B";
const NEG = "#D8625E";

function fmtMoney(v, ccy) {
  if (typeof v !== "number") return v == null ? "—" : String(v);
  const s = Math.abs(v) >= 1000 || Number.isInteger(v)
    ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return ccy ? `${ccy} ${s}` : s;
}
const PALETTE = ["#4C8DD8", "#3FB27F", "#E6A93C", "#E06C5E", "#8B7FD6", "#3FB2A6", "#DA7FB8", "#B0B54A"];

function MetricCard({ m, ccy, accent }) {
  const isNum = typeof m.value === "number";
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "0", minWidth: 0, overflow: "hidden" }}>
      <div style={{ height: 3, background: accent || ACCENT }} />
      <div style={{ padding: "14px 16px 15px" }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: ".12em", textTransform: "uppercase", color: "var(--muted, #8a94a6)", marginBottom: 10 }}>{m.label}</div>
        <div className="serif" style={{ fontSize: 32, lineHeight: 1, letterSpacing: "-.02em" }}>
          {isNum ? fmtMoney(m.value, m.unit || ccy) : m.value}
        </div>
        {m.sub ? <div className="mono" style={{ fontSize: 11.5, color: "var(--muted, #8a94a6)", marginTop: 10 }}>{m.sub}</div> : null}
      </div>
    </div>
  );
}

const TONE = {
  positive: { c: "#3FB27F", ic: "✓" }, watch: { c: "#E6A93C", ic: "!" },
  risk: { c: "#E06C5E", ic: "▲" }, info: { c: "#4C8DD8", ic: "i" },
};

function Ribbon({ items, ccy }) {
  const list = (items || []).filter((i) => Math.abs(i.value) > 0);
  const total = list.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
  if (!list.length) return null;
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "13px 16px", marginBottom: 14 }}>
      <div style={{ display: "flex", height: 16, borderRadius: 6, overflow: "hidden", marginBottom: 11 }}>
        {list.map((it, i) => (
          <span key={i} title={`${it.label} ${Math.round((Math.abs(it.value) / total) * 100)}%`}
            style={{ width: `${(Math.abs(it.value) / total) * 100}%`, background: it.color || PALETTE[i % PALETTE.length] }} />
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px" }}>
        {list.map((it, i) => (
          <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: it.color || PALETTE[i % PALETTE.length] }} />
            <span style={{ color: "var(--ink2,#c7cfda)" }}>{it.label}</span>
            <span className="mono" style={{ color: "var(--muted,#8a94a6)" }}>{Math.round((Math.abs(it.value) / total) * 100)}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function InsightsPanel({ data }) {
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px", marginBottom: 16 }}>
      <div className="row between" style={{ alignItems: "baseline", marginBottom: 13 }}>
        <div className="serif" style={{ fontSize: 16 }}>✨ AI insights</div>
        <span className="mono" style={{ fontSize: 10, color: "var(--muted,#8a94a6)" }}>generated from this view</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(250px,1fr))", gap: 10 }}>
        {(data.insights || []).map((it, i) => {
          const t = TONE[it.tone] || TONE.info;
          return (
            <div key={i} style={{ display: "flex", gap: 10, padding: "11px 12px", borderRadius: 8, background: `${t.c}14`, border: `1px solid ${t.c}44` }}>
              <span style={{ width: 20, height: 20, borderRadius: "50%", background: t.c, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700, flexShrink: 0 }}>{t.ic}</span>
              <div><div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 2 }}>{it.title}</div>
                <div style={{ fontSize: 12.5, color: "var(--ink2,#c7cfda)", lineHeight: 1.45 }}>{it.detail}</div></div>
            </div>
          );
        })}
      </div>
      {data.suggestions?.length ? (
        <div style={{ marginTop: 13 }}>
          <div className="mono" style={{ fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase", color: "var(--muted,#8a94a6)", marginBottom: 8 }}>Suggestions</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {data.suggestions.map((s, i) => (
              <div key={i} style={{ display: "flex", gap: 9, fontSize: 13, alignItems: "baseline" }}>
                <span style={{ color: "#E6A93C" }}>→</span>
                <span><b>{s.title}.</b> <span style={{ color: "var(--ink2,#c7cfda)" }}>{s.detail}</span></span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Bars({ section, ccy }) {
  const total = section.items.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
  const max = Math.max(1, ...section.items.map((i) => Math.abs(i.value)));
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px" }}>
      <div className="serif" style={{ fontSize: 16, marginBottom: 14 }}>{section.title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {section.items.map((it, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "minmax(70px,30%) 1fr auto auto", gap: 10, alignItems: "center", fontSize: 13.5 }}>
            <span style={{ color: "var(--ink2,#c7cfda)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={it.label}>{it.label}</span>
            <span style={{ height: 14, background: "rgba(120,130,150,0.14)", borderRadius: 3, overflow: "hidden" }}>
              <span style={{ display: "block", height: "100%", width: `${Math.max(2, (Math.abs(it.value) / max) * 100)}%`, background: it.color || ACCENT, borderRadius: 3 }} />
            </span>
            <span className="mono" style={{ fontVariantNumeric: "tabular-nums", fontSize: 13, whiteSpace: "nowrap" }}>{fmtMoney(it.value, section.unit || ccy)}</span>
            <span className="mono" style={{ fontSize: 11, color: "var(--muted,#8a94a6)", minWidth: 34, textAlign: "right" }}>{Math.round((Math.abs(it.value) / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Donut({ section, ccy }) {
  const items = (section.items || []).filter((i) => i.value > 0);
  const total = items.reduce((s, i) => s + i.value, 0) || 1;
  const R = 58, SW = 22, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = items.map((it, i) => {
    const len = (it.value / total) * C;
    const a = { ...it, color: it.color || PALETTE[i % PALETTE.length], dash: len, offset: off, pct: (it.value / total) * 100 };
    off += len; return a;
  });
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px" }}>
      <div className="serif" style={{ fontSize: 16, marginBottom: 14 }}>{section.title}</div>
      <div style={{ display: "flex", gap: 22, alignItems: "center", flexWrap: "wrap" }}>
        <svg viewBox="0 0 160 160" width="148" height="148" style={{ flexShrink: 0, color: "inherit" }} role="img" aria-label={section.title}>
          <g transform="rotate(-90 80 80)">
            <circle cx="80" cy="80" r={R} fill="none" stroke="rgba(120,130,150,0.12)" strokeWidth={SW} />
            {arcs.map((a, i) => (
              <circle key={i} cx="80" cy="80" r={R} fill="none" stroke={a.color} strokeWidth={SW}
                strokeDasharray={`${a.dash} ${C - a.dash}`} strokeDashoffset={-a.offset} strokeLinecap="butt" />
            ))}
          </g>
          <text x="80" y="75" textAnchor="middle" fontSize="10" fontFamily="monospace" fill="var(--muted,#8a94a6)">TOTAL</text>
          <text x="80" y="93" textAnchor="middle" fontSize="15" fontFamily="serif" fill="currentColor">{fmtMoney(Math.round(total), ccy)}</text>
        </svg>
        <div style={{ flex: 1, minWidth: 180, display: "flex", flexDirection: "column", gap: 7 }}>
          {arcs.map((a, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "12px 1fr auto auto", gap: 9, alignItems: "center", fontSize: 13 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: a.color, flexShrink: 0 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.label}>{a.label}</span>
              <span className="mono" style={{ fontVariantNumeric: "tabular-nums", fontSize: 12, whiteSpace: "nowrap" }}>{fmtMoney(a.value, ccy)}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--muted,#8a94a6)", minWidth: 36, textAlign: "right" }}>{a.pct.toFixed(0)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Trend({ section, ccy }) {
  const pts = section.points || [];
  const W = 640, H = 180, PADX = 46, PADT = 16, PADB = 34;
  const vals = pts.map((p) => p.value);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const x = (i) => PADX + (pts.length <= 1 ? 0 : (i * (W - PADX - 12)) / (pts.length - 1));
  const y = (v) => PADT + (H - PADT - PADB) * (1 - (v - lo) / span);
  const zeroY = y(0);
  const line = pts.map((p, i) => `${x(i)},${y(p.value)}`).join(" ");
  const area = `${x(0)},${zeroY} ${line} ${x(pts.length - 1)},${zeroY} Z`;
  const last = pts[pts.length - 1];
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px" }}>
      <div className="serif" style={{ fontSize: 16, marginBottom: 6 }}>{section.title}</div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ display: "block", width: "100%", height: "auto" }} role="img" aria-label={section.title}>
        {[0, 0.5, 1].map((f, i) => {
          const yy = PADT + (H - PADT - PADB) * f;
          return <line key={i} x1={PADX} y1={yy} x2={W - 12} y2={yy} stroke="rgba(255,255,255,0.07)" strokeWidth="1" />;
        })}
        <line x1={PADX} y1={zeroY} x2={W - 12} y2={zeroY} stroke="rgba(255,255,255,0.16)" strokeWidth="1" />
        <text x={PADX - 6} y={y(hi) + 3} textAnchor="end" fontSize="10" fill="var(--muted,#8a94a6)" fontFamily="monospace">{fmtMoney(hi, "")}</text>
        <polygon points={area} fill={ACCENT} opacity="0.12" />
        <polyline points={line} fill="none" stroke={ACCENT} strokeWidth="2.5" strokeLinejoin="round" />
        {last ? <circle cx={x(pts.length - 1)} cy={y(last.value)} r="4" fill={ACCENT} /> : null}
        {pts.map((p, i) => (
          <text key={i} x={x(i)} y={H - 12} textAnchor="middle" fontSize="10" fill="var(--muted,#8a94a6)" fontFamily="monospace">{p.label}</text>
        ))}
      </svg>
    </div>
  );
}

function MultiTrend({ section, ccy }) {
  const series = section.series || [];
  const W = 680, H = 200, PADX = 52, PADT = 16, PADB = 32;
  const allV = series.flatMap((s) => s.points.map((p) => p.value));
  const lo = Math.min(0, ...allV), hi = Math.max(0, ...allV), span = (hi - lo) || 1;
  const n = Math.max(1, ...series.map((s) => s.points.length));
  const x = (i) => PADX + (n <= 1 ? 0 : (i * (W - PADX - 12)) / (n - 1));
  const y = (v) => PADT + (H - PADT - PADB) * (1 - (v - lo) / span);
  const zero = y(0);
  const labels = (series.reduce((a, s) => s.points.length > a.length ? s.points : a, [])) || [];
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px" }}>
      <div className="ct" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
        <span className="serif" style={{ fontSize: 16 }}>{section.title}</span>
        <span style={{ display: "flex", gap: 16 }}>
          {series.map((s, si) => (
            <span key={si} className="mono" style={{ fontSize: 11, color: "var(--ink2,#c7cfda)", display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: s.color || PALETTE[si % PALETTE.length] }} />{s.label}
            </span>
          ))}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ display: "block", width: "100%", height: "auto" }} role="img" aria-label={section.title}>
        {[0, 0.5, 1].map((f, i) => { const yy = PADT + (H - PADT - PADB) * f; return <line key={i} x1={PADX} y1={yy} x2={W - 12} y2={yy} stroke="rgba(255,255,255,0.07)" strokeWidth="1" />; })}
        <line x1={PADX} y1={zero} x2={W - 12} y2={zero} stroke="rgba(255,255,255,0.16)" strokeWidth="1" />
        <text x={PADX - 6} y={y(hi) + 3} textAnchor="end" fontSize="10" fill="var(--muted,#8a94a6)" fontFamily="monospace">{fmtMoney(hi, "")}</text>
        {series.map((s, si) => {
          const c = s.color || PALETTE[si % PALETTE.length];
          const pts = s.points.map((p, i) => `${x(i)},${y(p.value)}`).join(" ");
          const last = s.points[s.points.length - 1];
          return <g key={si}><polyline points={pts} fill="none" stroke={c} strokeWidth="2.5" strokeLinejoin="round" />
            {last ? <circle cx={x(s.points.length - 1)} cy={y(last.value)} r="4" fill={c} /> : null}</g>;
        })}
        {labels.map((p, i) => <text key={i} x={x(i)} y={H - 12} textAnchor="middle" fontSize="10" fill="var(--muted,#8a94a6)" fontFamily="monospace">{p.label}</text>)}
      </svg>
    </div>
  );
}

function Table({ section }) {
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px", overflowX: "auto" }}>
      <div className="serif" style={{ fontSize: 16, marginBottom: 12 }}>{section.title}</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
        <thead>
          <tr>{section.columns.map((c, i) => (
            <th key={i} className="mono" style={{ textAlign: i === 0 ? "left" : (i === section.columns.length - 1 ? "right" : "left"), fontSize: 10, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--muted,#8a94a6)", padding: "6px 8px", borderBottom: "1px solid rgba(255,255,255,0.1)" }}>{c}</th>
          ))}</tr>
        </thead>
        <tbody>
          {section.rows.map((r, ri) => (
            <tr key={ri}>{r.map((cell, ci) => (
              <td key={ci} className={ci === r.length - 1 ? "mono" : ""} style={{ padding: "8px 8px", borderBottom: "1px solid rgba(255,255,255,0.06)", textAlign: ci === r.length - 1 ? "right" : "left", fontVariantNumeric: ci === r.length - 1 ? "tabular-nums" : "normal", whiteSpace: ci === 0 ? "nowrap" : "normal" }}>{cell}</td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATUS_MARK = {
  Abnormal: { c: "#E06C5E", m: "▲" }, Borderline: { c: "#E6A93C", m: "◑" },
  High: { c: "#E06C5E", m: "▲" }, Low: { c: "#E6A93C", m: "▼" },
  Normal: { c: "#3FB27F", m: "●" }, "No range": { c: "#9AA0A6", m: "·" },
};
const STATUS_CELL = {
  Abnormal: { bg: "rgba(224,108,94,0.20)", c: "#E06C5E" },
  Borderline: { bg: "rgba(230,169,60,0.20)", c: "#E6A93C" },
  Normal: { bg: "rgba(63,178,127,0.16)", c: "#3FB27F" },
};
const PANEL_TONE = { good: "#3FB27F", watch: "#E6A93C", info: "#4C8DD8" };

function Matrix({ section }) {
  return (
    <div className="bg1 border rounded-lg" style={{ padding: "16px 18px", overflowX: "auto" }}>
      <div className="row between" style={{ alignItems: "baseline", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div className="serif" style={{ fontSize: 16 }}>{section.title}</div>
        <div className="row" style={{ gap: 14 }}>
          {[["Normal", "#3FB27F"], ["Borderline", "#E6A93C"], ["Abnormal", "#E06C5E"]].map(([l, c]) => (
            <span key={l} className="mono" style={{ fontSize: 10.5, color: "var(--muted,#8a94a6)", display: "inline-flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: c }} />{l}
            </span>
          ))}
        </div>
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left", padding: "6px 10px", fontFamily: "var(--font-mono,monospace)", fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted,#8a94a6)", borderBottom: "1px solid rgba(120,130,150,.18)" }}>Parameter</th>
            <th style={{ textAlign: "left", padding: "6px 10px", fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted,#8a94a6)", borderBottom: "1px solid rgba(120,130,150,.18)" }} className="mono">Reference</th>
            {section.dates.map((d, i) => (
              <th key={i} className="mono" style={{ textAlign: "right", padding: "6px 10px", fontSize: 10.5, color: "var(--muted,#8a94a6)", borderBottom: "1px solid rgba(120,130,150,.18)", whiteSpace: "nowrap" }}>{d}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {section.rows.map((r, ri) => (
            <tr key={ri}>
              <td style={{ padding: "8px 10px", borderBottom: "1px solid rgba(120,130,150,.07)", whiteSpace: "nowrap" }}>{r.param}</td>
              <td className="mono" style={{ padding: "8px 10px", borderBottom: "1px solid rgba(120,130,150,.07)", color: "var(--muted,#8a94a6)", fontSize: 12, whiteSpace: "nowrap" }}>{r.ref}</td>
              {r.cells.map((c, ci) => {
                const s = STATUS_CELL[c.status];
                return (
                  <td key={ci} className="mono" style={{ padding: "7px 10px", textAlign: "right", borderBottom: "1px solid rgba(120,130,150,.07)", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", background: s?.bg || "transparent", color: s?.c || (c.value ? "inherit" : "var(--muted,#8a94a6)"), fontWeight: c.status === "Abnormal" ? 700 : 400 }}>
                    {c.value || "·"}
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

function MiniMultiTrend({ series }) {
  const W = 300, H = 76, PADX = 8, PADY = 10;
  const allV = series.flatMap((s) => s.points.map((p) => p.value));
  const lo = Math.min(...allV), hi = Math.max(...allV), span = (hi - lo) || 1;
  const n = Math.max(...series.map((s) => s.points.length));
  const x = (i) => PADX + (n <= 1 ? 0 : (i * (W - PADX * 2)) / (n - 1));
  const y = (v) => PADY + (H - PADY * 2) * (1 - (v - lo) / span);
  return (
    <div style={{ marginBottom: 10 }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
        {series.map((s, si) => {
          const c = PALETTE[si % PALETTE.length];
          const pts = s.points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
          return <g key={si}><polyline points={pts} fill="none" stroke={c} strokeWidth="2" strokeLinejoin="round" />
            {s.points.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.value)} r="2.5" fill={c} />)}</g>;
        })}
      </svg>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 12px", marginTop: 2 }}>
        {series.map((s, si) => (
          <span key={si} className="mono" style={{ fontSize: 10, color: "var(--muted,#8a94a6)", display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: PALETTE[si % PALETTE.length] }} />{s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function HealthPanels({ section }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(290px,1fr))", gap: 14 }}>
      {section.panels.map((p, i) => {
        const c = PANEL_TONE[p.tone] || PANEL_TONE.info;
        return (
          <div key={i} className="bg1 border rounded-lg" style={{ padding: "14px 16px" }}>
            <div className="row between" style={{ alignItems: "center", marginBottom: 11 }}>
              <span className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
                <span style={{ width: 26, height: 26, borderRadius: "50%", background: `${c}1e`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, flexShrink: 0 }}>{p.icon}</span>
                <span className="serif" style={{ fontSize: 15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
              </span>
              <span style={{ fontSize: 10.5, fontWeight: 600, color: c, background: `${c}1e`, padding: "3px 9px", borderRadius: 20, whiteSpace: "nowrap" }}>{p.status}</span>
            </div>
            {p.series?.length ? <MiniMultiTrend series={p.series} /> : null}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {p.tests.map((t, j) => {
                const s = STATUS_MARK[t.status] || STATUS_MARK["No range"];
                return (
                  <div key={j} className="row between" style={{ alignItems: "baseline", fontSize: 13, gap: 8 }}>
                    <span style={{ color: "var(--ink2,#c7cfda)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.name}>{t.name}</span>
                    <span className="row gap-2" style={{ alignItems: "baseline", flexShrink: 0 }}>
                      <span className="mono" style={{ fontSize: 12.5 }}>{t.value}</span>
                      <span style={{ color: s.c, fontSize: 10, width: 12, textAlign: "center" }} title={`${t.status}${t.ref !== "—" ? " · ref " + t.ref : ""}`}>{s.m}</span>
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--muted,#8a94a6)", marginTop: 11, paddingTop: 9, borderTop: "1px solid rgba(120,130,150,.1)" }}>{p.note}</div>
          </div>
        );
      })}
    </div>
  );
}

function Section({ section, ccy }) {
  if (section.kind === "bars") return <Bars section={section} ccy={ccy} />;
  if (section.kind === "donut") return <Donut section={section} ccy={ccy} />;
  if (section.kind === "trend") return <Trend section={section} ccy={ccy} />;
  if (section.kind === "multitrend") return <MultiTrend section={section} ccy={ccy} />;
  if (section.kind === "table") return <Table section={section} />;
  if (section.kind === "panels") return <HealthPanels section={section} />;
  if (section.kind === "matrix") return <Matrix section={section} />;
  return null;
}

const TIMEFRAMES = [
  { m: 0, label: "All" }, { m: 12, label: "12m" }, { m: 6, label: "6m" },
  { m: 3, label: "3m" }, { m: 1, label: "30d" },
];

const ctlBtn = {
  font: "inherit", fontSize: 13, padding: "7px 13px", borderRadius: 8,
  border: "1px solid var(--line,#232a33)", background: "transparent",
  color: "var(--ink2,#c2cad6)", cursor: "pointer", whiteSpace: "nowrap",
};


function Capsule({ active, onClick, children, disabled }) {
  return (
    <button onClick={onClick} disabled={disabled} className="no-print" style={{
      font: "inherit", fontSize: 13, padding: "6px 14px", borderRadius: 999,
      border: `1px solid ${active ? ACCENT : "var(--line,#232a33)"}`,
      background: active ? "rgba(63,164,122,0.15)" : "transparent",
      color: active ? ACCENT : "var(--ink2,#c2cad6)",
      cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.4 : 1, whiteSpace: "nowrap",
    }}>{children}</button>
  );
}

function DashboardBody({ dash, busy, insights, insLoading }) {
  const empty = dash.empty || (!dash.metrics?.length && !dash.sections?.length);
  if (empty) return (
    <div className="bg1 border rounded-lg" style={{ padding: 28, textAlign: "center", color: "var(--muted,#8a94a6)", marginTop: 14 }}>
      {dash.empty || "No data in this timeframe — try a wider window or include more documents."}
    </div>
  );
  const comp = (dash.sections || []).find((s) => (s.kind === "donut" || s.kind === "bars") && s.items?.length);
  return (
    <div style={{ opacity: busy ? 0.55 : 1, transition: "opacity .15s" }}>
      <div style={{ marginTop: 14 }}>
        {comp ? <Ribbon items={comp.items} ccy={dash.currency} /> : null}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 14, marginBottom: 16 }}>
        {dash.metrics.map((m, i) => <MetricCard key={i} m={m} ccy={dash.currency} accent={PALETTE[i % PALETTE.length]} />)}
      </div>
      {insLoading ? (
        <div className="bg1 border rounded-lg" style={{ padding: "14px 18px", marginBottom: 16, fontSize: 13, color: "var(--muted,#8a94a6)" }}>✨ Analyzing this view…</div>
      ) : insights ? <InsightsPanel data={insights} /> : null}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 16, alignItems: "start" }}>
        {dash.sections.map((s, i) => (
          <div key={i} style={{ gridColumn: (s.kind === "table" || s.kind === "trend" || s.kind === "multitrend" || s.kind === "panels" || s.kind === "matrix") ? "1 / -1" : "auto" }}>
            <Section section={s} ccy={dash.currency} />
          </div>
        ))}
      </div>
    </div>
  );
}

function DocPicker({ docs, checked, onClose, onApply }) {
  const [local, setLocal] = useState(() => {
    const o = {}; docs.forEach((d) => { o[d.id] = checked ? !!checked[d.id] : true; }); return o;
  });
  const n = docs.filter((d) => local[d.id]).length;
  const allOn = n === docs.length;
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div onClick={(e) => e.stopPropagation()} className="bg1 border rounded-lg" style={{ width: "100%", maxWidth: 460, maxHeight: "80vh", display: "flex", flexDirection: "column", padding: 18 }}>
        <div className="row between" style={{ alignItems: "center", marginBottom: 10 }}>
          <h2 className="serif" style={{ fontSize: 17, margin: 0 }}>Include documents</h2>
          <button onClick={() => { const v = !allOn; const o = {}; docs.forEach((d) => { o[d.id] = v; }); setLocal(o); }}
            className="mono" style={{ background: "transparent", border: "none", color: ACCENT, cursor: "pointer", fontSize: 12 }}>{allOn ? "Clear all" : "Select all"}</button>
        </div>
        <div style={{ overflowY: "auto", flex: 1, marginBottom: 12 }}>
          {docs.map((d) => (
            <label key={d.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "9px 4px", borderBottom: "1px solid rgba(120,130,150,.08)", cursor: "pointer" }}>
              <input type="checkbox" checked={!!local[d.id]} onChange={(e) => setLocal({ ...local, [d.id]: e.target.checked })} />
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13.5 }}>{d.name}</span>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--muted,#8a94a6)" }}>{(d.docType || "").replace(/_/g, " ")}</span>
            </label>
          ))}
        </div>
        <div className="row between" style={{ alignItems: "center" }}>
          <span className="mono" style={{ fontSize: 12, color: "var(--muted,#8a94a6)" }}>{n}/{docs.length} selected</span>
          <button onClick={() => onApply(allOn ? null : local)} disabled={!n} style={{ ...ctlBtn, background: ACCENT, color: "#06130d", borderColor: ACCENT, opacity: n ? 1 : 0.5 }}>Apply</button>
        </div>
      </div>
    </div>
  );
}

export default function AnalyticsView() {
  const { data, loading, error } = useApiResource(fetchAnalyticsDashboards, []);
  const [active, setActive] = useState(null);
  const [months, setMonths] = useState(0);
  const [checked, setChecked] = useState({});      // {docId:bool}
  const [phase, setPhase] = useState("select");    // "select" | "dashboard"
  const [dash, setDash] = useState(null);
  const [building, setBuilding] = useState(false);
  const [berr, setBerr] = useState(null);
  const [insights, setInsights] = useState(null);
  const [insLoading, setInsLoading] = useState(false);

  const themes = data?.themes || [];
  const available = useMemo(() => themes.filter((t) => t.available), [themes]);
  const activeTheme = themes.find((t) => t.key === active) || null;

  useEffect(() => { if (!active && available.length) setActive(available[0].key); }, [available, active]);
  // selecting a theme → selection screen with all its docs pre-ticked
  useEffect(() => {
    if (!activeTheme) return;
    const all = {}; activeTheme.documents.forEach((d) => { all[d.id] = true; });
    setChecked(all); setPhase("select"); setDash(null); setInsights(null); setBerr(null); setMonths(0);
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  const chosenIds = () => activeTheme ? activeTheme.documents.map((d) => d.id).filter((id) => checked[id]) : [];
  const genInsights = (ids, mo) => {
    setInsLoading(true); setInsights(null);
    fetchAnalyticsInsights(active, ids, mo)
      .then((r) => setInsights(r))
      .catch(() => setInsights({ insights: [{ tone: "info", title: "AI analysis unavailable", detail: "Couldn't generate insights right now — try again shortly." }], suggestions: [] }))
      .finally(() => setInsLoading(false));
  };
  const generate = (mo = months, withAI = true) => {
    const ids = chosenIds();
    if (!ids.length) return;
    setBuilding(true); setBerr(null); setPhase("dashboard");
    if (withAI) setInsights(null);
    buildAnalyticsDashboard(active, ids, mo)
      .then((r) => { setDash(r); if (withAI) genInsights(ids, mo); })
      .catch((e) => setBerr(e?.detail || e?.message || "Couldn't build the dashboard."))
      .finally(() => setBuilding(false));
  };
  const changeTf = (mo) => { setMonths(mo); setInsights(null); if (phase === "dashboard") generate(mo, false); };
  const exportPdf = () => openPrintReport(activeTheme, dash, TIMEFRAMES.find((t) => t.m === months)?.label || "All", insights);

  if (loading) return <LoadingState label="Loading analytics…" />;
  if (error) return <ErrorState error={error} />;
  if (!available.length) {
    return (
      <div>
        <h1 className="serif" style={{ fontSize: 24, margin: "0 0 6px" }}>Analytics</h1>
        <p style={{ color: "var(--muted,#8a94a6)", fontSize: 14.5, maxWidth: "60ch" }}>
          No dashboards yet — upload documents (bank statements, invoices, receipts, ID cards, lab reports)
          and they'll appear here as themes you can chart.
        </p>
      </div>
    );
  }

  // `active` is set by an effect after first paint — until then (or if the key
  // doesn't resolve) activeTheme is null; guard so the screens below can use it.
  if (!activeTheme) return <LoadingState label="Loading analytics…" />;

  // Alphabetical by name so the include-checklist reads predictably (feedback pk 54).
  const docs = [...(activeTheme.documents || [])].sort((a, b) =>
    (a.name || "").localeCompare(b.name || "", undefined, { numeric: true, sensitivity: "base" }));
  const nSel = docs.filter((d) => checked[d.id]).length;
  const allOn = nSel === docs.length && docs.length > 0;
  const tfLabel = TIMEFRAMES.find((t) => t.m === months)?.label || "All";

  const Capsules = (
    <div className="no-print" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
      {themes.map((t) => (
        <Capsule key={t.key} active={t.key === active} disabled={!t.available} onClick={() => t.available && setActive(t.key)}>
          {t.icon} {t.label.replace(/ overview/i, "")}{t.available ? ` · ${t.docCount}` : ""}
        </Capsule>
      ))}
    </div>
  );
  const Timeframe = (onPick) => (
    <div className="no-print" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <span className="mono" style={{ fontSize: 10.5, color: "var(--muted,#8a94a6)", letterSpacing: ".1em", textTransform: "uppercase", marginRight: 2 }}>Timeframe</span>
      {TIMEFRAMES.map((t) => <Capsule key={t.m} active={t.m === months} onClick={() => onPick(t.m)}>{t.label}</Capsule>)}
    </div>
  );

  // ── SELECTION SCREEN ────────────────────────────────────────────────────
  if (phase === "select") {
    return (
      <div>
        {Capsules}
        <div className="row between" style={{ alignItems: "baseline", flexWrap: "wrap", gap: 8, marginBottom: 4 }}>
          <h1 className="serif" style={{ fontSize: 24, margin: 0 }}>{activeTheme.icon} {activeTheme.label}</h1>
        </div>
        <p style={{ color: "var(--muted,#8a94a6)", fontSize: 14, margin: "0 0 16px" }}>
          Choose the documents to include, pick a timeframe, then generate the dashboard.
        </p>
        <div style={{ marginBottom: 14 }}>{Timeframe(setMonths)}</div>
        <div className="row between" style={{ alignItems: "center", marginBottom: 9 }}>
          <button onClick={() => { const v = !allOn; const o = {}; docs.forEach((d) => { o[d.id] = v; }); setChecked(o); }}
            className="mono" style={{ background: "transparent", border: "none", color: ACCENT, cursor: "pointer", fontSize: 12, padding: 0 }}>{allOn ? "Clear all" : "Select all"}</button>
          <span className="mono" style={{ fontSize: 12, color: "var(--muted,#8a94a6)" }}>{nSel} of {docs.length} selected</span>
        </div>
        <div className="bg1 border rounded-lg" style={{ overflow: "hidden", marginBottom: 18, maxHeight: "48vh", overflowY: "auto" }}>
          {docs.map((d, i) => (
            <label key={d.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 15px", borderTop: i ? "1px solid rgba(120,130,150,.08)" : "none", cursor: "pointer" }}>
              <input type="checkbox" checked={!!checked[d.id]} onChange={(e) => setChecked({ ...checked, [d.id]: e.target.checked })} />
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13.5 }}>{d.name}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--muted,#8a94a6)", background: "rgba(120,130,150,.08)", padding: "2px 8px", borderRadius: 20, whiteSpace: "nowrap" }}>{prettyType(d.docType)}</span>
            </label>
          ))}
        </div>
        <button onClick={() => generate()} disabled={!nSel}
          className="rounded-lg" style={{ padding: "11px 22px", background: ACCENT, color: "#06130d", border: "none", fontWeight: 600, cursor: nSel ? "pointer" : "default", opacity: nSel ? 1 : 0.5, fontSize: 14 }}>
          Generate {activeTheme.label.toLowerCase()} →
        </button>
      </div>
    );
  }

  // ── DASHBOARD ───────────────────────────────────────────────────────────
  return (
    <div>
      {Capsules}
      <div className="no-print" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 20, paddingBottom: 14, borderBottom: "1px solid var(--line,#232a33)" }}>
        {Timeframe(changeTf)}
        <span style={{ flex: 1 }} />
        <button onClick={() => setPhase("select")} style={ctlBtn}>🗂 Documents · {nSel}/{docs.length}</button>
        <button onClick={() => genInsights(chosenIds(), months)} disabled={!dash || building || insLoading} style={{ ...ctlBtn, borderColor: "#8B7FD6", color: "#a99cf0", opacity: (!dash || building || insLoading) ? 0.5 : 1 }}>{insLoading ? "✨ Analyzing…" : "✨ AI insights"}</button>
        <button onClick={exportPdf} disabled={!dash || building} style={{ ...ctlBtn, borderColor: ACCENT, color: ACCENT, opacity: (!dash || building) ? 0.5 : 1 }}>⬇ Export PDF</button>
      </div>
      <div id="analytics-print-root">
        <div className="row between" style={{ alignItems: "baseline", flexWrap: "wrap", gap: 8, marginBottom: 2 }}>
          <h1 className="serif" style={{ fontSize: 24, margin: 0 }}>{activeTheme.icon} {activeTheme.label}</h1>
          <span className="mono" style={{ fontSize: 11.5, color: "var(--muted,#8a94a6)" }}>
            {tfLabel === "All" ? "All time" : `Last ${tfLabel}`}{dash?.docCount != null ? ` · ${dash.docCount} docs` : ""}{dash?.currency ? ` · ${dash.currency}` : ""}
          </span>
        </div>
        {building && !dash ? <LoadingState label="Generating…" /> :
          berr ? <div style={{ color: NEG, fontSize: 13, marginTop: 16 }}>{berr}</div> :
          dash ? <DashboardBody dash={dash} busy={building} insights={insights} insLoading={insLoading} /> : null}
      </div>
    </div>
  );
}
