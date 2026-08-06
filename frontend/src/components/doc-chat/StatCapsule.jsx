// StatCapsule — bordered card with label, big value, optional sub-label.
// Shared between GraphTab and any other stats display.
import React from "react";

export default function StatCapsule({ label, value, color, sub }) {
  return (
    <div className="border rounded-md" style={{
      padding: "6px 12px", minWidth: 0, flex: "0 1 auto",
      borderLeft: `3px solid ${color || "var(--gold, #D4A843)"}`,
      background: "var(--bg2, #1E293B)",
    }}>
      <div className="ink3 upper" style={{ fontSize: 9, letterSpacing: ".06em" }}>{label}</div>
      <div className="mono" style={{
        fontSize: 18, fontWeight: 700,
        color: color || "var(--ink0, #F8FAFC)",
        lineHeight: 1.3,
      }}>
        {value}
      </div>
      {sub ? <div className="ink3" style={{ fontSize: 10, marginTop: 1 }}>{sub}</div> : null}
    </div>
  );
}
