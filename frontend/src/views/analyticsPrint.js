// Purpose-built PDF/print report for an Analytics dashboard.
//
// We deliberately do NOT print the dark SPA (that produced black voids, empty
// columns and cards split across pages). Instead we render the dashboard PAYLOAD
// into a clean, light, tightly-packed A4 document — KPIs, an auto-generated
// summary, charts (donut/bars/trend as inline SVG) and tables — then open it in a
// new window and call print(). The browser's "Save as PDF" yields a real report.

const PALETTE = ["#2E7D57", "#B8860B", "#3B6FB0", "#9B59B6", "#3FA48A", "#C0562A", "#6C5CB8", "#7A828E"];
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function fmt(v, ccy) {
  if (typeof v !== "number") return v == null ? "—" : esc(v);
  const s = Math.abs(v) >= 1000 || Number.isInteger(v)
    ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return ccy ? `${ccy} ${s}` : s;
}

function donutSvg(section, ccy) {
  const items = (section.items || []).filter((i) => i.value > 0);
  const total = items.reduce((s, i) => s + i.value, 0) || 1;
  const R = 50, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = items.map((it, i) => {
    const len = (it.value / total) * C;
    const a = { ...it, color: it.color || PALETTE[i % PALETTE.length], dash: len, off, pct: (it.value / total) * 100 };
    off += len; return a;
  });
  const circles = arcs.map((a) =>
    `<circle cx="66" cy="66" r="${R}" fill="none" stroke="${a.color}" stroke-width="19" stroke-dasharray="${a.dash.toFixed(2)} ${(C - a.dash).toFixed(2)}" stroke-dashoffset="${(-a.off).toFixed(2)}"/>`).join("");
  const legend = arcs.map((a) =>
    `<div class="lr"><span class="sw" style="background:${a.color}"></span><span class="ll">${esc(a.label)}</span><span class="lv">${fmt(a.value, ccy)}</span><span class="lp">${a.pct.toFixed(0)}%</span></div>`).join("");
  return `<div class="dn"><svg viewBox="0 0 132 132" width="120" height="120"><g transform="rotate(-90 66 66)"><circle cx="66" cy="66" r="${R}" fill="none" stroke="#eceef1" stroke-width="19"/>${circles}</g>
    <text x="66" y="62" text-anchor="middle" font-size="8.5" fill="#8a8f98" font-family="monospace">TOTAL</text>
    <text x="66" y="78" text-anchor="middle" font-size="13" fill="#141821" font-family="Georgia,serif">${fmt(Math.round(total), ccy)}</text></svg>
    <div class="lg">${legend}</div></div>`;
}

function barsHtml(section, ccy) {
  const items = section.items || [];
  const total = items.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
  const max = Math.max(1, ...items.map((i) => Math.abs(i.value)));
  return `<div class="bars">${items.map((it) => {
    const w = Math.max(2, (Math.abs(it.value) / max) * 100);
    return `<div class="br"><span class="bn">${esc(it.label)}</span><span class="bt"><span class="bf" style="width:${w}%;background:${it.color || "#2E7D57"}"></span></span><span class="bv">${fmt(it.value, section.unit || ccy)}</span><span class="bp">${Math.round((Math.abs(it.value) / total) * 100)}%</span></div>`;
  }).join("")}</div>`;
}

function trendSvg(section, ccy) {
  const pts = section.points || [];
  if (!pts.length) return "";
  const W = 700, H = 190, PADX = 56, PADT = 14, PADB = 30;
  const vals = pts.map((p) => p.value);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = (hi - lo) || 1;
  const x = (i) => PADX + (pts.length <= 1 ? 0 : (i * (W - PADX - 14)) / (pts.length - 1));
  const y = (v) => PADT + (H - PADT - PADB) * (1 - (v - lo) / span);
  const zero = y(0);
  const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const grid = [0, 0.5, 1].map((f) => { const yy = PADT + (H - PADT - PADB) * f; return `<line x1="${PADX}" y1="${yy}" x2="${W - 14}" y2="${yy}" stroke="#eef0f2"/>`; }).join("");
  const labels = pts.map((p, i) => `<text x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="middle" font-size="10" fill="#8a8f98" font-family="monospace">${esc(p.label)}</text>`).join("");
  const last = pts[pts.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet">${grid}
    <line x1="${PADX}" y1="${zero.toFixed(1)}" x2="${W - 14}" y2="${zero.toFixed(1)}" stroke="#c8ccd2"/>
    <text x="${PADX - 8}" y="${(y(hi) + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#8a8f98" font-family="monospace">${fmt(hi, "")}</text>
    <polygon points="${x(0).toFixed(1)},${zero.toFixed(1)} ${line} ${x(pts.length - 1).toFixed(1)},${zero.toFixed(1)}" fill="#2E7D57" opacity="0.10"/>
    <polyline points="${line}" fill="none" stroke="#2E7D57" stroke-width="2.5" stroke-linejoin="round"/>
    <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last.value).toFixed(1)}" r="4" fill="#2E7D57"/>${labels}</svg>`;
}

