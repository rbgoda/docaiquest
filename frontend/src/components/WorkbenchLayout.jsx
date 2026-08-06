// M47 · IDE-style multi-panel workspace layout with resizable columns.
// Panels are arranged horizontally. Each panel: resizable, collapsible,
// width persisted to localStorage. Resize handles between panels.
import React, { useState, useEffect, useCallback } from "react";

const LS = "docaiq.workbench.";

function loadW(key, def) {
  try { const v = localStorage.getItem(LS + key); return v ? Number(v) : def; }
  catch { return def; }
}
function saveW(key, v) {
  try { localStorage.setItem(LS + key, String(v)); } catch {}
}

// Panel: a column in the workbench
export function Panel({ w, minW, maxW, collapsed, onToggle, onResize, title, children, style }) {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e) => onResize?.(e.movementX);
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [dragging, onResize]);

  if (collapsed) {
    return (
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", borderRight: "1px solid var(--line)", background: "var(--bg2)" }}>
        <button onClick={onToggle} title={`Open ${title}`}
          style={{ writingMode: "vertical-rl", padding: "8px 6px", fontSize: 10, fontWeight: 600,
            color: "var(--ink3)", background: "none", border: "none", cursor: "pointer",
            letterSpacing: "0.05em", textTransform: "uppercase" }}>
          {title}
        </button>
      </div>
    );
  }

  return (
    <div style={{ flex: w ? "0 0 auto" : "1 1 0", width: w || undefined, minWidth: minW || 0, maxWidth: maxW || undefined,
      display: "flex", flexDirection: "column", overflow: "hidden", ...style }}>
      {/* Header */}
      {title && (
        <div className="row between border-b" style={{ flex: "0 0 auto", padding: "6px 10px", alignItems: "center", background: "var(--bg2)" }}>
          <span className="upper" style={{ fontSize: 9, letterSpacing: ".06em", color: "var(--ink3)" }}>{title}</span>
          <button onClick={onToggle} title={`Close ${title}`}
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: 13, color: "var(--ink3)", padding: 0, lineHeight: 1 }}>✕</button>
        </div>
      )}
      {/* Content */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        {children}
      </div>
      {/* Resize handle — right edge */}
      {onResize && (
        <div onMouseDown={() => setDragging(true)}
          style={{ position: "absolute", right: -3, top: 0, bottom: 0, width: 6, cursor: "col-resize", zIndex: 10 }}
          title="Drag to resize" />
      )}
    </div>
  );
}

// Handle: a thin draggable divider between panels
export function Handle({ onDelta, dir }) {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e) => onDelta(dir === "v" ? e.movementX : -e.movementY);
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [dragging, onDelta, dir]);

  return (
    <div onMouseDown={() => setDragging(true)}
      style={{ flex: "0 0 auto",
        width: dir === "v" ? 5 : "100%", height: dir === "h" ? 5 : "100%",
        cursor: dir === "v" ? "col-resize" : "row-resize",
        background: "var(--line)", opacity: 0.5,
      }}
      title="Drag to resize" />
  );
}

// Workbench: horizontal row of resizable panels
export default function WorkbenchLayout({ panels, height }) {
  // panels: [{id, title, minW, maxW, defaultW, flex, collapsed, onToggle, children}]

  return (
    <div style={{ display: "flex", height: height || "100%", width: "100%", overflow: "hidden" }}>
      {panels.map((p, i) => {
        const isLast = i === panels.length - 1;
        return (
          <React.Fragment key={p.id}>
            <Panel
              w={p.flex ? undefined : p.w}
              minW={p.minW}
              maxW={p.maxW}
              collapsed={p.collapsed}
              onToggle={p.onToggle}
              onResize={!isLast ? (dx) => p.onResize?.(dx) : undefined}
              title={p.title}
              style={{ position: "relative", borderRight: isLast ? "none" : undefined }}>
              {p.children}
            </Panel>
          </React.Fragment>
        );
      })}
    </div>
  );
}
