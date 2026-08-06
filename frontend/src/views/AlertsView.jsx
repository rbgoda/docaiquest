// Unified Alerts — merged Assistant watchlist + Intelligence alert engine.
// Single urgency-ranked feed of everything needing the user's attention.
// Owner-scoped, zero-LLM for core output.
import React, { useState } from "react";
import { useApiResource } from "../api/useApi.js";
import { fetchAlerts } from "../api/documents";
import { LoadingState, ErrorState } from "../components/Shell.jsx";

const KIND_ICON = {
  expiry: "⏳", payment: "💳", renewal: "🔁", contract: "📄", review: "🔍",
  ingestion_failed: "❌", overdue: "❗", expired: "⏰", due_soon: "📅",
  expiring_soon: "⏳", unclassified: "🏷", low_confidence: "⚠",
  low_ocr_confidence: "👁",
};

const URGENCY = {
  overdue:  { label: "Overdue",        color: "#D8625E", soft: "rgba(216,98,94,0.13)" },
  urgent:   { label: "This week",      color: "#E0662E", soft: "rgba(224,102,46,0.13)" },
  soon:     { label: "This month",     color: "#E0A23B", soft: "rgba(224,162,59,0.13)" },
  upcoming: { label: "Next 3 months",  color: "#8B7FD6", soft: "rgba(139,127,214,0.13)" },
  info:     { label: "Later",          color: "#3FA47A", soft: "rgba(63,164,122,0.10)" },
};

const SEVERITY = {
  high:   { label: "Needs action", color: "#D8625E", soft: "rgba(216,98,94,0.10)" },
  warn:   { label: "Coming up",    color: "#E0A23B", soft: "rgba(224,162,59,0.10)" },
  review: { label: "To review",    color: "#8B7FD6", soft: "rgba(139,127,214,0.10)" },
};

const ORDER = ["overdue", "urgent", "soon", "upcoming", "info"];

function whenLabel(days) {
  if (days == null) return "";
  if (days === 0) return "today";
  if (days < 0) return `${Math.abs(days)} day${days === -1 ? "" : "s"} ago`;
  if (days === 1) return "tomorrow";
  if (days < 45) return `in ${days} days`;
  if (days < 400) return `in ${Math.round(days / 30)} months`;
  return `in ${(days / 365).toFixed(days < 730 ? 1 : 0)} years`;
}

function urgencyFor(item) {
  // Watchlist items have urgency directly
  if (item.urgency) return URGENCY[item.urgency] || URGENCY.info;
  // Intelligence items map severity → urgency
  if (item.severity === "high") return URGENCY.overdue;
  if (item.severity === "warn") return URGENCY.soon;
  if (item.severity === "review") return URGENCY.info;
  return URGENCY.info;
}

function itemIcon(item) {
  if (item.kind) return KIND_ICON[item.kind] || "•";
  if (item.type) return KIND_ICON[item.type] || "•";
  return "•";
}

function itemTitle(item) {
  return item.title || item.type?.replace(/_/g, " ") || "Alert";
}

function itemDetail(item) {
  return item.suggestion || item.detail || "";
}

function itemDateLabel(item) {
  if (!item.date && !item.dueAt) return "";
  const d = item.date || item.dueAt;
  const days = item.daysUntil ?? item.daysDelta;
  if (days == null) return d;
  return `${d} · ${whenLabel(days)}`;
}

// ── Calendar download ───────────────────────────────────────────────────

