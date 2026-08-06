// EntityCard — clickable card for one graph entity. Shows text, kind badge,
// relation count, confidence. Follows LinkedTab card pattern.
import React from "react";
import { kindColor, kindLabel, FALLBACK_COLOR } from "./graphConstants.js";

export default function EntityCard({ entity, relationCount, onClick }) {
  if (!entity) return null;

  const text = entity.canonical || entity.text || "·";
  const color = kindColor(entity.kind);
  const conf = entity.confidence != null ? Math.round(entity.confidence * 100) : null;

  return (
    <button
      onClick={() => onClick(entity)}
      className="bg2 border rounded-md text-left"
      style={{
        padding: "10px 12px", cursor: "pointer", width: "100%",
        borderLeft: `3px solid ${color}`,
      }}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        {/* Left: entity name + kind */}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span style={{
              display: "inline-block", width: 9, height: 9,
              borderRadius: "50%", background: color, flexShrink: 0,
            }}/>
            <span className="mono" style={{
              fontSize: 13, fontWeight: 600, color: "var(--ink0, #F8FAFC)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {text}
            </span>
          </div>
          <div className="row gap-2 mt-1" style={{ marginLeft: 17 }}>
            <span style={{
              fontSize: 9, fontWeight: 600, color,
              textTransform: "uppercase", letterSpacing: ".04em",
            }}>
              {kindLabel(entity.kind)}
            </span>
            {entity.page != null ? (
              <span className="ink3" style={{ fontSize: 9 }}>p.{entity.page}</span>
            ) : null}
          </div>
        </div>

        {/* Right: relation count + confidence */}
        <div className="col" style={{ alignItems: "flex-end", flexShrink: 0, marginLeft: 8 }}>
          <span style={{
            fontSize: 11, fontWeight: 700,
            color: relationCount > 0 ? "var(--gold, #F59E0B)" : "var(--ink3, #64748B)",
          }}>
            {relationCount} link{relationCount !== 1 ? "s" : ""}
          </span>
          {conf != null ? (
            <span className="ink3 mono" style={{ fontSize: 9 }}>
              {conf}% conf
            </span>
          ) : null}
        </div>
      </div>
    </button>
  );
}
