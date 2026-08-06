// SmartVisuals — enhanced AI chat visualisations. Runs a chain of pattern
// detectors over the AI message text and renders the first match below the
// message bubble. Pure frontend: zero backend changes, zero dependencies.
//
// Detectors (priority order):
//   1. Comparison tables    — 🟢/🔴 diff-highlighted rows (must precede numeric)
//   2. Numeric tables       — stat cards + donut/bar/progress chart
//   3. Field-value cards    — identity / contract / entity lookup profiles
//   4. Watchlist urgency    — overdue / expiring items grouped by urgency
//
// Returns null when no pattern matches (same contract as MessageAnalytics).
import React from "react";

// ═══════════════════════════════════════════════════════════════════════════
// Shared helpers
// ═══════════════════════════════════════════════════════════════════════════

function num(s) {
  if (s == null) return null;
  // \b ensures "10-K Report" / "Account-123" don't yield 10 / 123
  const m = String(s).replace(/,/g, "").match(/\b-?\d+(?:\.\d+)?\b/);
  return m ? parseFloat(m[0]) : null;
}

// Extract a currency hint from a value string for multi-currency tables.
const CURRENCY_RX = /(?:USD|SGD|EUR|GBP|JPY|CNY|INR|AUD|CAD|HKD|CHF|MYR|THB|KRW|IDR|PHP|VND|AED|SAR)\b|[$€£¥₹₩฿]|S\$/g;
function currencyOf(s) {
  if (s == null) return "";
  const m = String(s).match(CURRENCY_RX);
  return m ? m[0] : "";
}

function isDateLike(s) {
  const str = String(s || "");
  return /\d{1,4}[-/]\d{1,2}[-/]\d{1,4}/.test(str) ||
         /\b\d{1,2}[-/\s][A-Za-z]{3,}[-/\s]\d{2,4}\b/.test(str);
}

function fmt(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.00$/, "");
}

function stats(vals) {
  if (!vals.length) return null;
  const s = [...vals].sort((a, b) => a - b);
  const sum = s.reduce((a, b) => a + b, 0);
  const mid = Math.floor(s.length / 2);
  const median = s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  return { min: s[0], max: s[s.length - 1], avg: sum / s.length, median };
}

const PALETTE = ["var(--gold)", "var(--emerald)", "var(--amber)", "var(--rose)", "var(--violet)", "var(--slate)", "var(--terracotta)"];