async function downloadIcs(item, remindDays) {
  if (!item.icsUrl) return;
  const url = `/api${item.icsUrl}&remind_days=${remindDays}`;
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) return;
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(item.title || "reminder").replace(/[^a-z0-9]+/gi, "_").toLowerCase()}.ics`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}

// ── KPI strip ───────────────────────────────────────────────────────────

function Kpi({ label, value, accent = "var(--gold2)" }) {
  return (
    <div className="bg1 border rounded-xl" style={{ flex: "1 1 140px", minWidth: 140, padding: "14px 16px" }}>
      <div className="upper ink3" style={{ fontSize: 10, letterSpacing: "0.12em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 24, fontWeight: 600, color: accent, marginTop: 4, lineHeight: 1 }}>{value}</div>
    </div>
  );
}

// ── Alert item card ─────────────────────────────────────────────────────

function AlertItem({ item, onOpenDocument }) {
  const u = urgencyFor(item);
  const hasCalendar = !!item.icsUrl;
  const [remind, setRemind] = useState(14);
  const [added, setAdded] = useState(false);
  const add = async () => { await downloadIcs(item, remind); setAdded(true); setTimeout(() => setAdded(false), 2600); };
  const sev = item.severity ? SEVERITY[item.severity] : null;

  return (
    <div className="bg1 border rounded-lg" style={{ display: "flex", gap: 0, overflow: "hidden" }}>
      <div style={{ width: 4, background: sev ? sev.color : u.color, flex: "0 0 4px" }} />
      <div style={{ flex: 1, padding: "13px 15px", display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <div className="row between" style={{ alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span className="row gap-2" style={{ alignItems: "baseline", minWidth: 0 }}>
            <span style={{ fontSize: 15 }}>{itemIcon(item)}</span>
            <span className="serif" style={{ fontSize: 15.5 }}>{itemTitle(item)}</span>
            {item.source === "intelligence" && (
              <span className="upper mono" style={{ fontSize: 8, padding: "2px 5px", borderRadius: 3, background: sev?.soft || "var(--bg3)", color: sev?.color || "var(--ink3)" }}>{sev?.label || item.severity}</span>
            )}
          </span>
          <span className="mono" style={{ fontSize: 11.5, color: u.color, fontWeight: 600, whiteSpace: "nowrap" }}>
            {itemDateLabel(item)}
          </span>
        </div>

        {itemDetail(item) && (
          <div className="ink3" style={{ fontSize: 12.5, lineHeight: 1.5 }}>{itemDetail(item)}</div>
        )}

        <div className="row between" style={{ alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button onClick={() => onOpenDocument?.(item.docId)} title="Open this document"
            className="ink3 truncate" style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11.5, padding: 0, maxWidth: 260, textAlign: "left" }}>
            📎 {item.docName}
          </button>
          {hasCalendar && (
            <div className="row gap-2" style={{ alignItems: "center" }}>
              <label className="ink3" style={{ fontSize: 10.5 }}>remind
                <select value={remind} onChange={(e) => setRemind(Number(e.target.value))}
                  className="border bg2" style={{ marginLeft: 4, fontSize: 10.5, padding: "1px 3px", borderRadius: 4, color: "var(--ink2)" }}>
                  <option value={0}>on the day</option>
                  <option value={3}>3 days before</option>
                  <option value={7}>1 week before</option>
                  <option value={14}>2 weeks before</option>
                  <option value={30}>1 month before</option>
                </select>
              </label>
              <button onClick={add} className="border bg2 hover-bg"
                style={{ fontSize: 11, padding: "5px 11px", borderRadius: 999, cursor: "pointer",
                         color: added ? "var(--emerald)" : "var(--ink)", borderColor: added ? "var(--emerald)" : undefined }}>
                {added ? "✓ Calendar file saved" : "＋ Add calendar reminder"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main view ────────────────────────────────────────────────────────────

export default function AlertsView({ onOpenDocument }) {
  const { data, loading, error } = useApiResource(fetchAlerts);
  if (loading) return <LoadingState label="Scanning your documents…" />;
  if (error) return <ErrorState message={error} />;

  const items = data?.items || [];
  const counts = data?.counts || {};
  const kpis = data?.kpis || {};

  return (
    <div style={{ maxWidth: 860, margin: "0 auto", display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 className="serif" style={{ fontSize: 26, lineHeight: 1.1, margin: 0 }}>Alerts</h1>
        <p className="ink3" style={{ fontSize: 13.5, marginTop: 5 }}>
          Everything that needs your attention — renewals, expiries, payments, and documents to review.
        </p>
      </div>

      {/* KPI strip */}
      {kpis.totalDocs > 0 && (
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <Kpi label="Documents" value={kpis.totalDocs} />
          <Kpi label="Ready" value={kpis.readyDocs} accent="var(--emerald)" />
          <Kpi label="Need attention" value={kpis.needsAttention} accent={kpis.overdueCount > 0 ? "var(--rose)" : "var(--gold2)"} />
          <Kpi label="Overdue / High" value={kpis.overdueCount || 0} accent="#D8625E" />
        </div>
      )}

      {items.length === 0 ? (
        <div className="bg1 border rounded-xl" style={{ padding: "34px 20px", textAlign: "center" }}>
          <div style={{ fontSize: 30, marginBottom: 8 }}>✅</div>
          <div className="serif" style={{ fontSize: 17 }}>All clear</div>
          <div className="ink3" style={{ fontSize: 13, marginTop: 4 }}>
            Nothing needs your attention right now. Upload documents to get started — alerts appear automatically.
          </div>
        </div>
      ) : (
        <>
          {/* Urgency badges */}
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {ORDER.filter((k) => counts[k]).map((k) => (
              <span key={k} className="mono" style={{ fontSize: 11, padding: "4px 11px", borderRadius: 999,
                background: URGENCY[k].soft, color: URGENCY[k].color, fontWeight: 600 }}>
                {counts[k]} · {URGENCY[k].label.toLowerCase()}
              </span>
            ))}
            {["high", "warn", "review"].filter((k) => counts[k]).map((k) => (
              <span key={k} className="mono" style={{ fontSize: 11, padding: "4px 11px", borderRadius: 999,
                background: SEVERITY[k]?.soft, color: SEVERITY[k]?.color, fontWeight: 600 }}>
                {counts[k]} · {SEVERITY[k]?.label?.toLowerCase()}
              </span>
            ))}
          </div>

          {/* Grouped feed: overdue high-alert items first, then urgency groups */}
          {(() => {
            const overdue = items.filter(it =>
              it.urgency === "overdue" || it.severity === "high");
            const rest = items.filter(it =>
              it.urgency !== "overdue" && it.severity !== "high");

            return (
              <>
                {overdue.length > 0 && (
                  <div key="overdue-group" style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                    <div className="upper" style={{ fontSize: 10, letterSpacing: ".1em", color: URGENCY.overdue.color, fontWeight: 600 }}>
                      ⚠ Needs action now
                    </div>
                    {overdue.map((it, i) => (
                      <AlertItem key={it.docId + (it.field || it.type || i)} item={it} onOpenDocument={onOpenDocument} />
                    ))}
                  </div>
                )}

                {ORDER.filter(k => k !== "overdue").map((k) => {
                  const group = rest.filter(it => it.urgency === k);
                  if (!group.length) return null;
                  return (
                    <div key={k} style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                      <div className="upper" style={{ fontSize: 10, letterSpacing: ".1em", color: URGENCY[k].color, fontWeight: 600 }}>
                        {URGENCY[k].label}
                      </div>
                      {group.map((it, i) => (
                        <AlertItem key={it.docId + (it.field || it.type || i)} item={it} onOpenDocument={onOpenDocument} />
                      ))}
                    </div>
                  );
                })}

                {/* Intelligence-only items (review severity, no urgency mapping) */}
                {(() => {
                  const reviewItems = rest.filter(it =>
                    it.severity === "review" || it.severity === "warn");
                  if (!reviewItems.length) return null;
                  // Already shown above — skip if urgency-mapped
                  const remaining = reviewItems.filter(it => !it.urgency);
                  if (!remaining.length) return null;
                  return (
                    <div key="review-group" style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                      <div className="upper" style={{ fontSize: 10, letterSpacing: ".1em", color: "#8B7FD6", fontWeight: 600 }}>
                        To review
                      </div>
                      {remaining.map((it, i) => (
                        <AlertItem key={it.docId + (it.field || it.type || i)} item={it} onOpenDocument={onOpenDocument} />
                      ))}
                    </div>
                  );
                })()}
              </>
            );
          })()}
        </>
      )}
    </div>
  );
}
