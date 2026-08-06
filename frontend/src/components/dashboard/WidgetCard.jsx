// WidgetCard — wrapper for each dashboard widget. Renders the widget title bar,
// size-responsive grid placement, and edit chrome (× remove, ⚙ configure, ↕ reorder)
// when in customize mode.

import React from "react";

const SIZE_GRID = {
  small:  "1 / span 1",
  medium: "1 / span 2",
  large:  "1 / span 3",
  full:   "1 / span 4",
};

export default function WidgetCard({ widget, children, customize, onRemove, onConfigure, onMoveUp, onMoveDown }) {
  const gridCol = SIZE_GRID[widget.size] || SIZE_GRID.medium;

  return (
    <div className="bg1 border rounded-xl" style={{
      gridColumn: gridCol, overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      {/* Title bar */}
      <div className="row between" style={{
        padding: "10px 14px", borderBottom: "1px solid var(--line)",
        alignItems: "center", gap: 8,
      }}>
        <div className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
            {widget.title}
          </span>
          {widget.source === "ai" && (
            <span className="upper mono" style={{ fontSize: 8, padding: "2px 5px", borderRadius: 4,
              background: "rgba(139,127,214,0.15)", color: "#8B7FD6" }}>AI</span>
          )}
        </div>

        {/* Edit chrome (only in customize mode) */}
        {customize && (
          <div className="row gap-1" style={{ alignItems: "center", flexShrink: 0 }}>
            <button onClick={onMoveUp} title="Move up"
              style={chromeBtnStyle}>↑</button>
            <button onClick={onMoveDown} title="Move down"
              style={chromeBtnStyle}>↓</button>
            <button onClick={onConfigure} title="Configure"
              style={chromeBtnStyle}>⚙</button>
            <button onClick={onRemove} title="Remove"
              style={{ ...chromeBtnStyle, color: "var(--rose)" }}>×</button>
          </div>
        )}
      </div>

      {/* Widget content */}
      <div style={{ flex: 1, padding: "12px 14px", overflow: "auto" }}>
        {children}
      </div>
    </div>
  );
}

const chromeBtnStyle = {
  width: 26, height: 26, borderRadius: 6,
  background: "var(--bg2)", border: "1px solid var(--line)",
  cursor: "pointer", fontSize: 13, color: "var(--ink2)",
  display: "grid", placeItems: "center", lineHeight: 1,
};
