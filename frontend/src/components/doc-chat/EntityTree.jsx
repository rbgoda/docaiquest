// EntityTree — hierarchical entity browser.
// Renders the same graph data (nodes + edges) as an indented, collapsible
// tree instead of a force-directed hairball. Scales to thousands of entities
// because only expanded branches render their children.
//
// Structure:
//   👤 Person (root)
//     📄 Document A (23 entities)
//       🏢 Orgs (1)
//         🏢 UBS AG
//           📄 (connections...)
//       💰 Money (14)
//         💰 USD 1,228,665
//         ...
//
// Props: { roots, allNodes, allEdges, expandedPks, onToggleExpand, onEntityClick,
//          nodeMap, adjMap, docColorMap, depth }

import React from "react";
import { kindColor, kindLabel } from "./graphConstants.js";

// Emoji per kind for the tree — more scannable than color alone
const KIND_EMOJI = {
  person: "👤", org: "🏢", money: "💰", date: "📅", location: "📍",
  identifier: "🔢", document: "📄", event: "🎫", standard: "📐",
  transaction: "💳", category: "🏷", contact: "📧", misc: "📝",
};

function kindEmoji(kind) {
  return KIND_EMOJI[kind] || "•";
}

/** Group an array of entity PKs by document → kind for hierarchical display. */
function groupByDocAndKind(pks, nodeMap) {
  // docPk → kind → [entity]
  const groups = new Map();
  for (const pk of pks) {
    const ent = nodeMap.get(pk);
    if (!ent) continue;
    const docPk = ent.documentPk ?? 0;
    const kind = ent.kind || "other";
    if (!groups.has(docPk)) groups.set(docPk, new Map());
    const kindMap = groups.get(docPk);
    if (!kindMap.has(kind)) kindMap.set(kind, []);
    kindMap.get(kind).push(ent);
  }
  return groups;
}

