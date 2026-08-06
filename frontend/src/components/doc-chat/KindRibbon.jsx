// KindRibbon — horizontal stacked bar showing entity-kind distribution.
import React from "react";
import { kindColor, kindLabel } from "./graphConstants.js";

export default function KindRibbon({ counts, total }) {
  if (!total) return null;
  const entries = Object.entries(counts || {})
    .filter(([, c]) => c > 0)
    .sort(([, a], [, b]) => b - a);
  return (
    <div className="row" style={{
      gap: 2, height: 6, borderRadius: 3, overflow: "hidden",
      flex: 1, minWidth: 80,
    }}>
      {entries.map(([kind, count]) => (
        <div key={kind} title={`${kindLabel(kind)}: ${count}`}
             style={{
               height: "100%", background: kindColor(kind),
               width: `${Math.max(2, (count / total) * 100)}%`,
             }}/>
      ))}
    </div>
  );
}
