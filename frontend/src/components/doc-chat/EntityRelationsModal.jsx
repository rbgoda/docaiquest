// EntityRelationsModal — detail card for a selected entity showing its
// outgoing and incoming relations. Replaces the old SVG-based NodeDetail.
import React from "react";
import { kindColor, kindLabel } from "./graphConstants.js";

export default function EntityRelationsModal({ entity, entities, relations, onClose }) {
  if (!entity) return null;

  const outgoing = relations.filter(r => r.srcEntityPk === entity.pk);
  const incoming = relations.filter(r => r.dstEntityPk === entity.pk);
  const color = kindColor(entity.kind);

  function lookupEntity(pk) {
    return entities.find(e => e.pk === pk);
  }

  function ConnectedEntity({ pk }) {
    const e = lookupEntity(pk);
    if (!e) return <span className="ink3 mono" style={{ fontSize: 10 }}>pk:{pk}</span>;
    return (
      <span className="row gap-1" style={{ alignItems: "center" }}>
        <span style={{
          display: "inline-block", width: 7, height: 7,
          borderRadius: "50%", background: kindColor(e.kind), flexShrink: 0,
        }}/>
        <span style={{ fontSize: 11, color: "var(--ink0, #F8FAFC)" }}>
          {e.canonical || e.text || "·"}
        </span>
        <span style={{ fontSize: 9, color: kindColor(e.kind), textTransform: "uppercase" }}>
          {kindLabel(e.kind)}
        </span>
      </span>
    );
  }

  return (
    <div style={{
      position: "absolute", top: 0, right: 0, bottom: 0,
      width: 300, maxWidth: "100%", zIndex: 20,
      background: "var(--bg1, #0F172A)", borderLeft: "1px solid var(--border-color, #334155)",
      display: "flex", flexDirection: "column", overflow: "hidden",
    }}>
      {/* Header */}
      <div className="row between" style={{
        padding: "10px 12px", borderBottom: "1px solid var(--border-color, #334155)",
        alignItems: "flex-start", flexShrink: 0,
      }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span style={{
              display: "inline-block", width: 10, height: 10,
              borderRadius: "50%", background: color, flexShrink: 0,
            }}/>
            <span style={{
              fontSize: 14, fontWeight: 700, color: "var(--ink0, #F8FAFC)",
              overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {entity.canonical || entity.text || "·"}
            </span>
          </div>
          <div className="row gap-2 mt-1" style={{ marginLeft: 18 }}>
            <span style={{
              fontSize: 10, fontWeight: 600, color,
              textTransform: "uppercase", letterSpacing: ".04em",
            }}>
              {kindLabel(entity.kind)}
            </span>
            {entity.confidence != null ? (
              <span className="ink3 mono" style={{ fontSize: 10 }}>
                {Math.round(entity.confidence * 100)}% conf
              </span>
            ) : null}
          </div>
          {entity.canonical && entity.canonical !== entity.text ? (
            <div className="ink3 mt-1 mono" style={{
              fontSize: 10, marginLeft: 18,
              overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {entity.canonical}
            </div>
          ) : null}
        </div>
        <button onClick={onClose} style={{
          fontSize: 20, lineHeight: 1, cursor: "pointer",
          background: "none", border: "none", color: "var(--ink3, #64748B)",
          padding: "0 4px", flexShrink: 0,
        }}>×</button>
      </div>

      {/* Relations list */}
      <div style={{ flex: 1, overflow: "auto", padding: 10 }}>
        {/* Outgoing */}
        <div style={{ marginBottom: outgoing.length ? 16 : 0 }}>
          <div className="upper ink2 mb-2" style={{ fontSize: 9, letterSpacing: ".06em" }}>
            Outgoing ({outgoing.length})
          </div>
          {outgoing.length === 0 ? (
            <div className="ink3" style={{ fontSize: 10, fontStyle: "italic" }}>(none)</div>
          ) : (
            outgoing.map(r => (
              <div key={r.pk} className="row gap-2" style={{
                alignItems: "center", padding: "6px 8px",
                marginBottom: 4, borderRadius: 6,
                background: "var(--bg2, #1E293B)",
                fontSize: 11,
              }}>
                <span className="ink3" style={{ flexShrink: 0 }}>→</span>
                <span style={{
                  flexShrink: 0, fontSize: 10, fontWeight: 600,
                  color: "var(--ink1, #E2E8F0)",
                  background: "var(--bg3, #334155)", padding: "1px 6px",
                  borderRadius: 4,
                }}>
                  {r.relation}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <ConnectedEntity pk={r.dstEntityPk}/>
                </span>
              </div>
            ))
          )}
        </div>

        {/* Incoming */}
        <div>
          <div className="upper ink2 mb-2" style={{ fontSize: 9, letterSpacing: ".06em" }}>
            Incoming ({incoming.length})
          </div>
          {incoming.length === 0 ? (
            <div className="ink3" style={{ fontSize: 10, fontStyle: "italic" }}>(none)</div>
          ) : (
            incoming.map(r => (
              <div key={r.pk} className="row gap-2" style={{
                alignItems: "center", padding: "6px 8px",
                marginBottom: 4, borderRadius: 6,
                background: "var(--bg2, #1E293B)",
                fontSize: 11,
              }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <ConnectedEntity pk={r.srcEntityPk}/>
                </span>
                <span style={{
                  flexShrink: 0, fontSize: 10, fontWeight: 600,
                  color: "var(--ink1, #E2E8F0)",
                  background: "var(--bg3, #334155)", padding: "1px 6px",
                  borderRadius: 4,
                }}>
                  {r.relation}
                </span>
                <span className="ink3" style={{ flexShrink: 0 }}>→</span>
              </div>
            ))
          )}
        </div>

        {outgoing.length === 0 && incoming.length === 0 ? (
          <div className="ink3 mt-4" style={{
            fontSize: 11, fontStyle: "italic", textAlign: "center",
          }}>
            No relations for this entity.
          </div>
        ) : null}
      </div>
    </div>
  );
}
