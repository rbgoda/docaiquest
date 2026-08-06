import React from "react";

// A vertical drag handle to resize an adjacent pane by width. `invert` flips the delta direction —
// use it when the pane being sized sits to the RIGHT of the handle (dragging left should GROW it).
export default function DragDivider({ getWidth, setWidth, min, max, invert }) {
  const onDown = (e) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = getWidth();
    const move = (ev) => {
      const d = (ev.clientX - x0) * (invert ? -1 : 1);
      setWidth(Math.max(min, Math.min(max, w0 + d)));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };
  return (
    <div onMouseDown={onDown} title="Drag to resize"
      style={{ flex: "0 0 8px", cursor: "col-resize", alignSelf: "stretch",
               display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: 3, minHeight: 40, alignSelf: "stretch", margin: "8px 0",
                    borderRadius: 3, background: "var(--line)" }} />
    </div>
  );
}
