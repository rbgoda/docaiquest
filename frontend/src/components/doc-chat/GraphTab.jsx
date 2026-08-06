// GraphTab — per-document + cross-document entity graph.
// Two modes: "List" (cards grouped by kind) and "Graph" (force-directed SVG).
// Cross-document: search an entity name → progressive click-to-expand ego-network.
//
// Progressive exploration:
//   Search returns the full subgraph (capped at HOPS depth). Initially ONLY
//   identity-core nodes are visible (person + document hubs). Single-click a
//   collapsed node to reveal its 1-hop neighbors. Double-click any node for
//   the detail side-panel. "Expand All" / "Collapse All" provide quick global
//   control. The HOPS slider sets the max fetch depth from the API.
//
// Pattern: EditHistory (inline states) + LinkedTab (clickable cards).
// Backend: GET /api/graph/document/{docId} → { entities, relations }
//          GET /api/graph/identity-graph?q=...&depth=... → cross-document ego-network

import React, { useState, useCallback, useEffect, useRef } from "react";
import { fetchDocGraph, fetchIdentityGraph } from "../../api";
import { useApiResource } from "../../api/useApi.js";
import { kindColor, kindLabel } from "./graphConstants.js";
import StatCapsule from "./StatCapsule.jsx";
import KindRibbon from "./KindRibbon.jsx";
import EntityCard from "./EntityCard.jsx";
import EntityRelationsModal from "./EntityRelationsModal.jsx";
import ForceGraph from "./ForceGraph.jsx";
import EntityTree from "./EntityTree.jsx";

/** Compute the set of visible node PKs given the current expansion state and
 *  the full edge list. Rules:
 *   1. Depth-0 nodes (identity core) are ALWAYS visible.
 *   2. Expanded nodes are visible.
 *   3. Nodes directly connected to an expanded node are visible.
 *  Everything else is hidden. */
function computeVisiblePks(allNodes, allEdges, expandedPks) {
  const expanded = new Set(expandedPks);
  const visible = new Set();

  // Build adjacency map: pk → Set of neighbor pks
  const adj = new Map();
  for (const e of allEdges) {
    if (!adj.has(e.srcEntityPk)) adj.set(e.srcEntityPk, new Set());
    if (!adj.has(e.dstEntityPk)) adj.set(e.dstEntityPk, new Set());
    adj.get(e.srcEntityPk).add(e.dstEntityPk);
    adj.get(e.dstEntityPk).add(e.srcEntityPk);
  }

  for (const n of allNodes) {
    // Rule 1: identity core is always visible
    if (n.depth === 0) {
      visible.add(n.pk);
      continue;
    }
    // Rule 2: expanded nodes themselves
    if (expanded.has(n.pk)) {
      visible.add(n.pk);
      continue;
    }
    // Rule 3: has at least one expanded neighbor
    const neighbors = adj.get(n.pk);
    if (neighbors) {
      for (const npk of neighbors) {
        if (expanded.has(npk)) {
          visible.add(n.pk);
          break;
        }
      }
    }
  }

  return visible;
}

