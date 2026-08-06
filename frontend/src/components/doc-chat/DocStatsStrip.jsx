// DocStatsStrip — per-document quality capsules above the document viewer.
// Each capsule: hover = tooltip explanation, click = expand details below.
// Accepts `controls` for inline chat/zoom/review buttons.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../Icon.jsx";
import { useQuality } from "./AdvancedSidebar.jsx";

// ── Helpers ──────────────────────────────────────────────────────────

function accentFor(score) {
  return score >= 0.8 ? "#3FA47A" : score >= 0.5 ? "#E0A23B" : "#D8625E";
}

function Bar({ pct, color }) {
  return (
    <div style={{ height: 4, borderRadius: 2, background: "var(--bg1)", overflow: "hidden", flex: 1, minWidth: 40 }}>
      <div style={{ height: "100%", width: Math.round(pct * 100) + "%", background: color, borderRadius: 2, transition: "width .3s" }} />
    </div>
  );
}

// ── Mid-sized stat capsule ───────────────────────────────────────────

function Capsule({ icon, label, value, accent, tip, active, onClick, children }) {
  const c = accent || "var(--ink3)";
  const ref = useRef(null);
  return (
    <div ref={ref} onClick={onClick}
      onMouseEnter={(e) => { if (!tip) return; const r = e.currentTarget.getBoundingClientRect(); showPortalTip(tip, r); }}
      onMouseLeave={hidePortalTip}
      style={{
        display: "flex", flexDirection: "column", flexShrink: 0, gap: 1,
        padding: "5px 11px 5px 14px", borderRadius: 10, position: "relative", overflow: "hidden",
        background: active ? `color-mix(in srgb, ${c} 15%, var(--bg1))` : `linear-gradient(180deg, color-mix(in srgb, ${c} 8%, var(--bg1)), var(--bg1) 70%)`,
        border: `1px solid ${active ? c : `color-mix(in srgb, ${c} 18%, var(--line))`}`,
        cursor: onClick ? "pointer" : "default", userSelect: "none", minWidth: 50,
        transition: "background .15s, border-color .15s",
      }}>
      <span aria-hidden style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: active ? 4 : 3, background: c, transition: "width .15s" }} />
      {children ? children : (
        <>
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {icon && <Icon name={icon} size={13} style={{ color: c, flexShrink: 0 }} />}
            {label && <span style={{ fontSize: 9, fontWeight: 600, color: "var(--ink3)", textTransform: "uppercase", letterSpacing: ".05em", whiteSpace: "nowrap" }}>{label}</span>}
          </span>
          {value != null && <span style={{ fontSize: 16, fontWeight: 700, color: c, lineHeight: 1.15, whiteSpace: "nowrap", fontFamily: "var(--serif)" }}>{value}</span>}
        </>
      )}
    </div>
  );
}

// ── Portal tooltip — escapes overflow clipping ──────────────────────

let _tipEl = null;
let _hideTimer = null;

function ensureTipRoot() {
  if (!_tipEl || !document.body.contains(_tipEl)) {
    _tipEl = document.createElement("div");
    _tipEl.id = "ds-tip-root";
    _tipEl.style.cssText = "position:fixed;z-index:99999;pointer-events:none;";
    document.body.appendChild(_tipEl);
  }
  return _tipEl;
}

function showPortalTip(text, rect) {
  ensureTipRoot();
  clearTimeout(_hideTimer);
  const x = rect.left + rect.width / 2;
  const y = rect.top - 8;
  const el = _tipEl;
  el.innerHTML = "";
  const inner = document.createElement("div");
  inner.textContent = text;
  inner.style.cssText = [
    "position:fixed", `left:${x}px`, `top:${y}px`,
    "transform:translate(-50%,-100%)",
    "background:var(--bg1, #1a1a1a)", "color:var(--ink, #eee)",
    "border:1px solid var(--gold2, #E0A23B)", "border-radius:8px",
    "padding:7px 11px", "font-size:11.5px", "line-height:1.6",
    "white-space:pre", "max-width:320px",
    "box-shadow:0 6px 24px rgba(0,0,0,0.5)",
  ].join(";");
  el.appendChild(inner);
}

function hidePortalTip() {
  _hideTimer = setTimeout(() => {
    if (_tipEl) { _tipEl.innerHTML = ""; }
  }, 80);
}

// ── Detail panel ─────────────────────────────────────────────────────

