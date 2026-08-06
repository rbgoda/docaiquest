import React from "react";

// Reusable drag-handle for splitter UIs. Place it between two panes (in a flex row
// or a CSS grid); the parent owns the size state.
//
// Props:
//   onDelta(px)   → called on every mousemove with the cursor delta since the last
//                   frame (Δx for vertical handles, Δy for horizontal). The parent
//                   applies it to its size state with whatever min/max + sign it wants.
//   orientation   → "vertical" (default · resizes width, drag left/right) or
//                   "horizontal" (resizes height, drag up/down).
//   ariaLabel     → for screen readers; defaults to "Resize panel".
//   style         → extra styles (e.g. gridColumn / gridRow to place it in a grid).
//
// Visual: a 6px bar, dim by default, gold on hover/drag, with a center grip.
export default function ResizableHandle({ onDelta, ariaLabel = "Resize panel", orientation = "vertical", style }) {
  const horizontal = orientation === "horizontal";

  const onMouseDown = (e) => {
    e.preventDefault();
    let last = horizontal ? e.clientY : e.clientX;
    document.body.style.userSelect = "none";
    document.body.style.cursor = horizontal ? "row-resize" : "col-resize";

    const onMove = (ev) => {
      const cur = horizontal ? ev.clientY : ev.clientX;
      const delta = cur - last;
      last = cur;
      if (delta !== 0) onDelta(delta);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div
      role="separator"
      aria-orientation={horizontal ? "horizontal" : "vertical"}
      aria-label={ariaLabel}
      onMouseDown={onMouseDown}
      style={{
        ...(horizontal ? { height: 6, cursor: "row-resize" } : { width: 6, cursor: "col-resize" }),
        background: "var(--line)",
        position: "relative",
        flexShrink: 0,
        transition: "background 120ms ease",
        ...style,
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--gold)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--line)"; }}
    >
      {/* center grip cue */}
      <div style={{
        position: "absolute", top: "50%", left: "50%",
        transform: "translate(-50%, -50%)",
        ...(horizontal ? { width: 32, height: 2 } : { width: 2, height: 32 }),
        background: "var(--line2)", borderRadius: 1, pointerEvents: "none",
      }}/>
    </div>
  );
}
