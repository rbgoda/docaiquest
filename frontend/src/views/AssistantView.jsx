// Document-intelligence assistant — a watchlist of what your documents want you to know:
// renewals, expiries, payment-due dates, contract ends — urgency-ranked, each with a one-click
// calendar reminder (.ics with an alarm). Everything is derived from already-extracted fields
// (zero extra LLM), owner-scoped by the backend.
import React, { useState } from "react";
import { useApiResource } from "../api/useApi.js";
import { fetchWatchlist } from "../api/documents";
import { LoadingState, ErrorState } from "../components/Shell.jsx";

const KIND_ICON = { expiry: "⏳", payment: "💳", renewal: "🔁", contract: "📄", review: "🔍" };
const URGENCY = {
  overdue:  { label: "Overdue",        color: "#D8625E", soft: "rgba(216,98,94,0.13)" },
  urgent:   { label: "This week",      color: "#E0662E", soft: "rgba(224,102,46,0.13)" },
  soon:     { label: "This month",     color: "#E0A23B", soft: "rgba(224,162,59,0.13)" },
  upcoming: { label: "Next 3 months",  color: "#8B7FD6", soft: "rgba(139,127,214,0.13)" },
  info:     { label: "Later",          color: "#3FA47A", soft: "rgba(63,164,122,0.10)" },
};
const ORDER = ["overdue", "urgent", "soon", "upcoming", "info"];

function whenLabel(days) {
  if (days === 0) return "today";
  if (days < 0) return `${Math.abs(days)} day${days === -1 ? "" : "s"} ago`;
  if (days === 1) return "tomorrow";
  if (days < 45) return `in ${days} days`;
  if (days < 400) return `in ${Math.round(days / 30)} months`;
  return `in ${(days / 365).toFixed(days < 730 ? 1 : 0)} years`;
}

async function downloadIcs(item, remindDays) {
  const url = `/api${item.icsUrl}&remind_days=${remindDays}`;
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) return;
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${item.title.replace(/[^a-z0-9]+/gi, "_").toLowerCase()}.ics`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}

function Item({ it, onOpenDocument }) {
  const u = URGENCY[it.urgency] || URGENCY.info;
  const [remind, setRemind] = useState(it.kind === "payment" ? 3 : 14);
  const [added, setAdded] = useState(false);
  const add = async () => { await downloadIcs(it, remind); setAdded(true); setTimeout(() => setAdded(false), 2600); };
  return (
    <div className="bg1 border rounded-lg" style={{ display: "flex", gap: 0, overflow: "hidden" }}>
      <div style={{ width: 4, background: u.color, flex: "0 0 4px" }} />
      <div style={{ flex: 1, padding: "13px 15px", display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <div className="row between" style={{ alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span className="row gap-2" style={{ alignItems: "baseline", minWidth: 0 }}>
            <span style={{ fontSize: 15 }}>{KIND_ICON[it.kind] || "•"}</span>
            <span className="serif" style={{ fontSize: 15.5 }}>{it.title}</span>
          </span>
          <span className="mono" style={{ fontSize: 11.5, color: u.color, fontWeight: 600, whiteSpace: "nowrap" }}>
            {it.date} · {whenLabel(it.daysUntil)}
          </span>
        </div>
        <div className="ink3" style={{ fontSize: 12.5, lineHeight: 1.5 }}>{it.suggestion}</div>
        <div className="row between" style={{ alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button onClick={() => onOpenDocument?.(it.docId)} title="Open this document"
            className="ink3 truncate" style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11.5, padding: 0, maxWidth: 260, textAlign: "left" }}>
            📎 {it.docName}
          </button>
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
        </div>
      </div>
    </div>
  );
}

export default function AssistantView({ onOpenDocument }) {
  const { data, loading, error } = useApiResource(fetchWatchlist);
  if (loading) return <LoadingState label="Reading your documents…" />;
  if (error) return <ErrorState message={error} />;
  const items = data?.items || [];
  const counts = data?.counts || {};

  return (
    <div style={{ maxWidth: 860, margin: "0 auto", display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 className="serif" style={{ fontSize: 26, lineHeight: 1.1, margin: 0 }}>Assistant</h1>
        <p className="ink3" style={{ fontSize: 13.5, marginTop: 5 }}>
          What your documents want you to know — renewals, expiries and due dates, watched for you.
        </p>
      </div>

      {items.length === 0 ? (
        <div className="bg1 border rounded-xl" style={{ padding: "34px 20px", textAlign: "center" }}>
          <div style={{ fontSize: 30, marginBottom: 8 }}>✅</div>
          <div className="serif" style={{ fontSize: 17 }}>All clear</div>
          <div className="ink3" style={{ fontSize: 13, marginTop: 4 }}>
            Nothing needs your attention right now. New renewals and due dates will appear here automatically as you add documents.
          </div>
        </div>
      ) : (
        <>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {ORDER.filter((k) => counts[k]).map((k) => (
              <span key={k} className="mono" style={{ fontSize: 11, padding: "4px 11px", borderRadius: 999,
                background: URGENCY[k].soft, color: URGENCY[k].color, fontWeight: 600 }}>
                {counts[k]} · {URGENCY[k].label.toLowerCase()}
              </span>
            ))}
          </div>
          {ORDER.map((k) => {
            const group = items.filter((it) => it.urgency === k);
            if (!group.length) return null;
            return (
              <div key={k} style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                <div className="upper" style={{ fontSize: 10, letterSpacing: ".1em", color: URGENCY[k].color, fontWeight: 600 }}>
                  {URGENCY[k].label}
                </div>
                {group.map((it) => <Item key={it.docId + it.field} it={it} onOpenDocument={onOpenDocument} />)}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
