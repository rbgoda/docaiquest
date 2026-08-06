// ForceGraph — interactive SVG force-directed entity graph.
// Uses d3-force for physics + d3-zoom/drag for interactivity.
// d3 manages SVG elements directly in a useEffect for 60fps tick perf;
// React owns the container, zoom state, and selection props.
//
// Props: { nodes, edges, onNodeClick, selectedPk, expandedPks, onToggleExpand }
//
// Progressive exploration:
//   Single-click a collapsed node → expand (reveal neighbors)
//   Single-click an expanded node → collapse (hide neighbors)
//   Double-click any node → detail modal (onNodeClick)
//   Document-colored outer rings distinguish which doc each entity came from.

import React, { useEffect, useRef, useState } from "react";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from "d3-force";
import { select } from "d3-selection";
import { zoom } from "d3-zoom";
import { drag } from "d3-drag";
import { kindColor, kindLabel } from "./graphConstants.js";

const NODE_R_MIN = 6;
const NODE_R_MAX = 22;
const LABEL_MAX = 22;
const CHARGE = -350;
const LINK_DISTANCE = 130;
const COLLIDE_R = 36;

// Document color palette — assigned round-robin to unique documentPk values
const DOC_PALETTE = [
  "#3B82F6", // blue
  "#EF4444", // red
  "#10B981", // emerald
  "#F59E0B", // amber
  "#8B5CF6", // violet
  "#EC4899", // pink
  "#06B6D4", // cyan
  "#F97316", // orange
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

/** Count how many hidden (non-visible) neighbors a node has based on the
 *  full edge list. Used for the "+N" badge on collapsed nodes. */
function hiddenNeighborCount(nodePk, visiblePkSet, allEdges) {
  const seen = new Set();
  for (const e of allEdges) {
    if (e.srcEntityPk === nodePk && !visiblePkSet.has(e.dstEntityPk)) seen.add(e.dstEntityPk);
    if (e.dstEntityPk === nodePk && !visiblePkSet.has(e.srcEntityPk)) seen.add(e.srcEntityPk);
  }
  return seen.size;
}

/** Pre-compute hidden-neighbor counts for each node. Requires the FULL
 *  edge list (before filtering) so we can count what's absent. */
function buildHiddenCounts(allNodes, allEdges, visiblePkSet) {
  const map = new Map();
  for (const n of allNodes) {
    const c = hiddenNeighborCount(n.pk, visiblePkSet, allEdges);
    if (c > 0) map.set(n.pk, c);
  }
  return map;
}

export default function ForceGraph({ nodes, edges, onNodeClick, selectedPk,
                                      expandedPks, onToggleExpand, allEdges }) {
  const svgRef = useRef(null);
  const simRef = useRef(null);
  const [hoveredEdge, setHoveredEdge] = useState(null);
  const [dimensions, setDimensions] = useState({ w: 600, h: 400 });
  const containerRef = useRef(null);
  const initRef = useRef(false);
  const expandedSet = expandedPks instanceof Set ? expandedPks : new Set(expandedPks || []);

  // ── ResizeObserver ───────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setDimensions({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Pre-compute hidden-neighbor badges for collapsed nodes
  const visiblePkSet = new Set(nodes.map(n => n.pk));
  const hiddenCounts = buildHiddenCounts(nodes, allEdges || edges, visiblePkSet);

  // ── Build simulation + render SVG elements ──────────────────────────
  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl || !nodes.length) return;

    const W = dimensions.w;
    const H = dimensions.h;
    const cx = W / 2;
    const cy = H / 2;

    // Compute degree for node sizing (only among visible nodes)
    const degMap = new Map();
    for (const e of edges) {
      degMap.set(e.srcEntityPk, (degMap.get(e.srcEntityPk) || 0) + 1);
      degMap.set(e.dstEntityPk, (degMap.get(e.dstEntityPk) || 0) + 1);
    }
    const maxDeg = Math.max(1, ...nodes.map(n => degMap.get(n.pk) || 0));

    // Prepare simulation data
    const simNodes = nodes.map(n => ({
      ...n,
      r: NODE_R_MIN + ((degMap.get(n.pk) || 0) / maxDeg) * (NODE_R_MAX - NODE_R_MIN),
      x: cx + (Math.random() - 0.5) * 80,
      y: cy + (Math.random() - 0.5) * 80,
    }));
    const nodeMap = new Map(simNodes.map(n => [n.pk, n]));

    const simLinks = edges
      .filter(e => nodeMap.has(e.srcEntityPk) && nodeMap.has(e.dstEntityPk))
      .map(e => ({
        ...e,
        source: nodeMap.get(e.srcEntityPk),
        target: nodeMap.get(e.dstEntityPk),
      }));

    // Clear previous render
    const svg = select(svgEl);
    svg.selectAll("g.graph-layer > *").remove();

    const g = svg.select("g.graph-layer");

    // ── Zoom setup ──────────────────────────────────────────────────
    const z = zoom()
      .scaleExtent([0.15, 4])
      .on("zoom", (evt) => {
        g.attr("transform", evt.transform);
      });
    svg.call(z);
    if (!initRef.current) {
      initRef.current = true;
    }

    // ── Links ───────────────────────────────────────────────────────
    const linkG = g.append("g").attr("class", "links");
    const link = linkG.selectAll("line")
      .data(simLinks, d => d.pk)
      .join("line")
      .attr("stroke", "var(--line, #334155)")
      .attr("stroke-width", 1)
      .attr("stroke-opacity", 0.55);

    link.on("mouseenter", (evt, d) => setHoveredEdge(d))
        .on("mouseleave", () => setHoveredEdge(null));

    // ── Edge labels on hover ────────────────────────────────────────
    const edgeLabelG = g.append("g").attr("class", "edge-labels");

    // ── Nodes ───────────────────────────────────────────────────────
    const nodeG = g.append("g").attr("class", "nodes");

    // Click tracking for single vs double-click
    const clickTimers = new Map();

    const node = nodeG.selectAll("g.node")
      .data(simNodes, d => d.pk)
      .join("g")
      .attr("class", "node")
      .attr("cursor", "pointer")
      .on("click", (evt, d) => {
        const prev = clickTimers.get(d.pk);
        if (prev) {
          // Second click within 350ms → double-click → detail modal
          clearTimeout(prev);
          clickTimers.delete(d.pk);
          onNodeClick?.(d);
        } else {
          // First click → wait to see if double-click follows
          const timer = setTimeout(() => {
            clickTimers.delete(d.pk);
            // Single click → toggle expand/collapse
            onToggleExpand?.(d.pk);
          }, 350);
          clickTimers.set(d.pk, timer);
        }
      });

    // Build document→color map for border tinting
    const docColorMap = buildDocColorMap(nodes);

    // ── Expanded ring (gold glow for expanded nodes) ────────────────
    node.append("circle")
      .attr("class", "expand-ring")
      .attr("r", d => d.r + 5)
      .attr("fill", "none")
      .attr("stroke", d => expandedSet.has(d.pk) ? "var(--gold, #F59E0B)" : "transparent")
      .attr("stroke-width", 2)
      .attr("stroke-dasharray", d => expandedSet.has(d.pk) ? "3 3" : "none")
      .attr("opacity", d => expandedSet.has(d.pk) ? 0.7 : 0);

    // Document-colored outer ring
    node.append("circle")
      .attr("class", "doc-ring")
      .attr("r", d => d.r + 3)
      .attr("fill", "none")
      .attr("stroke", d => docColorMap.get(d.documentPk) || "transparent")
      .attr("stroke-width", 3)
      .attr("opacity", d => docColorMap.has(d.documentPk) ? 0.85 : 0);

    // Main node circle — dimmed for collapsed nodes with hidden neighbors
    node.append("circle")
      .attr("r", d => d.r)
      .attr("fill", d => kindColor(d.kind))
      .attr("stroke", d => d.pk === selectedPk ? "var(--gold, #F59E0B)" : "var(--bg1, #0F172A)")
      .attr("stroke-width", d => d.pk === selectedPk ? 3 : 2)
      .attr("opacity", d => {
        // Dim collapsed nodes that HAVE hidden neighbors
        if (!expandedSet.has(d.pk) && hiddenCounts.has(d.pk)) return 0.6;
        return 0.92;
      });

    // ── "+N" badge for collapsed nodes with hidden neighbors ───────
    const badgeG = g.append("g").attr("class", "badges");
    const badges = badgeG.selectAll("g.badge")
      .data(simNodes.filter(d => !expandedSet.has(d.pk) && hiddenCounts.has(d.pk)), d => d.pk)
      .join("g")
      .attr("class", "badge")
      .attr("pointer-events", "none");

    badges.append("circle")
      .attr("r", 10)
      .attr("fill", "var(--bg1, #0F172A)")
      .attr("stroke", "var(--ink3, #64748B)")
      .attr("stroke-width", 1);

    badges.append("text")
      .attr("text-anchor", "middle")
      .attr("dy", 3)
      .attr("fill", "var(--ink2, #94A3B8)")
      .attr("font-size", 9)
      .attr("font-weight", 700)
      .attr("font-family", "system-ui, sans-serif")
      .text(d => `+${hiddenCounts.get(d.pk)}`);

    // Labels
    node.append("text")
      .attr("dy", d => d.r + 12)
      .attr("text-anchor", "middle")
      .attr("fill", "var(--ink2, #94A3B8)")
      .attr("font-size", 9)
      .attr("font-family", "system-ui, -apple-system, sans-serif")
      .attr("pointer-events", "none")
      .text(d => {
        const t = d.canonical || d.text || "?";
        return t.length > LABEL_MAX ? t.slice(0, LABEL_MAX) + "…" : t;
      });

    // ── Drag ────────────────────────────────────────────────────────
    const drg = drag()
      .on("start", (evt, d) => {
        if (!evt.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (evt, d) => {
        d.fx = evt.x;
        d.fy = evt.y;
      })
      .on("end", (evt, d) => {
        if (!evt.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
    node.call(drg);

    // ── Simulation ──────────────────────────────────────────────────
    if (simRef.current) simRef.current.stop();
    const sim = forceSimulation(simNodes)
      .force("link", forceLink(simLinks).id(d => d.pk).distance(LINK_DISTANCE))
      .force("charge", forceManyBody().strength(CHARGE))
      .force("center", forceCenter(cx, cy))
      .force("collide", forceCollide(COLLIDE_R))
      .alphaDecay(0.022);

    sim.on("tick", () => {
      link
        .attr("x1", d => d.source.x)
        .attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x)
        .attr("y2", d => d.target.y);

      if (hoveredEdge) {
        const mx = (hoveredEdge.source.x + hoveredEdge.target.x) / 2;
        const my = (hoveredEdge.source.y + hoveredEdge.target.y) / 2;
        edgeLabelG.selectAll("text.edge-hover-label")
          .data([hoveredEdge])
          .join("text")
          .attr("class", "edge-hover-label")
          .attr("x", mx)
          .attr("y", my - 6)
          .attr("text-anchor", "middle")
          .attr("fill", "var(--ink, #F8FAFC)")
          .attr("font-size", 9)
          .attr("font-family", "system-ui, sans-serif")
          .attr("pointer-events", "none")
          .text(d => d.relation || "related");
      } else {
        edgeLabelG.selectAll("text.edge-hover-label").remove();
      }

      // Position badges near parent node (top-right)
      badges.attr("transform", d => `translate(${d.x + d.r + 8},${d.y - d.r - 6})`);

      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });

    simRef.current = sim;

    return () => {
      sim.stop();
      simRef.current = null;
    };
  }, [nodes, edges, dimensions, selectedPk, onNodeClick, hoveredEdge, expandedPks,
      onToggleExpand, hiddenCounts, expandedSet]);

  // ── Render ───────────────────────────────────────────────────────────
  if (!nodes.length) {
    return (
      <div ref={containerRef} style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div className="ink3" style={{ fontSize: 12, fontStyle: "italic" }}>No entities to graph.</div>
      </div>
    );
  }

  if (nodes.length === 1) {
    const n = nodes[0];
    const color = kindColor(n.kind);
    return (
      <div ref={containerRef} style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
        <div style={{ width: 44, height: 44, borderRadius: "50%", background: color, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, color: "#fff", fontWeight: 700 }}>
          {(n.canonical || n.text || "?")[0]?.toUpperCase()}
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>{n.canonical || n.text}</div>
        <div style={{ fontSize: 10, color, textTransform: "uppercase", letterSpacing: ".04em" }}>{kindLabel(n.kind)}</div>
        <div className="ink3" style={{ fontSize: 11, fontStyle: "italic", marginTop: 4 }}>Click to expand · double-click for detail</div>
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%", overflow: "hidden", position: "relative", background: "var(--bg1, #0F172A)" }}>
      <svg ref={svgRef} width="100%" height="100%" style={{ display: "block" }}>
        <g className="graph-layer" />
      </svg>
      {/* Legend — bottom-left overlay */}
      <div style={{
        position: "absolute", bottom: 8, left: 8,
        display: "flex", flexWrap: "wrap", gap: "3px 8px",
        padding: "5px 8px", borderRadius: 6,
        background: "color-mix(in srgb, var(--bg2, #1E293B) 90%, transparent)",
        backdropFilter: "blur(4px)",
        fontSize: 9, color: "var(--ink3, #64748B)",
        pointerEvents: "none",
      }}>
        {(() => {
          const kinds = new Set(nodes.map(n => n.kind));
          return [...kinds].sort().map(k => (
            <span key={k} style={{ display: "flex", alignItems: "center", gap: 3 }}>
              <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: kindColor(k) }} />
              {kindLabel(k)}
            </span>
          ));
        })()}
        {(() => {
          const docMap = buildDocColorMap(nodes);
          if (!docMap.size) return null;
          return [...docMap.entries()].map(([dpk, color], i) => (
            <span key={`doc-${dpk}`} style={{ display: "flex", alignItems: "center", gap: 3, marginLeft: i === 0 ? 6 : 0 }}>
              <span style={{
                display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                border: `2px solid ${color}`, background: "transparent",
              }} />
              <span style={{ opacity: 0.8 }}>Doc {dpk}</span>
            </span>
          ));
        })()}
        <span style={{ marginLeft: 4, opacity: 0.7 }}>{nodes.length}n · {edges.length}e</span>
      </div>
    </div>
  );
}