const StatCard = React.memo(function StatCard({ label, value }) {
  return (
    <div className="border rounded-md" style={{ padding: "6px 10px", minWidth: 0, flex: 1 }}>
      <div className="ink3" style={{ fontSize: 9, letterSpacing: ".08em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 15, fontWeight: 600 }}>{fmt(value)}</div>
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. Numeric table detector (ported + enhanced from MessageAnalytics)
// ═══════════════════════════════════════════════════════════════════════════

// Simple cache — comparison + numeric detectors both call parseTable on the same content.
let _parseTableCacheKey = null;
let _parseTableCacheVal = null;

function parseTable(md) {
  if (md === _parseTableCacheKey) return _parseTableCacheVal;
  _parseTableCacheKey = md;
  const lines = (md || "").split("\n");
  const cells = (l) => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(s => s.trim());
  for (let i = 0; i < lines.length - 1; i++) {
    const sep = lines[i + 1] || "";
    // Require separator-row cells to each contain leading/trailing - or : so
    // data rows like | -1 | -2 | -3 | aren't mistaken for a separator.
    const sepCells = sep.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(s => s.trim());
    if (/\|/.test(lines[i]) && /^[\s:|-]+$/.test(sep.trim()) && sepCells.every(c => /^:?-{3,}:?$/.test(c))) {
      const header = cells(lines[i]);
      const rows = [];
      for (let j = i + 2; j < lines.length && /\|/.test(lines[j]) && rows.length < 200; j++) {
        const r = cells(lines[j]);
        if (r.some(x => x)) rows.push(r);
      }
      if (header.length >= 2 && rows.length >= 1) { _parseTableCacheVal = { header, rows }; return _parseTableCacheVal; }
    }
  }
  _parseTableCacheVal = null;
  return null;
}

// Inline SVG donut chart — ring of coloured arcs proportional to each category's count.
const DonutChart = React.memo(function DonutChart({ bars, barKind }) {
  // barKind is always "count" at this call site — the value branch is unreachable.
  const total = bars.reduce((s, b) => s + b.v, 0);
  if (!total) return null;
  const r = 14, c = 17, circ = 2 * Math.PI * r;
  let offset = 0;
  const slices = bars.map((b, i) => {
    const pct = b.v / total;
    const dash = pct * circ;
    const s = { dash, dashoffset: circ - offset, color: PALETTE[i % PALETTE.length], pct };
    offset += dash;
    return s;
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
      <svg width={34} height={34} viewBox="0 0 34 34" style={{ flexShrink: 0 }} role="img" aria-label="Donut chart">
        <title>Donut chart</title>
        {slices.map((s, i) => (
          <circle key={i} cx={c} cy={c} r={r} fill="none" stroke={s.color}
            strokeWidth="6" strokeDasharray={`${s.dash} ${circ - s.dash}`}
            strokeDashoffset={s.dashoffset}
            strokeLinecap="butt" transform="rotate(-90 17 17)"
            style={{ transition: "stroke-dasharray .3s" }} />
        ))}
      </svg>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 10, minWidth: 0 }}>
        {bars.slice(0, 6).map((b, i) => (
          <div key={i} className="row gap-1" style={{ alignItems: "center" }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: PALETTE[i % PALETTE.length], flexShrink: 0 }} />
            <span className="ink2 truncate" style={{ flex: 1 }} title={b.label}>{b.label}</span>
            <span className="mono ink3">{b.v}</span>
          </div>
        ))}
      </div>
    </div>
  );
});

// Progress bars — for percentage columns (values 0-100 or header contains "%").
const ProgressBars = React.memo(function ProgressBars({ bars }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {bars.map((b, i) => {
        const v = Math.max(0, Math.min(100, Math.abs(b.v)));
        const color = v <= 50 ? "var(--emerald)" : v <= 80 ? "var(--amber)" : "var(--rose)";
        return (
          <div key={i} className="row gap-2" style={{ alignItems: "center" }}>
            <span className="ink2 truncate" style={{ fontSize: 10, width: "clamp(60px, 25%, 100px)", flexShrink: 0 }}
              title={b.label}>{b.label}</span>
            <div style={{ flex: 1, height: 7, background: "var(--line)", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${v}%`, height: "100%", background: color, borderRadius: 4,
                transition: "width .3s" }} />
            </div>
            <span className="mono ink3" style={{ fontSize: 10, width: 36, textAlign: "right", flexShrink: 0 }}>
              {v.toFixed(0)}%
            </span>
          </div>
        );
      })}
    </div>
  );
});

function detectNumericTable(text) {
  let table;
  try { table = parseTable(text); } catch { table = null; }
  if (!table) return null;
  const { header, rows } = table;

  // Pick numeric column — prefer a header that names a measure.
  const cols = header.map((_, c) => rows.map(r => r[c]));
  const HINT = /amount|value|count|qty|quantity|total|worker|number|price|balance|score|age|sum|rate|#|pct|%|percent/i;
  let numCol = -1, best = -1;
  cols.forEach((col, c) => {
    const parsed = col.map(num).filter(v => v != null);
    const ratio = parsed.length / rows.length;
    if (ratio < 0.6) return;
    const score = ratio + (HINT.test(header[c] || "") ? 1 : 0) + c * 0.001;
    if (score > best) { best = score; numCol = c; }
  });
  if (numCol < 0) return null;

  // Label column.
  let labelCol = cols.findIndex((col, c) =>
    c !== numCol && col.filter(x => x && num(x) == null && !isDateLike(x)).length >= rows.length * 0.6);
  if (labelCol < 0) {
    labelCol = cols.findIndex((col, c) => c !== numCol && col.some(x => x && num(x) == null));
  }
  if (labelCol < 0) labelCol = numCol === 0 ? Math.min(1, header.length - 1) : 0;

  const vals = rows.map(r => num(r[numCol])).filter(v => v != null);
  const st = stats(vals);
  if (!st) return null;

  // Determine bar kind — categorical count mix or per-label value.
  let groupCol = -1, fewest = Infinity;
  cols.forEach((col, c) => {
    if (c === numCol) return;
    if (col.filter(x => x && num(x) == null).length < rows.length * 0.6) return;
    const d = new Set(col.map(x => (x || "").toLowerCase()).filter(Boolean)).size;
    if (d >= 2 && d <= 12 && d < fewest && d <= Math.max(rows.length * 0.7, 2)) { fewest = d; groupCol = c; }
  });

  // Is the numeric column already a count/quantity column? If so the values
  // ARE the counts — use them directly for the donut. Don't count rows.
  const isCountCol = /\b(count|qty|quantity)\b|#|number\s*of/i.test(header[numCol] || "");

  let bars, barKind;
  if (isCountCol) {
    // The numeric column itself is a count → use values directly, render as donut
    bars = rows.map(r => ({ label: r[labelCol] || "—", v: num(r[numCol]) }))
      .filter(b => b.v != null).sort((a, b) => b.v - a.v).slice(0, 8);
    barKind = "count";
  } else if (groupCol >= 0) {
    // Repeated categories across rows → count occurrences per category
    const counts = new Map();
    rows.forEach(r => { const k = r[groupCol] || "—"; counts.set(k, (counts.get(k) || 0) + 1); });
    bars = [...counts.entries()].map(([label, v]) => ({ label, v })).sort((a, b) => b.v - a.v).slice(0, 8);
    barKind = "count";
  } else {
    bars = rows.map(r => ({ label: r[labelCol] || "—", v: num(r[numCol]) }))
      .filter(b => b.v != null).sort((a, b) => Math.abs(b.v) - Math.abs(a.v)).slice(0, 8);
    barKind = "value";
  }

  // Multi-currency: if values use different currency symbols, suffix labels
  // so USD/SGD amounts aren't misleadingly merged into one series.
  const currencies = new Set(rows.map(r => currencyOf(r[numCol])).filter(Boolean));
  if (currencies.size > 1) {
    bars = bars.map(b => {
      const sourceRow = rows.find(r => (r[labelCol] || "—") === b.label);
      const cur = sourceRow ? currencyOf(sourceRow[numCol]) : "";
      return { ...b, label: cur ? `${b.label} (${cur})` : b.label };
    });
  }

  // Percentages: only when header hints it OR values look genuinely %-like
  // (exclude categorical-count data — 2 invoices / 1 statement are NOT percentages).
  const allPct = barKind !== "count" && (
    /%|pct|percent|ratio|rate|coverage|score/i.test(header[numCol] || "") ||
    (vals.every(v => v >= 0 && v <= 100) && vals.some(v => v !== Math.round(v) || v > 20) &&
     !/\b(age|id|number|year|month|day|rank|level|grade|index)\b/i.test(header[numCol] || ""))
  );

  // Check for 🟢/🔴 in any cell.
  const hasTrend = rows.some(r => r.some(cell => /[🟢🔴]/.test(String(cell))));

  return { header, rows, numCol, labelCol, vals, st, bars, barKind, allPct, hasTrend };
}

function renderNumericTable(data) {
  const { header, numCol, vals, st, bars, barKind, allPct, hasTrend } = data;
  const barMax = allPct ? 100 : Math.max(...bars.map(b => Math.abs(b.v)), 1);

  return (
    <div className="border rounded-md mt-2" style={{ padding: 10, background: "var(--bg2)" }}>
      <div className="ink3" style={{ fontSize: 9, letterSpacing: ".08em", marginBottom: 8 }}>
        {header[numCol] ? header[numCol].toUpperCase() : "SUMMARY"} · {vals.length} values
      </div>

      {/* Stat cards */}
      <div className="row" style={{ gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        <StatCard label={allPct ? "AVG %" : "AVG"} value={st.avg} />
        <StatCard label="MEDIAN" value={st.median} />
        <StatCard label="MIN" value={st.min} />
        <StatCard label="MAX" value={st.max} />
      </div>

      {/* Chart area */}
      {allPct ? (
        <ProgressBars bars={bars} />
      ) : barKind === "count" ? (
        <DonutChart bars={bars} barKind={barKind} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {bars.map((b, i) => (
            <div key={i} className="row" style={{ gap: 8, alignItems: "center" }}>
              <span className="ink2" style={{ fontSize: 10, width: 120, flexShrink: 0, overflow: "hidden",
                textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={b.label}>{b.label}</span>
              <div style={{ flex: 1, height: 8, background: "var(--line)", borderRadius: 4, overflow: "hidden" }}>
                <div style={{ width: `${Math.max(2, (Math.abs(b.v) / barMax) * 100)}%`, height: "100%",
                  background: PALETTE[i % PALETTE.length], borderRadius: 4 }} />
              </div>
              <span className="mono" style={{ fontSize: 10, width: 46, textAlign: "right", flexShrink: 0 }}>
                {fmt(b.v)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Trend badge for 🟢/🔴 comparison tables */}
      {hasTrend && (
        <div className="row gap-1 mt-2" style={{ fontSize: 10 }}>
          <span className="ink3" style={{ letterSpacing: ".04em" }}>
            🟢 = same · 🔴 = differs
          </span>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. Field-value cards detector
// ═══════════════════════════════════════════════════════════════════════════

// Icon map — label → emoji (best-effort, fallback is a dot).
const FIELD_ICONS = {
  full_name: "👤", name: "👤", first_name: "👤", last_name: "👤",
  date_of_birth: "🎂", dob: "🎂", birth_date: "🎂",
  nationality: "🌍", citizenship: "🌍", country: "🌍",
  document_number: "🔢", id_number: "🔢", passport_number: "🔢", nric: "🔢",
  expiry: "📅", expiry_date: "📅", expiration: "📅", issued: "📅", date: "📅",
  sex: "⚧", gender: "⚧",
  amount: "💰", total: "💰", sum: "💰", balance: "💰", price: "💰",
  address: "🏠", residence: "🏠",
  email: "✉️", phone: "📞", contact: "📞",
  parties: "🤝", duration: "⏱", services: "📋",
  registration: "📝", number: "🔢",
  type: "📄", category: "📂", status: "🏷",
};

// Pre-built exact-match index for iconFor — avoids O(n) substring scan per field.
const _iconIndex = new Map(Object.entries(FIELD_ICONS));

function iconFor(label) {
  const key = label.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "");
  const hit = _iconIndex.get(key);
  if (hit) return hit;
  // Fallback substring match for partial keys
  for (const [k, v] of _iconIndex) {
    if (key.includes(k) || k.includes(key)) return v;
  }
  return "•";
}

// Section-stop labels — these are section dividers, not field labels.
const SECTION_STOPS = new Set([
  "documents", "connected to", "dates seen", "identifiers", "amounts",
  "key claims", "flags", "type", "parties", "period", "scope", "period / scope",
]);

function detectFieldValues(text) {
  const lines = text.split("\n");
  // Look for blocks: a bold header line followed by 2+ `- Label: **value**` lines.
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Header: "**Bold** — rest" or "**Bold**" on its own
    const headerMatch = line.match(/^\*{1,2}([^*]+)\*{1,2}\s*(—.*)?$/);
    if (!headerMatch) { i++; continue; }
    const headerName = headerMatch[1].trim();
    const headerRest = (headerMatch[2] || "").replace(/^—\s*/, "").trim();

    // Collect consecutive `- Label: **value**` or `- Label: value` lines
    i++;
    const fields = [];
    while (i < lines.length) {
      const fl = lines[i];
      const fm = fl.match(/^\s*[-*•]\s+([A-Za-z][A-Za-z\s/()-]{0,40}?):\s*\*{0,2}([^*\n]+?)\*{0,2}\s*$/);
      if (!fm) break;
      const label = fm[1].trim();
      const value = fm[2].trim();
      if (SECTION_STOPS.has(label.toLowerCase())) break;
      fields.push({ label, value });
      i++;
    }
    if (fields.length >= 2) {
      blocks.push({ header: headerName, sub: headerRest, fields });
    }
  }
  if (!blocks.length) return null;
  return blocks;
}

function renderFieldValueCards(blocks) {
  const allFields = blocks.flatMap(b => b.fields);
  if (!allFields.length) return null;

  return (
    <div className="border rounded-md mt-2" style={{ padding: 10, background: "var(--bg2)" }}>
      {blocks.map((block, bi) => (
        <div key={bi} style={{ marginBottom: bi < blocks.length - 1 ? 10 : 0 }}>
          {/* Block header */}
          <div className="row gap-2 mb-2" style={{ alignItems: "baseline" }}>
            <span className="ink" style={{ fontSize: 12, fontWeight: 600 }}>{block.header}</span>
            {block.sub && <span className="ink3" style={{ fontSize: 10 }}>— {block.sub}</span>}
          </div>
          {/* Field cards grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 6,
          }}>
            {block.fields.map((f, fi) => (
              <div key={fi} className="border rounded-md"
                style={{
                  padding: "7px 9px", background: "var(--bg1)",
                  borderLeft: `2px solid ${PALETTE[fi % PALETTE.length]}`,
                }}>
                <div className="row gap-1 mb-1" style={{ alignItems: "center" }}>
                  <span style={{ fontSize: 10 }}>{iconFor(f.label)}</span>
                  <span className="ink3 upper" style={{ fontSize: 7.5, letterSpacing: ".06em" }}>{f.label}</span>
                </div>
                <div className="ink mono" style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.3 }}>
                  {f.value || "—"}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. Watchlist urgency cards detector
// ═══════════════════════════════════════════════════════════════════════════

const URGENCY_COLORS = {
  "🔴": { css: "var(--rose)", label: "overdue" },
  "🟠": { css: "var(--amber)", label: "this week" },
  "🟡": { css: "var(--gold)", label: "this month" },
  "🟣": { css: "var(--violet)", label: "next 3 months" },
  "🟢": { css: "var(--emerald)", label: "later" },
};

function detectWatchlist(text) {
  const lines = text.split("\n");
  // Only match the watchlist pattern — starts with a specific intro sentence
  // or has urgency-coded bold headers.
  const groups = [];
  let i = 0;
  // Find the first urgency header.
  while (i < lines.length) {
    const line = lines[i];
    const m = line.match(/^\*{1,2}([🔴🟠🟡🟣🟢])\s*(.+?)\*{1,2}\s*$/);
    if (!m) { i++; continue; }
    const urgencyEmoji = m[1];
    const urgencyLabel = m[2].trim();
    const uc = URGENCY_COLORS[urgencyEmoji];
    if (!uc) { i++; continue; }

    i++;
    const items = [];
    while (i < lines.length) {
      const il = lines[i];
      // Item: "- **Title** — date · _DocName_" or "- **Title** — details"
      const im = il.match(/^\s*[-*•]\s+\*{1,2}([^*]+)\*{1,2}\s*(—.*)?$/);
      if (!im) {
        // Check if it's a sub-line (suggestion text indented under an item)
        if (il.trim() && !il.match(/^\s*[-*•]\s/) && !il.match(/^\*{1,2}[🔴🟠🟡🟣🟢]/)) {
          // Continuation line — append to previous item's suggestion
          if (items.length > 0) {
            const prev = items[items.length - 1];
            prev.suggestion = (prev.suggestion || "") + " " + il.trim();
          }
          i++; continue;
        }
        // Check for next urgency header
        if (il.match(/^\*{1,2}[🔴🟠🟡🟣🟢]/)) break;
        // Line with no bullet prefix — could be suggestion for last item or a break
        if (il.trim() && !il.startsWith("**")) { i++; continue; }
        break;
      }
      const itemTitle = im[1].trim();
      const itemRest = (im[2] || "").replace(/^—\s*/, "").trim();
      items.push({ title: itemTitle, detail: itemRest, suggestion: "" });
      i++;
    }
    if (items.length > 0) groups.push({ emoji: urgencyEmoji, label: urgencyLabel, color: uc.css, colorLabel: uc.label, items });
  }
  if (!groups.length) return null;
  return groups;
}

function renderWatchlistCards(groups) {
  return (
    <div className="border rounded-md mt-2" style={{ padding: 10, background: "var(--bg2)" }}>
      <div className="ink3" style={{ fontSize: 9, letterSpacing: ".08em", marginBottom: 8 }}>
        WATCHLIST · needs attention
      </div>
      {groups.map((g, gi) => (
        <div key={gi} style={{ marginBottom: gi < groups.length - 1 ? 8 : 0 }}>
          {/* Urgency header */}
          <div className="row gap-1 mb-1" style={{ alignItems: "center" }}>
            <span style={{ fontSize: 13 }}>{g.emoji}</span>
            <span className="ink" style={{ fontSize: 11.5, fontWeight: 600 }}>{g.label}</span>
            <span className="mono ink3" style={{ fontSize: 9 }}>· {g.items.length}</span>
          </div>
          {/* Item cards */}
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {g.items.map((item, ii) => (
              <div key={ii} className="border rounded-md"
                style={{
                  padding: "6px 9px", background: "var(--bg1)",
                  borderLeft: `3px solid ${g.color}`,
                }}>
                <div className="row gap-2" style={{ alignItems: "baseline", flexWrap: "wrap" }}>
                  <span className="ink mono" style={{ fontSize: 11.5, fontWeight: 600 }}>{item.title}</span>
                  {item.detail && (
                    <span className="ink2" style={{ fontSize: 10 }}>{item.detail}</span>
                  )}
                </div>
                {item.suggestion && (
                  <div className="ink3 mt-1" style={{ fontSize: 10, lineHeight: 1.4 }}>{item.suggestion.trim()}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. Comparison table detector
// ═══════════════════════════════════════════════════════════════════════════

function detectComparisonTable(text) {
  const table = parseTable(text);
  if (!table) return null;
  // Check if the last column contains 🟢 or 🔴 in ANY row.
  const lastCol = table.header.length - 1;
  const hasMarker = table.rows.some(r => /[🟢🔴]/.test(String(r[lastCol] || "")));
  if (!hasMarker) return null;

  // Count differences.
  const diffs = table.rows.filter(r => String(r[lastCol] || "").includes("🔴")).length;

  return { ...table, diffs, markerCol: lastCol };
}

function renderComparisonTable(data) {
  const { header, rows, diffs, markerCol } = data;
  // Remove the marker column for display — it's shown as row backgrounds instead.
  const displayHeader = header.filter((_, c) => c !== markerCol);
  const parse = (r) => r.map((c, ci) => ci === markerCol ? null : c).filter((_, ci) => ci !== markerCol);

  return (
    <div className="border rounded-md mt-2" style={{ padding: 10, background: "var(--bg2)" }}>
      <div className="ink3" style={{ fontSize: 9, letterSpacing: ".08em", marginBottom: 8 }}>
        COMPARISON · {diffs} difference{diffs === 1 ? "" : "s"}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
          <thead>
            <tr>
              {displayHeader.map((h, hi) => (
                <th key={hi} style={{ padding: "4px 8px", background: "var(--bg3)", fontWeight: 600,
                  textAlign: "left", borderBottom: "2px solid var(--gold2)", whiteSpace: "nowrap", fontSize: 10.5 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => {
              const marker = String(row[markerCol] || "");
              const isDiff = marker.includes("🔴");
              const isSame = marker.includes("🟢");
              const bg = isDiff ? "color-mix(in srgb, var(--rose) 8%, transparent)" : isSame ? "color-mix(in srgb, var(--emerald) 6%, transparent)" : "transparent";
              const cells = parse(row);
              return (
                <tr key={ri} style={{ background: ri % 2 ? "var(--bg3)" : "var(--bg1)" }}>
                  {cells.map((c, ci) => (
                    <td key={ci} style={{
                      padding: "4px 8px", borderBottom: "1px solid var(--line)",
                      background: ci > 0 ? bg : "transparent",
                      fontWeight: ci > 0 && isDiff ? 600 : 400,
                      color: ci > 0 ? (isDiff ? "var(--rose)" : isSame ? "var(--emerald)" : "var(--ink2)") : "var(--ink2)",
                    }}>{c}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="ink3 row gap-3 mt-1" style={{ fontSize: 9 }}>
        <span>🟢 same</span>
        <span>🔴 differs</span>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Orchestrator — runs detectors in priority order, renders the first match.
// ═══════════════════════════════════════════════════════════════════════════

export default function SmartVisuals({ content }) {
  if (!content) return null;

  // 1. Comparison tables with 🟢/🔴 — must run before numeric: a comparison
  //    table has numeric columns too, and numeric would steal it as a bar chart.
  const comparison = detectComparisonTable(content);
  if (comparison) return renderComparisonTable(comparison);

  // 2. Numeric tables (most common)
  const tableData = detectNumericTable(content);
  if (tableData) return renderNumericTable(tableData);

  // 3. Field-value cards (identity, contract, entity lookups)
  const fieldBlocks = detectFieldValues(content);
  if (fieldBlocks) return renderFieldValueCards(fieldBlocks);

  // 4. Watchlist urgency cards
  const watchlist = detectWatchlist(content);
  if (watchlist) return renderWatchlistCards(watchlist);

  return null;
}