function tableHtml(section) {
  const head = section.columns.map((c, i) => `<th class="${i === section.columns.length - 1 ? "r" : ""}">${esc(c)}</th>`).join("");
  const body = section.rows.map((r) => `<tr>${r.map((cell, ci) => `<td class="${ci === r.length - 1 ? "r" : ""}">${esc(cell)}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function summaryBlock(theme, dash, tfLabel) {
  const ccy = dash.currency;
  const kv = (dash.metrics || []).map((m) =>
    `<span class="kf"><span class="kfl">${esc(m.label)}</span><span class="kfv">${fmt(m.value, m.unit || ccy)}</span>${m.sub ? `<span class="kfs">${esc(m.sub)}</span>` : ""}</span>`).join("");
  let narr = `This ${esc((theme.label || "").toLowerCase())} draws on <b>${dash.docCount}</b> document${dash.docCount === 1 ? "" : "s"}${tfLabel && tfLabel !== "All" ? ` over the last ${esc(tfLabel)}` : ""}.`;
  const comp = (dash.sections || []).find((s) => (s.kind === "donut" || s.kind === "bars") && (s.items || []).length);
  if (comp) {
    const tot = comp.items.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
    const t = comp.items[0];
    narr += ` The largest ${esc(comp.title.toLowerCase())} entry is <b>${esc(t.label)}</b> at ${Math.round((Math.abs(t.value) / tot) * 100)}% (${fmt(t.value, comp.unit || ccy)}).`;
  }
  const tbl = (dash.sections || []).find((s) => s.kind === "table");
  if (tbl) narr += ` ${tbl.rows.length} row${tbl.rows.length === 1 ? "" : "s"} of detail are listed under “${esc(tbl.title)}”.`;
  return `<div class="summary"><div class="st">At a glance</div><p>${narr}</p><div class="kfs-row">${kv}</div></div>`;
}

function ribbonHtml(items) {
  const list = (items || []).filter((i) => Math.abs(i.value) > 0);
  const total = list.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
  if (!list.length) return "";
  const bar = list.map((it, i) => `<span style="width:${(Math.abs(it.value) / total) * 100}%;background:${it.color || PALETTE[i % PALETTE.length]}"></span>`).join("");
  const leg = list.map((it, i) => `<span class="rl"><span class="sw" style="background:${it.color || PALETTE[i % PALETTE.length]}"></span>${esc(it.label)} <b>${Math.round((Math.abs(it.value) / total) * 100)}%</b></span>`).join("");
  return `<div class="ribbon"><div class="rbar">${bar}</div><div class="rleg">${leg}</div></div>`;
}

const ITONE = { positive: "#2E7D57", watch: "#B8860B", risk: "#C0562A", info: "#3B6FB0" };
const ITONE_IC = { positive: "✓", watch: "!", risk: "▲", info: "i" };

function insightsHtml(insights) {
  if (!insights || (!insights.insights?.length && !insights.suggestions?.length)) return "";
  const cards = (insights.insights || []).map((it) => {
    const c = ITONE[it.tone] || ITONE.info;
    return `<div class="ins" style="background:${c}12;border:1px solid ${c}44"><span class="idot" style="background:${c}">${ITONE_IC[it.tone] || "i"}</span><div><div class="ith">${esc(it.title)}</div><div class="itd">${esc(it.detail)}</div></div></div>`;
  }).join("");
  const sugg = (insights.suggestions || []).length
    ? `<div class="sugg"><div class="st">Suggestions</div>${insights.suggestions.map((s) => `<div class="sr"><span>→</span><span><b>${esc(s.title)}.</b> ${esc(s.detail)}</span></div>`).join("")}</div>` : "";
  return `<div class="aibox"><div class="aih">✨ AI insights</div><div class="insgrid">${cards}</div>${sugg}</div>`;
}

function miniTrendSvg(series) {
  const W = 280, H = 60, PX = 6, PY = 8;
  const allV = series.flatMap((s) => s.points.map((p) => p.value));
  const lo = Math.min(...allV), hi = Math.max(...allV), span = (hi - lo) || 1;
  const n = Math.max(...series.map((s) => s.points.length));
  const x = (i) => PX + (n <= 1 ? 0 : (i * (W - PX * 2)) / (n - 1));
  const y = (v) => PY + (H - PY * 2) * (1 - (v - lo) / span);
  const lines = series.map((s, si) => {
    const c = PALETTE[si % PALETTE.length];
    const pts = s.points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
    return `<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.8"/>` +
      s.points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2" fill="${c}"/>`).join("");
  }).join("");
  const leg = series.map((s, si) => `<span class="pl"><span class="sw" style="background:${PALETTE[si % PALETTE.length]}"></span>${esc(s.label)}</span>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="margin-bottom:4px">${lines}</svg><div class="plg">${leg}</div>`;
}

const PST = { High: { c: "#C0562A", m: "▲" }, Low: { c: "#B8860B", m: "▼" }, Normal: { c: "#2E7D57", m: "●" }, "No range": { c: "#9AA0A6", m: "·" } };
const PTONE = { good: "#2E7D57", watch: "#B8860B", info: "#3B6FB0" };

function panelsHtml(section) {
  const cards = (section.panels || []).map((p) => {
    const c = PTONE[p.tone] || PTONE.info;
    const trend = p.series && p.series.length ? miniTrendSvg(p.series) : "";
    const rows = (p.tests || []).map((t) => {
      const s = PST[t.status] || PST["No range"];
      return `<div class="pr"><span class="pn">${esc(t.name)}</span><span class="pv">${esc(t.value)} <b style="color:${s.c}">${s.m}</b></span></div>`;
    }).join("");
    return `<div class="panel"><div class="ph"><span class="pi" style="background:${c}20">${p.icon}</span><span class="pnm">${esc(p.name)}</span><span class="pst" style="color:${c};background:${c}20">${esc(p.status)}</span></div>${trend}<div class="prs">${rows}</div><div class="pnote">${esc(p.note)}</div></div>`;
  }).join("");
  return `<div class="panels-wrap"><div class="panels">${cards}</div></div>`;
}

function multitrendHtml(section) {
  const series = section.series || [];
  const W = 700, H = 190, PADX = 52, PADT = 14, PADB = 30;
  const allV = series.flatMap((s) => s.points.map((p) => p.value));
  const lo = Math.min(0, ...allV), hi = Math.max(0, ...allV), span = (hi - lo) || 1;
  const n = Math.max(1, ...series.map((s) => s.points.length));
  const x = (i) => PADX + (n <= 1 ? 0 : (i * (W - PADX - 12)) / (n - 1));
  const y = (v) => PADT + (H - PADT - PADB) * (1 - (v - lo) / span);
  const zero = y(0);
  const grid = [0, 0.5, 1].map((f) => { const yy = PADT + (H - PADT - PADB) * f; return `<line x1="${PADX}" y1="${yy}" x2="${W - 12}" y2="${yy}" stroke="#eef0f2"/>`; }).join("");
  const base = series.reduce((a, s) => s.points.length > a.length ? s.points : a, []);
  const labels = base.map((p, i) => `<text x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="middle" font-size="10" fill="#8a8f98" font-family="monospace">${esc(p.label)}</text>`).join("");
  const lines = series.map((s, si) => {
    const c = s.color || PALETTE[si % PALETTE.length];
    const pts = s.points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
    const last = s.points[s.points.length - 1];
    return `<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="2.2"/>` + (last ? `<circle cx="${x(s.points.length - 1).toFixed(1)}" cy="${y(last.value).toFixed(1)}" r="3.5" fill="${c}"/>` : "");
  }).join("");
  const leg = series.map((s, si) => `<span class="pl"><span class="sw" style="background:${s.color || PALETTE[si % PALETTE.length]}"></span>${esc(s.label)}</span>`).join("");
  return `<div class="card full"><div class="ct" style="display:flex;justify-content:space-between;align-items:baseline;gap:12px"><span>${esc(section.title)}</span><span class="plg" style="margin-bottom:0">${leg}</span></div><svg viewBox="0 0 ${W} ${H}" width="100%">${grid}<line x1="${PADX}" y1="${zero.toFixed(1)}" x2="${W - 12}" y2="${zero.toFixed(1)}" stroke="#c8ccd2"/><text x="${PADX - 6}" y="${(y(hi) + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#8a8f98" font-family="monospace">${fmt(hi, "")}</text>${lines}${labels}</svg></div>`;
}

const MCELL = { Normal: { bg: "#e8f5ee", c: "#2E7D57" }, Borderline: { bg: "#fbf3e0", c: "#B8860B" }, Abnormal: { bg: "#f9e5e2", c: "#C0562A" } };

function matrixHtml(section) {
  const head = `<th class="mp">Parameter</th><th class="mp">Reference</th>` + section.dates.map((d) => `<th>${esc(d)}</th>`).join("");
  const rows = section.rows.map((r) => {
    const cells = r.cells.map((c) => { const s = MCELL[c.status]; return `<td${s ? ` style="background:${s.bg};color:${s.c}"` : ""}>${esc(c.value || "·")}</td>`; }).join("");
    return `<tr><td class="mpn">${esc(r.param)}</td><td class="mrn">${esc(r.ref)}</td>${cells}</tr>`;
  }).join("");
  const legend = `<div class="mleg">` + [["Normal", "#2E7D57"], ["Borderline", "#B8860B"], ["Abnormal", "#C0562A"]].map(([l, c]) => `<span><span class="mdot" style="background:${c}"></span>${l}</span>`).join("") + `</div>`;
  return `<div class="card full"><div class="ct">${esc(section.title)}</div>${legend}<div style="overflow-x:auto"><table class="mtx"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

export function buildReportHtml(theme, dash, tfLabel, insights, nowStr) {
  const ccy = dash.currency;
  const now = nowStr || new Date().toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  const kpis = (dash.metrics || []).map((m) =>
    `<div class="kpi"><div class="l">${esc(m.label)}</div><div class="v">${fmt(m.value, m.unit || ccy)}</div>${m.sub ? `<div class="s">${esc(m.sub)}</div>` : ""}</div>`).join("");

  // half-width cards (donut/bars) first so the 2-col grid packs tightly; then full-width (table/trend)
  const cardHtml = (s) => {
    if (s.kind === "panels") return panelsHtml(s);
    if (s.kind === "matrix") return matrixHtml(s);
    if (s.kind === "multitrend") return multitrendHtml(s);
    const inner = s.kind === "donut" ? donutSvg(s, ccy)
      : s.kind === "bars" ? barsHtml(s, ccy)
      : s.kind === "trend" ? trendSvg(s, ccy)
      : s.kind === "table" ? tableHtml(s)
      : "";
    const full = (s.kind === "table" || s.kind === "trend") ? " full" : "";
    return `<div class="card${full}"><div class="ct">${esc(s.title)}</div>${inner}</div>`;
  };
  const sections = (dash.sections || []).slice()
    .sort((a, b) => { const w = (k) => (k === "table" || k === "trend" || k === "multitrend" || k === "panels" || k === "matrix") ? 1 : 0; return w(a.kind) - w(b.kind); });
  const cards = sections.map(cardHtml).join("");

  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(theme.label)} — DocAIQ</title><style>
    @page { size: A4; margin: 13mm; }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #141821; margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .hd { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
    h1 { font-family: Georgia, "Times New Roman", serif; font-size: 22px; margin: 0; }
    .sub { color: #6b7280; font-size: 11.5px; font-family: ui-monospace, Menlo, monospace; }
    .rule { height: 2px; background: #141821; margin: 9px 0 15px; }
    .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 13px; }
    .kpi { border: 1px solid #e3e6ea; border-radius: 8px; padding: 11px 13px; }
    .kpi .l { font-family: ui-monospace, monospace; font-size: 8.5px; letter-spacing: .1em; text-transform: uppercase; color: #8a8f98; margin-bottom: 7px; }
    .kpi .v { font-family: Georgia, serif; font-size: 26px; line-height: 1; }
    .ribbon { margin-bottom: 13px; }
    .rbar { display: flex; height: 16px; border-radius: 6px; overflow: hidden; margin-bottom: 8px; }
    .rbar > span { display: block; }
    .rleg { display: flex; flex-wrap: wrap; gap: 5px 18px; font-size: 11.5px; color: #3a4150; }
    .rl { display: inline-flex; align-items: center; gap: 6px; }
    .rl .sw { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
    .aibox { border: 1px solid #d9c9a4; background: #fcfaf4; border-radius: 8px; padding: 13px 15px; margin-bottom: 13px; break-inside: avoid; }
    .aih { font-family: Georgia, serif; font-size: 14.5px; margin-bottom: 10px; }
    .insgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .ins { display: flex; gap: 9px; padding: 9px 11px; border-radius: 7px; break-inside: avoid; }
    .idot { width: 18px; height: 18px; border-radius: 50%; color: #fff; font-size: 10px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .ith { font-size: 12.5px; font-weight: 700; margin-bottom: 1px; }
    .itd { font-size: 11.5px; color: #3a4150; line-height: 1.42; }
    .sugg { margin-top: 10px; }
    .sugg .st { font-family: ui-monospace, monospace; font-size: 8.5px; letter-spacing: .1em; text-transform: uppercase; color: #8a8f98; margin-bottom: 6px; }
    .sr { display: flex; gap: 8px; font-size: 12px; margin-bottom: 4px; align-items: baseline; }
    .sr > span:first-child { color: #B8860B; }
    .kpi .s { font-family: ui-monospace, monospace; font-size: 10px; color: #8a8f98; margin-top: 6px; }
    .summary { border: 1px solid #e3e6ea; border-radius: 8px; padding: 12px 14px; margin-bottom: 13px; background: #f7f8f9; break-inside: avoid; }
    .summary .st { font-family: ui-monospace, monospace; font-size: 8.5px; letter-spacing: .12em; text-transform: uppercase; color: #2E7D57; margin-bottom: 7px; }
    .summary p { margin: 0 0 9px; font-size: 12.5px; line-height: 1.55; }
    .kfs-row { display: flex; flex-wrap: wrap; gap: 8px 20px; }
    .kf { display: flex; flex-direction: column; }
    .kfl { font-family: ui-monospace, monospace; font-size: 8.5px; letter-spacing: .08em; text-transform: uppercase; color: #8a8f98; }
    .kf .kfv { font-family: Georgia, serif; font-size: 15px; }
    .kf .kfs { font-family: ui-monospace, monospace; font-size: 9.5px; color: #8a8f98; }
    .mtx { width: 100%; border-collapse: collapse; font-size: 11px; }
    .mtx th { font-family: ui-monospace, monospace; font-size: 8.5px; letter-spacing: .05em; text-transform: uppercase; color: #8a8f98; padding: 5px 8px; border-bottom: 1px solid #d7dbe0; text-align: right; white-space: nowrap; }
    .mtx th.mp { text-align: left; }
    .mtx td { padding: 6px 8px; border-bottom: 1px solid #f0f2f4; text-align: right; font-family: ui-monospace, monospace; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .mtx td.mpn { text-align: left; font-family: -apple-system, sans-serif; }
    .mtx td.mrn { text-align: left; color: #8a8f98; }
    .mleg { display: flex; gap: 14px; margin-bottom: 8px; font-family: ui-monospace, monospace; font-size: 9.5px; color: #8a8f98; }
    .mleg span { display: inline-flex; align-items: center; gap: 5px; }
    .mdot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .panels-wrap { grid-column: 1 / -1; }
    .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .panel { border: 1px solid #e3e6ea; border-radius: 8px; padding: 12px 13px; break-inside: avoid; }
    .ph { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
    .pi { width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; }
    .pnm { font-family: Georgia, serif; font-size: 13.5px; flex: 1; }
    .pst { font-size: 9.5px; font-weight: 700; padding: 2px 8px; border-radius: 20px; white-space: nowrap; }
    .pr { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 11.5px; padding: 2px 0; }
    .pn { color: #3a4150; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pv { font-family: ui-monospace, monospace; font-size: 11px; white-space: nowrap; flex-shrink: 0; }
    .pnote { font-family: ui-monospace, monospace; font-size: 9.5px; color: #8a8f98; margin-top: 8px; padding-top: 7px; border-top: 1px solid #f0f2f4; }
    .plg { display: flex; flex-wrap: wrap; gap: 2px 10px; margin-bottom: 4px; }
    .pl { font-family: ui-monospace, monospace; font-size: 9px; color: #8a8f98; display: inline-flex; align-items: center; gap: 4px; }
    .pl .sw { width: 7px; height: 7px; border-radius: 2px; display: inline-block; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; grid-auto-flow: row dense; }
    .card { border: 1px solid #e3e6ea; border-radius: 8px; padding: 12px 14px; break-inside: avoid; page-break-inside: avoid; }
    .card.full { grid-column: 1 / -1; }
    .ct { font-family: Georgia, serif; font-size: 14.5px; margin-bottom: 10px; }
    .dn { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
    .lg { flex: 1; min-width: 150px; display: flex; flex-direction: column; gap: 5px; }
    .lr { display: grid; grid-template-columns: 11px 1fr auto auto; gap: 8px; align-items: center; font-size: 12px; }
    .sw { width: 9px; height: 9px; border-radius: 2px; }
    .ll { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .lv { font-family: ui-monospace, monospace; font-size: 11.5px; }
    .lp { font-family: ui-monospace, monospace; font-size: 10px; color: #8a8f98; min-width: 30px; text-align: right; }
    .bars { display: flex; flex-direction: column; gap: 8px; }
    .br { display: grid; grid-template-columns: minmax(64px, 30%) 1fr auto auto; gap: 9px; align-items: center; font-size: 12.5px; }
    .bn { color: #3a4150; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bt { height: 12px; background: #eef0f2; border-radius: 3px; overflow: hidden; }
    .bf { display: block; height: 100%; border-radius: 3px; }
    .bv { font-family: ui-monospace, monospace; font-size: 12px; }
    .bp { font-family: ui-monospace, monospace; font-size: 10px; color: #8a8f98; min-width: 30px; text-align: right; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th { font-family: ui-monospace, monospace; font-size: 8.5px; letter-spacing: .07em; text-transform: uppercase; color: #8a8f98; text-align: left; padding: 5px 7px; border-bottom: 1px solid #d7dbe0; }
    td { padding: 6px 7px; border-bottom: 1px solid #f0f2f4; }
    td.r, th.r { text-align: right; font-family: ui-monospace, monospace; font-variant-numeric: tabular-nums; }
    tr { break-inside: avoid; }
    footer { margin-top: 15px; border-top: 1px solid #e3e6ea; padding-top: 8px; display: flex; justify-content: space-between; font-family: ui-monospace, monospace; font-size: 10px; color: #8a8f98; }
  </style></head><body>
    <div class="hd"><h1>${esc(theme.icon || "")} ${esc(theme.label)}</h1>
      <span class="sub">${tfLabel && tfLabel !== "All" ? `Last ${esc(tfLabel)}` : "All time"} · ${dash.docCount} docs${ccy ? ` · ${esc(ccy)}` : ""}</span></div>
    <div class="rule"></div>
    ${ribbonHtml(((dash.sections || []).find((s) => (s.kind === "donut" || s.kind === "bars") && (s.items || []).length) || {}).items)}
    <div class="kpis">${kpis}</div>
    ${summaryBlock(theme, dash, tfLabel)}
    ${insightsHtml(insights)}
    <div class="grid">${cards}</div>
    <footer><span>DocAIQ · Analytics</span><span>Generated ${esc(now)}</span></footer>
  </body></html>`;
  return html;
}

export function openPrintReport(theme, dash, tfLabel, insights) {
  if (!dash || (!dash.metrics?.length && !dash.sections?.length)) return;
  const html = buildReportHtml(theme, dash, tfLabel, insights);
  const w = window.open("", "_blank", "width=900,height=1100");
  if (!w) return; // popup blocked
  w.document.open();
  w.document.write(html);
  w.document.close();
  w.focus();
  // give the browser a tick to lay out SVG/fonts before printing
  setTimeout(() => { try { w.print(); } catch (e) { /* user can print manually */ } }, 350);
}