/** A single entity row in the tree. */
function EntityRow({ entity, isExpanded, hasChildren, hiddenCount, onToggle, onDetail,
                      docColor, depth }) {
  const emoji = kindEmoji(entity.kind);
  const label = entity.canonical || entity.text || "?";
  const truncated = label.length > 40 ? label.slice(0, 40) + "…" : label;

  return (
    <div
      className="row"
      onClick={() => onToggle(entity.pk)}
      onDoubleClick={() => onDetail(entity)}
      title={`${label}\n${kindLabel(entity.kind)}${entity.documentPk != null ? ` · doc ${entity.documentPk}` : ""}\nClick to expand · double-click for detail`}
      style={{
        padding: "3px 6px", paddingLeft: 12 + depth * 18,
        cursor: "pointer", alignItems: "center", gap: 5,
        fontSize: 11, borderRadius: 4,
        background: "transparent",
        transition: "background 0.1s",
        userSelect: "none",
      }}
      onMouseEnter={e => e.currentTarget.style.background = "var(--bg2)"}
      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
    >
      {/* Expand toggle */}
      <span style={{
        width: 14, fontSize: 8, color: "var(--ink3)", flexShrink: 0,
        textAlign: "center", visibility: hasChildren ? "visible" : "hidden",
        transform: isExpanded ? "rotate(90deg)" : "none",
        transition: "transform 0.15s",
      }}>
        ▶
      </span>

      {/* Kind emoji */}
      <span style={{ flexShrink: 0, fontSize: 13 }}>{emoji}</span>

      {/* Document color dot (if cross-doc) */}
      {docColor && (
        <span style={{
          display: "inline-block", width: 7, height: 7, borderRadius: "50%",
          background: docColor, flexShrink: 0, opacity: 0.8,
        }} />
      )}

      {/* Entity text */}
      <span style={{
        color: "var(--ink)", fontWeight: entity.kind === "document" || entity.kind === "person" ? 600 : 400,
        flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {truncated}
      </span>

      {/* Kind badge */}
      <span style={{
        fontSize: 8, color: kindColor(entity.kind), background: `${kindColor(entity.kind)}18`,
        padding: "1px 5px", borderRadius: 999, fontWeight: 600, flexShrink: 0,
        textTransform: "uppercase", letterSpacing: ".03em",
      }}>
        {kindLabel(entity.kind)}
      </span>

      {/* Hidden children count */}
      {hasChildren && !isExpanded && hiddenCount > 0 && (
        <span style={{
          fontSize: 9, color: "var(--ink3)", background: "var(--bg2)",
          padding: "1px 5px", borderRadius: 999, flexShrink: 0, fontWeight: 600,
        }}>
          +{hiddenCount}
        </span>
      )}
    </div>
  );
}

/** Recursively render a branch of the tree. */
function EntityBranch({ entity, allEdges, expandedPks, onToggleExpand, onEntityClick,
                         nodeMap, adjMap, docColorMap, depth, parentPk }) {
  const pk = entity.pk;
  const isExpanded = expandedPks.has(pk);
  const neighborPks = adjMap.get(pk) || new Set();
  // Filter out the parent to avoid back-links cluttering the tree
  const children = [...neighborPks].filter(cpk => cpk !== parentPk);
  const hasChildren = children.length > 0;
  const docColor = docColorMap.get(entity.documentPk);

  return (
    <div>
      <EntityRow
        entity={entity}
        isExpanded={isExpanded}
        hasChildren={hasChildren}
        hiddenCount={children.length}
        onToggle={onToggleExpand}
        onDetail={onEntityClick}
        docColor={docColor}
        depth={depth}
      />

      {/* Expanded children — grouped by document → kind */}
      {isExpanded && hasChildren && (
        <div>
          {(() => {
            const groups = groupByDocAndKind(children, nodeMap);

            // If there's only one document and few children, skip grouping headers
            const totalDocs = groups.size;
            const allPks = children;

            if (totalDocs <= 1 && allPks.length <= 6) {
              // Flat list — no grouping headers needed
              return allPks.map(cpk => {
                const childEnt = nodeMap.get(cpk);
                if (!childEnt) return null;
                return (
                  <EntityBranch
                    key={cpk}
                    entity={childEnt}
                    allEdges={allEdges}
                    expandedPks={expandedPks}
                    onToggleExpand={onToggleExpand}
                    onEntityClick={onEntityClick}
                    nodeMap={nodeMap}
                    adjMap={adjMap}
                    docColorMap={docColorMap}
                    depth={depth + 1}
                    parentPk={pk}
                  />
                );
              });
            }

            // Multi-document or large group — show grouping headers
            return [...groups.entries()].map(([docPk, kindMap]) => {
              // Find document entity for label
              let docLabel = `Doc ${docPk}`;
              let docEnt = null;
              for (const [k, ents] of kindMap) {
                for (const e of ents) {
                  if (e.kind === "document" && e.documentPk === docPk) {
                    docEnt = e;
                    break;
                  }
                }
                if (docEnt) break;
              }
              if (docEnt) docLabel = (docEnt.canonical || docEnt.text || docLabel).slice(0, 35);

              const docColor = docColorMap.get(docPk);

              return (
                <div key={`doc-${docPk}`}>
                  {/* Document group header */}
                  {totalDocs > 1 && (
                    <div style={{
                      padding: "4px 6px", paddingLeft: 12 + (depth + 1) * 18,
                      fontSize: 9, color: "var(--ink3)", fontWeight: 600,
                      letterSpacing: ".03em", textTransform: "uppercase",
                      display: "flex", alignItems: "center", gap: 5,
                    }}>
                      {docColor && (
                        <span style={{
                          display: "inline-block", width: 6, height: 6, borderRadius: "50%",
                          background: docColor,
                        }} />
                      )}
                      📄 {docLabel}
                    </div>
                  )}

                  {/* Kind groups within this document */}
                  {[...kindMap.entries()].map(([kind, ents]) => {
                    if (ents.length === 0) return null;
                    return (
                      <div key={`kind-${kind}`}>
                        {/* Kind group header (only if > 1 entity in group or multiple kinds) */}
                        {kindMap.size > 1 && (
                          <div style={{
                            padding: "3px 6px", paddingLeft: 12 + (depth + 1) * 18 + 6,
                            fontSize: 9, color: kindColor(kind), fontWeight: 500,
                            display: "flex", alignItems: "center", gap: 4,
                          }}>
                            <span>{kindEmoji(kind)}</span>
                            <span>{kindLabel(kind)}</span>
                            <span style={{ opacity: 0.6 }}>({ents.length})</span>
                          </div>
                        )}

                        {/* Individual entity rows */}
                        {ents.map(childEnt => (
                          <EntityBranch
                            key={childEnt.pk}
                            entity={childEnt}
                            allEdges={allEdges}
                            expandedPks={expandedPks}
                            onToggleExpand={onToggleExpand}
                            onEntityClick={onEntityClick}
                            nodeMap={nodeMap}
                            adjMap={adjMap}
                            docColorMap={docColorMap}
                            depth={depth + 1 + (kindMap.size > 1 ? 1 : 0) + (totalDocs > 1 ? 1 : 0)}
                            parentPk={pk}
                          />
                        ))}
                      </div>
                    );
                  })}
                </div>
              );
            });
          })()}
        </div>
      )}
    </div>
  );
}

/** Build lookup maps from flat node/edge arrays. */
function buildMaps(allNodes, allEdges) {
  const nodeMap = new Map(allNodes.map(n => [n.pk, n]));
  const adjMap = new Map();
  for (const e of allEdges) {
    if (!adjMap.has(e.srcEntityPk)) adjMap.set(e.srcEntityPk, new Set());
    if (!adjMap.has(e.dstEntityPk)) adjMap.set(e.dstEntityPk, new Set());
    adjMap.get(e.srcEntityPk).add(e.dstEntityPk);
    adjMap.get(e.dstEntityPk).add(e.srcEntityPk);
  }
  return { nodeMap, adjMap };
}

// Document color palette
const DOC_PALETTE = [
  "#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6",
  "#EC4899", "#06B6D4", "#F97316",
];

function buildDocColorMap(nodes) {
  const map = new Map();
  let i = 0;
  for (const n of nodes) {
    const dpk = n.documentPk;
    if (dpk != null && !map.has(dpk)) {
      map.set(dpk, DOC_PALETTE[i % DOC_PALETTE.length]);
      i++;
    }
  }
  return map;
}

export default function EntityTree({ allNodes, allEdges, expandedPks, onToggleExpand,
                                      onEntityClick }) {
  if (!allNodes.length) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <div className="ink3" style={{ fontSize: 12, fontStyle: "italic" }}>
          No entities to browse.
        </div>
      </div>
    );
  }

  const { nodeMap, adjMap } = buildMaps(allNodes, allEdges);
  const docColorMap = buildDocColorMap(allNodes);

  // ── Root selection ──────────────────────────────────────────────────
  // Show ALL depth-0 entities grouped by document. This is essential for
  // cross-document identity graphs where each document forms its own
  // disconnected subgraph — picking a single root hides the other docs.
  const depth0 = allNodes.filter(n => n.depth === 0);

  // Group depth-0 entities by document
  const byDoc = new Map();
  for (const n of depth0) {
    const dpk = n.documentPk ?? 0;
    if (!byDoc.has(dpk)) byDoc.set(dpk, []);
    byDoc.get(dpk).push(n);
  }

  // Build doc labels (find a document entity, or use the first entity's doc info)
  const docLabels = new Map();
  for (const [dpk, ents] of byDoc) {
    // Try to find a "document" kind entity to use as the label
    const docEnt = ents.find(e => e.kind === "document");
    const label = docEnt ? (docEnt.canonical || docEnt.text || `Doc ${dpk}`) : `Document ${dpk}`;
    docLabels.set(dpk, label.slice(0, 40));
  }

  // For each document, pick ONE primary root (the highest-degree person, or
  // highest-degree entity). ALL depth-0 entities are still rendered — the
  // primary root just serves as the top-level anchor for that document.
  const docPrimaryRoots = [];
  for (const [dpk, ents] of byDoc) {
    let best = ents[0];
    let bestDeg = -1;
    for (const n of ents) {
      const deg = (adjMap.get(n.pk) || new Set()).size;
      if (n.kind === "person" && deg > bestDeg) { best = n; bestDeg = deg; }
    }
    if (bestDeg < 0) {
      for (const n of ents) {
        const deg = (adjMap.get(n.pk) || new Set()).size;
        if (deg > bestDeg) { best = n; bestDeg = deg; }
      }
    }
    docPrimaryRoots.push({ docPk: dpk, label: docLabels.get(dpk) || `Doc ${dpk}`, root: best, allEnts: ents });
  }

  return (
    <div style={{
      flex: 1, overflow: "auto", padding: "4px 0",
      background: "var(--bg1, #1A1E2E)",
    }}>
      {docPrimaryRoots.length === 0 && (
        <div style={{ padding: 24, textAlign: "center" }}>
          <div className="ink3" style={{ fontSize: 12, fontStyle: "italic" }}>
            No root entities found. Try expanding the search.
          </div>
        </div>
      )}

      {docPrimaryRoots.map(({ docPk, label, root }) => (
        <div key={`doc-${docPk}`}>
          {/* Document header — always shown for consistency between 1-doc and multi-doc views */}
          <div style={{
            padding: "6px 10px", fontSize: 10, fontWeight: 700,
            color: "var(--ink2)", letterSpacing: ".03em",
            textTransform: "uppercase",
            background: "var(--bg2)",
            borderBottom: "1px solid var(--line)",
            display: "flex", alignItems: "center", gap: 5,
          }}>
            {(docColorMap.get(docPk)) && (
              <span style={{
                display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                background: docColorMap.get(docPk),
              }} />
            )}
            📄 {label}
          </div>
          <EntityBranch
            key={root.pk}
            entity={root}
            allEdges={allEdges}
            expandedPks={expandedPks}
            onToggleExpand={onToggleExpand}
            onEntityClick={onEntityClick}
            nodeMap={nodeMap}
            adjMap={adjMap}
            docColorMap={docColorMap}
            depth={0}
            parentPk={null}
          />
        </div>
      ))}

      {/* Footer with counts */}
      <div style={{
        padding: "8px 12px", fontSize: 9, color: "var(--ink3)",
        borderTop: "1px solid var(--line)", marginTop: 8,
        display: "flex", gap: 12,
      }}>
        <span>{allNodes.length} entities</span>
        <span>{allEdges.length} relations</span>
        <span>{docPrimaryRoots.length} doc{docPrimaryRoots.length !== 1 ? "s" : ""}</span>
      </div>
    </div>
  );
}