function DetailPanel({ title, onClose, children }) {
  return (
    <div style={{ padding: "8px 12px 12px", background: "var(--bg1)", borderTop: "1px solid var(--line)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".06em", color: "var(--ink)", textTransform: "uppercase" }}>{title}</span>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 14, color: "var(--ink3)", padding: 0, lineHeight: 1 }}>✕</button>
      </div>
      {children}
    </div>
  );
}

function FieldRow({ name, score, risk }) {
  const c = accentFor(score || 0);
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--line)" }}>
      <span style={{ fontSize: 10.5, color: "var(--ink)", fontWeight: 500, textTransform: "capitalize" }}>{name.replace(/_/g, " ")}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Bar pct={score || 0} color={c} />
        <span style={{ fontSize: 10, fontWeight: 600, color: c, minWidth: 28, textAlign: "right" }}>{Math.round((score || 0) * 100)}%</span>
        {risk && (
          <span style={{
            fontSize: 9, fontWeight: 600, padding: "1px 5px", borderRadius: 3,
            background: risk === "high" ? "rgba(216,98,94,0.15)" : risk === "medium" ? "rgba(224,162,59,0.15)" : "rgba(63,164,122,0.15)",
            color: risk === "high" ? "#D8625E" : risk === "medium" ? "#E0A23B" : "#3FA47A",
          }}>{risk.toUpperCase()}</span>
        )}
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

export default function DocStatsStrip({ doc, onReview, controls }) {
  const quality = useQuality(doc?.id);
  const [review, setReview] = useState(null);
  const [expanded, setExpanded] = useState(null); // 'confidence' | 'fields' | 'anomalies' | 'chunks' | 'language'

  useEffect(() => {
    if (!doc?.id) return;
    let c = false;
    fetch("/api/documents/" + encodeURIComponent(doc.id) + "/review")
      .then(r => r.json()).then(d => { if (!c) setReview(d); }).catch(() => {});
    return () => { c = true; };
  }, [doc?.id]);

  // ── Compute values ───────────────────────────────────────────────

  const ef = doc?.extractedFields || {};
  const fields = ef.fields || {};
  const fc = ef.field_confidence || {};
  const isEmpty = (v) => v === "" || v == null || (Array.isArray(v) && v.length === 0)
    || (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);
  const scalarKeys = Object.keys(fields).filter(k => !k.startsWith("_"));
  const filled = scalarKeys.filter(k => !isEmpty(fields[k]));
  const missing = scalarKeys.length - filled.length;
  const lowConfFields = Object.entries(fc)
    .filter(([k, v]) => !k.startsWith("_") && typeof v === "number" && v < 0.6)
    .sort((a, b) => a[1] - b[1]);
  const lowConf = lowConfFields.length;
  const needs = missing + lowConf;

  const conf = ef.confidence != null ? Math.round(ef.confidence * 100)
    : (doc.docTypeConfidence != null ? Math.round(doc.docTypeConfidence * 100) : null);

  const anomalies = review?.anomalies || [];
  const qc = quality?.chunks;
  const qe = quality?.embedding;
  const ql = quality?.language;
  const topLang = Object.entries(ql?.detected || {})[0];
  const langEntries = Object.entries(ql?.detected || {}).sort((a, b) => b[1] - a[1]);

  const qScores = review?.field_scores || {};
  const worstFields = Object.entries(qScores)
    .filter(([k]) => !k.startsWith("_"))
    .sort((a, b) => (a[1].confidence || 0) - (b[1].confidence || 0))
    .slice(0, 5);

  const toggle = (key) => setExpanded(e => e === key ? null : key);
  const close = () => setExpanded(null);

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div style={{ flex: "0 0 auto", borderBottom: "1px solid var(--line)" }}>
      {/* Capsule row */}
      <div className="doc-stats-bar" style={{
        display: "flex", alignItems: "center", gap: 7,
        padding: "6px 10px", background: "var(--bg1)",
      }}>
        {/* Confidence */}
        <Capsule icon="check" label="confidence" value={conf != null ? conf + "%" : "—"}
          accent={accentFor((conf || 0) / 100)} active={expanded === "confidence"}
          tip={"Extraction confidence\nHow reliable the AI-extracted fields are overall.\nClick for breakdown."}
          onClick={() => toggle("confidence")} />

        {/* Fields */}
        <Capsule icon="file" label="fields" value={filled.length + "/" + scalarKeys.length}
          accent={needs > 0 ? "#E0A23B" : "#3FA47A"} active={expanded === "fields"}
          tip={"Populated fields\n" + filled.length + " filled · " + missing + " missing · " + lowConf + " low confidence\nClick to see details."}
          onClick={() => toggle("fields")} />

        {/* To review */}
        {needs > 0 && (
          <Capsule icon="alert" label="to review" value={needs}
            accent="#E0A23B" active={expanded === "review"}
            tip={"Fields needing review\n" + missing + " empty · " + lowConf + " low confidence\nClick to see which."}
            onClick={() => toggle("review")} />
        )}

        {/* Anomalies */}
        <Capsule icon="alert" label="anomalies" value={anomalies.length}
          accent={anomalies.length > 0 ? "#D8625E" : "#3FA47A"} active={expanded === "anomalies"}
          tip={"Extraction anomalies\n" + anomalies.filter(a => a.severity === "high").length + " high · " + anomalies.filter(a => a.severity === "medium").length + " med · " + anomalies.filter(a => a.severity === "low").length + " low\nClick for details."}
          onClick={() => toggle("anomalies")} />

        {/* Chunks */}
        <Capsule icon="layers" label="chunks" value={qc?.total ?? "—"}
          accent="#6A93C8" active={expanded === "chunks"}
          tip={"Searchable text chunks\nAvg " + (qc?.avg_len || "?") + " chars · pipeline v" + (qc?.pipeline_version || 1) + "\nClick for indexing details."}
          onClick={() => toggle("chunks")} />

        {/* Language */}
        {topLang && (
          <Capsule icon="flag" label="language" value={topLang[0]}
            accent="#8B7FD6" active={expanded === "language"}
            tip={"Primary language: " + topLang[0] + " (" + Math.round(topLang[1] * 100) + "%)\n" + (ql?.mixed ? "⚠️ Mixed-language document\n" : "") + "Click for full breakdown."}
            onClick={() => toggle("language")} />
        )}

        {/* Separator + controls */}
        {controls && <div style={{ width: 1, height: 24, background: "var(--line)", margin: "0 2px", flexShrink: 0 }} />}
        {controls}
      </div>

      {/* ── Expanded detail panels ─────────────────────────────────── */}

      {expanded === "confidence" && (
        <DetailPanel title="🎯 Extraction Confidence" onClose={close}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span style={{ fontSize: 28, fontWeight: 700, color: accentFor((conf || 0) / 100), fontFamily: "var(--serif)" }}>{conf != null ? conf + "%" : "—"}</span>
            <div style={{ flex: 1 }}>
              <Bar pct={(conf || 0) / 100} color={accentFor((conf || 0) / 100)} />
              <div style={{ fontSize: 10, color: "var(--ink3)", marginTop: 3 }}>
                {conf >= 80 ? "High confidence — fields are reliable." : conf >= 50 ? "Medium confidence — some fields may need review." : "Low confidence — review recommended."}
              </div>
            </div>
          </div>
          {review && (
            <div style={{ fontSize: 10.5, color: "var(--ink2)", lineHeight: 1.6 }}>
              <strong>Quality:</strong> {review.quality_level?.toUpperCase()} ({Math.round(review.overall_quality * 100)}%)
              {review.quality_notes && <div style={{ color: "var(--ink3)", marginTop: 2 }}>{review.quality_notes}</div>}
            </div>
          )}
          <div style={{ marginTop: 8, display: "flex", gap: 8, fontSize: 10, color: "var(--ink3)" }}>
            <span>🟢 High: {Object.values(fc).filter(v => typeof v === "number" && v >= 0.8).length}</span>
            <span>🟡 Med: {Object.values(fc).filter(v => typeof v === "number" && v >= 0.5 && v < 0.8).length}</span>
            <span>🔴 Low: {Object.values(fc).filter(v => typeof v === "number" && v < 0.5).length}</span>
          </div>
        </DetailPanel>
      )}

      {expanded === "fields" && (
        <DetailPanel title="📋 Field Summary" onClose={close}>
          <div style={{ display: "flex", gap: 12, marginBottom: 10, fontSize: 11, color: "var(--ink2)" }}>
            <span>✅ {filled.length} filled</span>
            <span>⬜ {missing} missing</span>
            <span>⚠️ {lowConf} low confidence</span>
          </div>
          {missing > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--ink3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>Missing fields</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {scalarKeys.filter(k => isEmpty(fields[k])).slice(0, 8).map(k => (
                  <span key={k} style={{ padding: "2px 7px", borderRadius: 999, fontSize: 10, background: "var(--bg2)", color: "var(--ink2)", border: "1px solid var(--line)" }}>{k.replace(/_/g, " ")}</span>
                ))}
                {missing > 8 && <span style={{ fontSize: 10, color: "var(--ink3)" }}>+{missing - 8} more</span>}
              </div>
            </div>
          )}
          {lowConf > 0 && (
            <div>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--ink3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>Low confidence fields</div>
              {lowConfFields.slice(0, 5).map(([k, s]) => <FieldRow key={k} name={k} score={s} />)}
            </div>
          )}
          <button onClick={() => { close(); onReview?.(); }}
            style={{ marginTop: 10, background: "none", border: "none", cursor: "pointer", fontSize: 10.5, color: "var(--gold2)", padding: 0, fontWeight: 600 }}>
            View all in Fields tab →
          </button>
        </DetailPanel>
      )}

      {expanded === "review" && (
        <DetailPanel title="⚠️ Fields Needing Review" onClose={close}>
          {missing > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--ink3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>Empty ({missing})</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {scalarKeys.filter(k => isEmpty(fields[k])).map(k => (
                  <span key={k} style={{ padding: "2px 7px", borderRadius: 999, fontSize: 10, background: "var(--bg2)", color: "var(--ink2)", border: "1px solid var(--line)" }}>{k.replace(/_/g, " ")}</span>
                ))}
              </div>
            </div>
          )}
          {lowConf > 0 && (
            <div>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--ink3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>Low confidence ({lowConf})</div>
              {lowConfFields.map(([k, s]) => <FieldRow key={k} name={k} score={s} />)}
            </div>
          )}
          <button onClick={() => { close(); onReview?.(); }}
            style={{ marginTop: 10, background: "none", border: "none", cursor: "pointer", fontSize: 10.5, color: "var(--gold2)", padding: 0, fontWeight: 600 }}>
            Open Fields tab to edit →
          </button>
        </DetailPanel>
      )}

      {expanded === "anomalies" && (
        <DetailPanel title={"⚠️ " + anomalies.length + " Anomal" + (anomalies.length === 1 ? "y" : "ies")} onClose={close}>
          {anomalies.map((a, i) => (
            <div key={i} style={{
              padding: "5px 8px", borderRadius: 4, fontSize: 10.5, lineHeight: 1.4, marginBottom: 4,
              background: { high: "rgba(239,68,68,0.12)", medium: "rgba(245,158,11,0.12)", low: "rgba(16,185,129,0.12)" }[a.severity] || "rgba(245,158,11,0.12)",
              borderLeft: "3px solid " + ({ high: "#ef4444", medium: "#f59e0b", low: "#10b981" }[a.severity] || "#f59e0b"),
            }}>
              <span style={{ fontWeight: 600, color: "var(--ink)" }}>{a.type}</span>
              <span style={{ color: "var(--ink2)" }}> · {a.message}</span>
              {a.suggestion && <div style={{ color: "var(--ink3)", marginTop: 1 }}>💡 {a.suggestion}</div>}
            </div>
          ))}
          {worstFields.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--ink3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>Lowest Confidence Fields</div>
              {worstFields.map(([fname, fq]) => <FieldRow key={fname} name={fname} score={fq.confidence} risk={fq.risk_level} />)}
            </div>
          )}
        </DetailPanel>
      )}

      {expanded === "chunks" && (
        <DetailPanel title="🧩 Indexing Details" onClose={close}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 16px", fontSize: 11 }}>
            <div style={{ color: "var(--ink3)" }}>Total chunks</div><div style={{ color: "var(--ink)", fontWeight: 600 }}>{qc?.total ?? "—"}</div>
            <div style={{ color: "var(--ink3)" }}>Avg length</div><div style={{ color: "var(--ink)", fontWeight: 600 }}>{qc?.avg_len ?? "—"} chars</div>
            <div style={{ color: "var(--ink3)" }}>Min / Max</div><div style={{ color: "var(--ink)", fontWeight: 600 }}>{qc?.min_len ?? "—"} / {qc?.max_len ?? "—"}</div>
            <div style={{ color: "var(--ink3)" }}>Pipeline</div><div style={{ color: "var(--ink)", fontWeight: 600 }}>v{qc?.pipeline_version || 1}</div>
            <div style={{ color: "var(--ink3)" }}>Embedded</div>
            <div style={{ color: qc?.v2_embedded === qc?.total ? "#3FA47A" : "#E0A23B", fontWeight: 600 }}>
              {qc?.v2_embedded ?? "?"}/{qc?.total ?? "?"}
            </div>
            <div style={{ color: "var(--ink3)" }}>Model</div><div style={{ color: "var(--ink)", fontWeight: 600 }}>{qe?.v2_model || "BGE-M3"} {qe?.v2_dim || 1024}d</div>
          </div>
        </DetailPanel>
      )}

      {expanded === "language" && (
        <DetailPanel title="🌐 Language Detection" onClose={close}>
          {ql?.mixed && (
            <div style={{ fontSize: 10.5, color: "#E0A23B", marginBottom: 8, padding: "4px 8px", borderRadius: 4, background: "rgba(224,162,59,0.1)", border: "1px solid rgba(224,162,59,0.2)" }}>
              ⚠️ Mixed-language document — may affect extraction quality.
            </div>
          )}
          {langEntries.map(([lang, pct]) => (
            <div key={lang} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--ink)", minWidth: 50, textTransform: "capitalize" }}>{lang}</span>
              <Bar pct={pct} color="#8B7FD6" />
              <span style={{ fontSize: 10, fontWeight: 600, color: "#8B7FD6", minWidth: 32, textAlign: "right" }}>{Math.round(pct * 100)}%</span>
            </div>
          ))}
        </DetailPanel>
      )}
    </div>
  );
}