export default function GraphTab({ doc }) {
  const [mode, setMode] = useState("tree"); // "tree" | "graph" | "list" — tree is default
  const [selected, setSelected] = useState(null);
  const [crossDocQ, setCrossDocQ] = useState("");
  const [crossDocData, setCrossDocData] = useState(null);
  const [crossDocLoading, setCrossDocLoading] = useState(false);
  const [crossDocError, setCrossDocError] = useState(null);
  const [depth, setDepth] = useState(3); // max fetch depth (1–5)
  const [expandedPks, setExpandedPks] = useState(new Set());

  const isStandalone = !doc; // workspace-level: no single document

  // ── Per-document data (skipped when standalone / no doc) ──────────────
  const { data, loading, error } = useApiResource(
    () => doc ? fetchDocGraph(doc.id) : Promise.resolve({ entities: [], relations: [] }),
    [doc?.id],
  );

  // ── Cross-document search ────────────────────────────────────────────
  const searchCrossDoc = useCallback(async (e) => {
    e?.preventDefault?.();
    const q = crossDocQ.trim();
    if (!q) return;
    setCrossDocLoading(true);
    setCrossDocError(null);
    setCrossDocData(null);
    setExpandedPks(new Set()); // reset expansion
    try {
      const result = await fetchIdentityGraph(q, depth);
      if (result.found) {
        setCrossDocData(result);
        setMode("tree");
      } else {
        setCrossDocError(`No entity matching "${q}" found across your documents.`);
      }
    } catch (err) {
      setCrossDocError(err.message || "Search failed");
    }
    setCrossDocLoading(false);
  }, [crossDocQ, depth]);

  // Re-fetch when depth changes while there's an active cross-doc result
  const prevDepthRef = useRef(depth);
  useEffect(() => {
    if (prevDepthRef.current !== depth && crossDocData?.found && crossDocQ.trim()) {
      prevDepthRef.current = depth;
      searchCrossDoc({ preventDefault: () => {} });
    } else {
      prevDepthRef.current = depth;
    }
  }, [depth]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Safe defaults (never early-return — the top bar must stay visible) ──
  const entities = Array.isArray(data?.entities) ? data.entities : [];
  const relations = Array.isArray(data?.relations) ? data.relations : [];

  // ── Cross-doc: full graph (fetched) vs visible graph (filtered) ──────
  const allNodes = crossDocData?.nodes?.length
    ? crossDocData.nodes
    : entities.map(e => ({ ...e, depth: 0 }));
  const allEdges = crossDocData?.edges?.length
    ? crossDocData.edges
    : relations;

  // Compute which nodes are visible based on expansion state
  const isCrossDoc = !!(crossDocData?.nodes?.length);
  let graphNodes = allNodes;
  let graphEdges = allEdges;
  if (isCrossDoc) {
    const visiblePks = computeVisiblePks(allNodes, allEdges, expandedPks);
    graphNodes = allNodes.filter(n => visiblePks.has(n.pk));
    graphEdges = allEdges.filter(e => visiblePks.has(e.srcEntityPk) && visiblePks.has(e.dstEntityPk));
  }

  // Toggle a node's expansion
  const handleToggleExpand = useCallback((pk) => {
    setExpandedPks(prev => {
      const next = new Set(prev);
      if (next.has(pk)) {
        next.delete(pk);
      } else {
        next.add(pk);
      }
      return next;
    });
  }, []);

  // Expand / collapse all
  const expandAll = () => {
    // Expand every node that has at least one edge (i.e. has neighbors)
    const pkSet = new Set();
    for (const e of allEdges) {
      pkSet.add(e.srcEntityPk);
      pkSet.add(e.dstEntityPk);
    }
    setExpandedPks(pkSet);
  };
  const collapseAll = () => setExpandedPks(new Set());

  const expandedCount = expandedPks.size;
  const totalExpandable = (() => {
    const pkSet = new Set();
    for (const e of allEdges) {
      pkSet.add(e.srcEntityPk);
      pkSet.add(e.dstEntityPk);
    }
    return pkSet.size;
  })();

  // ── Derived data for list mode + stats ───────────────────────────────
  const kindCounts = {};
  for (const e of entities) {
    const k = e.kind || "other";
    kindCounts[k] = (kindCounts[k] || 0) + 1;
  }

  const degreeMap = new Map();
  for (const r of relations) {
    degreeMap.set(r.srcEntityPk, (degreeMap.get(r.srcEntityPk) || 0) + 1);
    degreeMap.set(r.dstEntityPk, (degreeMap.get(r.dstEntityPk) || 0) + 1);
  }

  const byKind = {};
  for (const e of entities) {
    const k = kindLabel(e.kind);
    if (!byKind[k]) byKind[k] = [];
    byKind[k].push(e);
  }

  const sortedKinds = Object.entries(kindCounts).sort((a, b) => b[1] - a[1]);
  const topKind = sortedKinds[0];
  const density = entities.length > 1
    ? Math.round((relations.length / (entities.length * (entities.length - 1) / 2)) * 100)
    : null;

  const isEmpty = entities.length === 0 && !crossDocData;

  return (
    <div style={{
      display: "flex", flexDirection: "column", height: "100%",
      background: "var(--bg1, #1A1E2E)", position: "relative",
    }}>
      {/* ── Top bar: mode toggle + cross-doc search + depth ────────────── */}
      <div className="row between" style={{
        padding: "6px 10px", borderBottom: "1px solid var(--line)",
        flexShrink: 0, gap: 6, flexWrap: "wrap", alignItems: "center",
      }}>
        {/* Mode toggle pills */}
        <div className="row" style={{ gap: 2, background: "var(--bg2)", borderRadius: 8, padding: 2 }}>
          <button onClick={() => setMode("tree")}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 11, cursor: "pointer",
              background: mode === "tree" ? "var(--gold2)" : "transparent",
              color: mode === "tree" ? "var(--ink)" : "var(--ink3)",
              border: "none", fontWeight: mode === "tree" ? 600 : 400,
              whiteSpace: "nowrap",
            }}>
            🌳 Tree
          </button>
          <button onClick={() => setMode("graph")}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 11, cursor: "pointer",
              background: mode === "graph" ? "var(--gold2)" : "transparent",
              color: mode === "graph" ? "var(--ink)" : "var(--ink3)",
              border: "none", fontWeight: mode === "graph" ? 600 : 400,
              whiteSpace: "nowrap",
            }}>
            🕸 Graph
          </button>
          <button onClick={() => { setMode("list"); setCrossDocData(null); setCrossDocError(null); }}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 11, cursor: "pointer",
              background: mode === "list" ? "var(--gold2)" : "transparent",
              color: mode === "list" ? "var(--ink)" : "var(--ink3)",
              border: "none", fontWeight: mode === "list" ? 600 : 400,
              whiteSpace: "nowrap",
            }}>
            📋 List
          </button>
        </div>

        {/* Cross-document search */}
        <form onSubmit={searchCrossDoc} className="row" style={{ gap: 4, alignItems: "center", flex: 1, maxWidth: 300, minWidth: 160 }}>
          <input
            type="text"
            value={crossDocQ}
            onChange={e => setCrossDocQ(e.target.value)}
            placeholder="Search entity across ALL docs…"
            className="bg2 border"
            style={{
              flex: 1, padding: "4px 8px", borderRadius: 5, fontSize: 11,
              color: "var(--ink)", outline: "none",
            }}
          />
          <button type="submit" disabled={crossDocLoading || !crossDocQ.trim()}
            className="btn-gold"
            style={{ padding: "4px 10px", borderRadius: 5, fontSize: 10, cursor: "pointer", whiteSpace: "nowrap" }}>
            {crossDocLoading ? "…" : "🌐 Find"}
          </button>
          {crossDocData && (
            <button onClick={() => { setCrossDocData(null); setCrossDocError(null); setCrossDocQ(""); }}
              style={{ padding: "3px 6px", borderRadius: 5, fontSize: 11, cursor: "pointer",
                background: "transparent", border: "1px solid var(--line)", color: "var(--ink3)" }}>
              ✕
            </button>
          )}
        </form>

        {/* Depth slider — max fetch hops */}
        <div className="row" style={{ gap: 3, alignItems: "center", flexShrink: 0 }}>
          <span className="ink3" style={{ fontSize: 9, whiteSpace: "nowrap" }}>HOPS</span>
          <input
            type="range" min={1} max={5} step={1}
            value={depth}
            onChange={e => setDepth(Number(e.target.value))}
            style={{ width: 40, cursor: "pointer", accentColor: "var(--gold, #F59E0B)" }}
            title={`Max API fetch depth: ${depth}`}
          />
          <span style={{ fontSize: 10, fontWeight: 700, color: "var(--ink2)", minWidth: 10, textAlign: "center" }}>
            {depth}
          </span>
        </div>
      </div>

      {/* ── Cross-doc status bar + expand/collapse controls ──────────── */}
      {crossDocData && (
        <div className="row between" style={{
          padding: "5px 10px", fontSize: 10, color: "var(--ink2)",
          background: "color-mix(in srgb, var(--gold) 8%, var(--bg1))",
          borderBottom: "1px solid var(--line)", flexShrink: 0, alignItems: "center",
          gap: 6, flexWrap: "wrap",
        }}>
          <span>
            🕸 <b>{crossDocData.identity?.name || crossDocData.query}</b>
            {" "}· {crossDocData.identity?.kind || "entity"}
            {" "}· <span style={{background:"yellow",color:"#000",padding:"0 4px",borderRadius:3,fontWeight:700}}>{crossDocData.identity?.docCount || 0}</span> doc{(crossDocData.identity?.docCount || 0) !== 1 ? "s" : ""}
            {" "}· <span style={{fontSize:8,opacity:.6}}>[debug: nodes={crossDocData.nodes?.length || 0}, seedPk={crossDocData.seedPk}]</span>
          </span>
          <span className="row" style={{ gap: 4, alignItems: "center" }}>
            <span className="ink3" style={{ fontSize: 9 }}>
              {graphNodes.length}/{allNodes.length}n · {graphEdges.length}/{allEdges.length}e
            </span>
            {/* Expand / Collapse All */}
            <button onClick={expandAll}
              disabled={expandedCount >= totalExpandable}
              title="Expand all nodes"
              style={{
                padding: "2px 6px", borderRadius: 3, fontSize: 9, cursor: "pointer",
                background: "transparent", border: "1px solid var(--line)",
                color: expandedCount >= totalExpandable ? "var(--ink3)" : "var(--ink2)",
                opacity: expandedCount >= totalExpandable ? 0.5 : 1,
              }}>
              ⊞ All
            </button>
            <button onClick={collapseAll}
              disabled={expandedCount === 0}
              title="Collapse all — show only identity core"
              style={{
                padding: "2px 6px", borderRadius: 3, fontSize: 9, cursor: "pointer",
                background: "transparent", border: "1px solid var(--line)",
                color: expandedCount === 0 ? "var(--ink3)" : "var(--ink2)",
                opacity: expandedCount === 0 ? 0.5 : 1,
              }}>
              ⊟ Core
            </button>
          </span>
        </div>
      )}
      {crossDocError && (
        <div style={{
          padding: "5px 10px", fontSize: 10, color: "var(--rose, #D8625E)",
          borderBottom: "1px solid var(--line)", flexShrink: 0,
          background: "color-mix(in srgb, var(--rose) 6%, var(--bg1))",
        }}>
          {crossDocError}
        </div>
      )}

      {/* ── Hint for first-time users ────────────────────────────────── */}
      {isCrossDoc && expandedCount === 0 && (
        <div style={{
          padding: "5px 10px", fontSize: 10, color: "var(--ink3)",
          borderBottom: "1px solid var(--line)", flexShrink: 0,
          textAlign: "center", fontStyle: "italic",
        }}>
          💡 Click ▶ to expand · click entity name to collapse · double-click for detail
        </div>
      )}

      {/* ── Status states (content area only — top bar stays visible) ────── */}
      {loading && (
        <div className="ink3 text-xs" style={{ padding: 24, textAlign: "center", fontStyle: "italic" }}>
          Loading entity graph…
        </div>
      )}
      {!loading && error && (
        <div className="text-xs" style={{ padding: 24, textAlign: "center", color: "var(--rose, #D8625E)" }}>
          {error}
        </div>
      )}
      {!loading && !error && isEmpty && (
        <div style={{ padding: 32, textAlign: "center" }}>
          <div style={{ fontSize: 40, marginBottom: 12, opacity: 0.5 }}>🕸</div>
          <div className="ink2" style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
            {isStandalone ? "Explore entities across all your documents" : "No entities extracted for this document yet."}
          </div>
          <div className="ink4 text-xs" style={{ fontStyle: "italic", maxWidth: 320, margin: "0 auto" }}>
            {isStandalone
              ? "Search for a person, company, or identifier above to see its connections across your entire document library."
              : "Upload a document and run extraction to populate the entity graph."}
          </div>
        </div>
      )}

      {/* ── Tree mode (default) — hierarchical entity browser ────────── */}
      {!loading && !error && !isEmpty && mode === "tree" && (
        <EntityTree
          allNodes={allNodes}
          allEdges={allEdges}
          expandedPks={expandedPks}
          onToggleExpand={isCrossDoc ? handleToggleExpand : undefined}
          onEntityClick={setSelected}
        />
      )}

      {/* ── Graph mode — force-directed SVG ──────────────────────────── */}
      {!loading && !error && !isEmpty && mode === "graph" && (
        <div style={{ flex: 1, minHeight: 0 }}>
          <ForceGraph
            nodes={graphNodes}
            edges={graphEdges}
            selectedPk={selected?.pk}
            onNodeClick={setSelected}
            expandedPks={expandedPks}
            onToggleExpand={isCrossDoc ? handleToggleExpand : undefined}
            allEdges={isCrossDoc ? allEdges : undefined}
          />
        </div>
      )}

      {/* ── List mode ────────────────────────────────────────────────── */}
      {!loading && !error && !isEmpty && mode === "list" && (
        <>
          {/* Stats capsules row */}
          <div className="row gap-2" style={{
            padding: "8px 10px", flexWrap: "wrap", alignItems: "stretch",
          }}>
            <StatCapsule
              label="Entities" value={entities.length}
              color="var(--gold, #F59E0B)"
              sub={`${Object.keys(kindCounts).length} kind${Object.keys(kindCounts).length > 1 ? "s" : ""}`}
            />
            <StatCapsule
              label="Relations" value={relations.length}
              color="var(--violet, #8B5CF6)"
              sub={density != null ? `${density}% dense` : ""}
            />
            {topKind ? (
              <StatCapsule
                label="Top Kind" value={kindLabel(topKind[0])}
                color={kindColor(topKind[0])}
                sub={`${topKind[1]} of ${entities.length}`}
              />
            ) : null}

            {sortedKinds.slice(0, 5).map(([kind, count]) => (
              <span key={kind} className="row gap-1" style={{
                alignItems: "center", padding: "3px 8px", borderRadius: 999,
                background: `${kindColor(kind)}18`,
                border: `1px solid ${kindColor(kind)}40`,
                fontSize: 10, fontWeight: 600, whiteSpace: "nowrap",
              }}>
                <span style={{
                  display: "inline-block", width: 7, height: 7,
                  borderRadius: "50%", background: kindColor(kind),
                }}/>
                {kindLabel(kind)} <span style={{ opacity: 0.65 }}>{count}</span>
              </span>
            ))}
          </div>

          {/* Kind distribution ribbon */}
          <div style={{ padding: "0 10px 6px" }}>
            <KindRibbon counts={kindCounts} total={entities.length}/>
          </div>

          {/* Entity list grouped by kind */}
          <div style={{ flex: 1, overflow: "auto", padding: "6px 10px 10px" }}>
            {Object.entries(byKind)
              .sort(([, a], [, b]) => b.length - a.length)
              .map(([groupLabel, groupEntities]) => (
                <div key={groupLabel} style={{ marginBottom: 16 }}>
                  <div className="upper ink2 mb-2" style={{
                    fontSize: 10, letterSpacing: ".06em",
                  }}>
                    {groupLabel} · {groupEntities.length}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {groupEntities.map(e => (
                      <EntityCard
                        key={e.pk}
                        entity={e}
                        relationCount={degreeMap.get(e.pk) || 0}
                        onClick={setSelected}
                      />
                    ))}
                  </div>
                </div>
              ))}
          </div>

          {/* Footer */}
          <div className="row between ink3" style={{
            fontSize: 10, padding: "5px 12px",
            borderTop: "1px solid var(--border-color, #334155)",
            flexShrink: 0,
          }}>
            <span>
              {entities.length} entit{entities.length === 1 ? "y" : "ies"} · {relations.length} relation{relations.length === 1 ? "" : "s"}
            </span>
            <span>
              {selected
                ? `Viewing: ${selected.canonical || selected.text || "·"}`
                : "Click an entity for details"}
            </span>
          </div>
        </>
      )}

      {/* ── Entity detail side-panel (both modes) ──────────────────────── */}
      {selected ? (
        <EntityRelationsModal
          entity={selected}
          entities={allNodes}
          relations={allEdges}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </div>
  );
}
