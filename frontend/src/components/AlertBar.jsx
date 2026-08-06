// AlertBar — compact, collapsible alert strip for the Dashboard.
// Fetches from /alerts/unified. Collapsed: a thin bar with counts.
// Expanded: inline alert items with doc links. Sits below KPIs, above chat.
import React, { useState } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApiResource } from "../api/useApi.js";
import { fetchAlerts } from "../api/documents";
import Icon from "./Icon.jsx";

const KIND_ICON = {
  expiry: "⏳", payment: "💳", renewal: "🔁", contract: "📄", review: "🔍",
  ingestion_failed: "❌", overdue: "❗", expired: "⏰", due_soon: "📅",
  expiring_soon: "⏳", unclassified: "🏷", low_confidence: "⚠",
  low_ocr_confidence: "👁",
};

const URGENCY_COLOR = {
  overdue:  "#D8625E",
  urgent:   "#E0662E",
  soon:     "#E0A23B",
  upcoming: "#8B7FD6",
  info:     "#3FA47A",
};

const URGENCY_LABEL = {
  overdue: "Overdue", urgent: "This week", soon: "This month",
  upcoming: "Upcoming", info: "Later",
};

function whenLabel(days) {
  if (days == null) return "";
  if (days === 0) return "today";
  if (days < 0) return `${Math.abs(days)}d ago`;
  if (days === 1) return "tomorrow";
  if (days < 45) return `${days}d`;
  if (days < 400) return `${Math.round(days / 30)}mo`;
  return `${(days / 365).toFixed(days < 730 ? 1 : 0)}y`;
}

function urgencyFor(item) {
  if (item.urgency) return item.urgency;
  if (item.severity === "high") return "overdue";
  if (item.severity === "warn") return "soon";
  return "info";
}

// ── Compact alert item (used in expanded bar) ────────────────────────────

function CompactAlert({ item, onOpenDocument }) {
  const u = urgencyFor(item);
  const color = URGENCY_COLOR[u] || "var(--ink3)";
  const title = item.title || item.type?.replace(/_/g, " ") || "Alert";
  const detail = item.suggestion || item.detail || "";
  const days = item.daysUntil ?? item.daysDelta;
  const label = whenLabel(days);
  const icon = item.kind ? KIND_ICON[item.kind] : (item.type ? KIND_ICON[item.type] : "•");

  return (
    <div className="row" style={{
      alignItems: "center", gap: 10, padding: "7px 12px",
      borderLeft: `3px solid ${color}`, minWidth: 0,
      background: "var(--bg1)", fontSize: 12.5,
    }}>
      <span style={{ flex: "0 0 auto", fontSize: 14 }}>{icon}</span>
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        <span style={{ color: "var(--ink)", fontWeight: 500 }}>{title}</span>
        {detail ? <span className="ink3" style={{ marginLeft: 8 }}>{detail}</span> : null}
      </span>
      {label && (
        <span className="mono" style={{ flex: "0 0 auto", fontSize: 11, color, fontWeight: 600, whiteSpace: "nowrap" }}>
          {label}
        </span>
      )}
      {item.docId && onOpenDocument && (
        <button onClick={() => onOpenDocument(item.docId)}
          className="ink3 hover-ink"
          style={{ flex: "0 0 auto", background: "none", border: "none", cursor: "pointer", fontSize: 11, padding: 0, whiteSpace: "nowrap" }}>
          📎 {item.docName || "Open"}
        </button>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────

export default function AlertBar({ onOpenDocument }) {
  // P2 · watchlist alerts are a cloud-only premium feature
  const { isCloud } = useAuth();
  const { data, loading } = useApiResource(isCloud ? fetchAlerts : null);
  const [open, setOpen] = useState(false);

  // Nothing to show while loading or if no alerts
  if (loading) return null;

  const items = data?.items || [];
  const counts = data?.counts || {};
  const kpis = data?.kpis || {};
  const total = items.length;

  if (total === 0) {
    return (
      <div className="bg1 border rounded-lg row" style={{
        alignItems: "center", gap: 8, padding: "10px 14px", fontSize: 13,
      }}>
        <span style={{ fontSize: 15 }}>✅</span>
        <span className="ink2">All clear — nothing needs your attention</span>
      </div>
    );
  }

  // Urgency groups for collapsed summary pills
  const groups = [];
  if (counts.overdue) groups.push(`${counts.overdue} overdue`);
  if (counts.urgent) groups.push(`${counts.urgent} this week`);
  if (counts.soon) groups.push(`${counts.soon} this month`);
  if (counts.upcoming) groups.push(`${counts.upcoming} upcoming`);

  // Top 5 items to show when expanded (most urgent first)
  const ORDER = ["overdue", "urgent", "soon", "upcoming", "info"];
  const sorted = [...items].sort((a, b) => {
    const ai = ORDER.indexOf(urgencyFor(a));
    const bi = ORDER.indexOf(urgencyFor(b));
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  const visible = sorted.slice(0, 5);

  return (
    <div className="bg1 border rounded-lg" style={{ overflow: "hidden" }}>
      {/* Collapsed bar / header */}
      <button
        onClick={() => setOpen(!open)}
        className="row hover-bg"
        style={{
          width: "100%", alignItems: "center", gap: 10, padding: "10px 14px",
          background: "none", border: "none", cursor: "pointer", fontSize: 13,
          color: "var(--ink)", textAlign: "left",
        }}
      >
        <span style={{ fontSize: 16, flex: "0 0 auto" }}>🔔</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{total} alert{total !== 1 ? "s" : ""}</span>
          {groups.length > 0 && (
            <span className="ink3" style={{ marginLeft: 6 }}>
              — {groups.join(", ")}
            </span>
          )}
        </span>
        {kpis.overdueCount > 0 && (
          <span className="mono" style={{
            flex: "0 0 auto", fontSize: 11, padding: "3px 8px", borderRadius: 999,
            background: "rgba(216,98,94,0.15)", color: "#D8625E", fontWeight: 600,
          }}>
            {kpis.overdueCount} urgent
          </span>
        )}
        <span style={{ flex: "0 0 auto", fontSize: 11, color: "var(--ink3)", transition: "transform 0.2s", transform: open ? "rotate(180deg)" : "none" }}>
          ▼
        </span>
      </button>

      {/* Expanded items */}
      {open && (
        <div className="anim-fade" style={{ borderTop: "1px solid var(--line)" }}>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {visible.map((it, i) => (
              <CompactAlert
                key={it.docId + (it.field || it.type || i)}
                item={it}
                onOpenDocument={onOpenDocument}
              />
            ))}
          </div>
          {total > visible.length && (
            <div className="ink3" style={{
              padding: "8px 14px", fontSize: 11.5, borderTop: "1px solid var(--line)",
              textAlign: "center",
            }}>
              +{total - visible.length} more alert{total - visible.length !== 1 ? "s" : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
