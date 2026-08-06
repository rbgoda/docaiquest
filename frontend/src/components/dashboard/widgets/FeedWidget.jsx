// FeedWidget — scrollable alert/event list.
import React from "react";

const SEV_COLOR = { high: "#D8625E", warn: "#E0A23B", review: "#8B7FD6" };

export default function FeedWidget({ data }) {
  const items = data?.items || [];
  if (!items.length) {
    return <div className="ink3" style={{ fontSize: 12, textAlign: "center", padding: 20, fontStyle: "italic" }}>Nothing to show</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1, maxHeight: 320, overflowY: "auto" }}>
      {items.map((item, i) => (
        <div key={i} className="row gap-2" style={{
          alignItems: "center", padding: "6px 0",
          borderBottom: i < items.length - 1 ? "1px solid var(--line)" : "none",
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: SEV_COLOR[item.severity] || "var(--ink3)", flexShrink: 0,
          }} />
          <span style={{ fontSize: 12, color: "var(--ink)", flex: 1, minWidth: 0 }} className="truncate">
            {item.title || item.type}
          </span>
          {item.dueAt && (
            <span className="mono ink3" style={{ fontSize: 10, flexShrink: 0 }}>
              {item.dueAt}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
