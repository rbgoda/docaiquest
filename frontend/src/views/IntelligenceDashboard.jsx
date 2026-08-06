// Intelligence Dashboard · Phase A — proactive attention layer.
//
// "Intelligence apps for your data": instead of waiting for a chat question,
// surface what needs attention across the user's library — expiries, overdue
// payments, low-confidence classifications, failed ingests — computed server-
// side with zero LLM (app/intelligence/alerts.py). View-engine + AI-proposed
// views land in Phases B/C. See docs/architecture/INTELLIGENCE_DASHBOARD.md.
import React, { useState } from "react";
import Icon from "../components/Icon.jsx";
import { LoadingState, ErrorState } from "../components/Shell.jsx";
import { useApiResource } from "../api/useApi.js";
import { fetchIntelligenceOverview, fetchIntelligenceViews,
         proposeIntelligenceViews, updateIntelligenceView } from "../api/documents";

const SEV = {
  high:   { color: "#D8625E", bg: "rgba(216,98,94,0.10)", label: "Needs action", icon: "alert" },
  warn:   { color: "#E0A23B", bg: "rgba(224,162,59,0.10)", label: "Coming up",    icon: "clock" },
  review: { color: "#8B7FD6", bg: "rgba(139,127,214,0.10)", label: "To review",   icon: "eye" },
};

function Kpi({ label, value, sub, accent = "var(--gold2)" }) {
  return (
    <div className="bg1 border rounded-xl" style={{ flex: "1 1 160px", minWidth: 160, padding: "16px 18px" }}>
      <div className="upper ink3" style={{ fontSize: 10, letterSpacing: "0.12em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 26, fontWeight: 600, color: accent, marginTop: 6, lineHeight: 1 }}>{value}</div>
      {sub && <div className="ink3" style={{ fontSize: 11, marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

function AlertRow({ a, onOpen }) {
  const s = SEV[a.severity] || SEV.review;
  return (
    <button onClick={() => onOpen?.(a.documentId)}
      className="row hover-bg w-full"
      style={{ justifyContent: "space-between", alignItems: "center", textAlign: "left",
               padding: "11px 12px", background: "none", border: "none", cursor: "pointer",
               borderTop: "1px solid var(--line)" }}>
      <div className="row gap-3" style={{ alignItems: "center", minWidth: 0 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: s.color, flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span style={{ fontSize: 13, color: "var(--ink)", fontWeight: 600 }}>{a.title}</span>
            <span className="truncate ink2" style={{ fontSize: 12 }}>· {a.documentName}</span>
          </div>
          <div className="ink3" style={{ fontSize: 11.5, marginTop: 2 }}>{a.detail}</div>
        </div>
      </div>
      <div className="row gap-2" style={{ alignItems: "center", flexShrink: 0 }}>
        {a.docType && <span className="upper mono" style={{ fontSize: 8, padding: "1px 5px", borderRadius: 3, background: "var(--bg3)", color: "var(--ink3)" }}>{a.docType}</span>}
        <Icon name="chevronr" size={14} />
      </div>
    </button>
  );
}

function flagColor(flag) {
  return flag === "past" ? "#D8625E" : flag === "soon" ? "#E0A23B" : null;
}

function metricColor(label, value) {
  const l = label.toLowerCase();
  if (l.includes("overdue") || l.includes("expired")) return value > 0 ? "#D8625E" : "var(--ink3)";
  if (l.includes("expiring")) return value > 0 ? "#E0A23B" : "var(--ink3)";
  return "var(--gold2)";
}

function ViewCard({ v, onOpen, onDismiss }) {
  const isAI = v.source === "ai";
  return (
    <div className="bg1 border rounded-xl" style={{ overflow: "hidden" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start",
           padding: "14px 16px", borderBottom: "1px solid var(--line)", gap: 14 }}>
        <div style={{ minWidth: 0 }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span style={{ fontSize: 16 }}>{v.icon}</span>
            <span className="serif" style={{ fontSize: 16 }}>{v.title}</span>
            <span className="mono ink3" style={{ fontSize: 11 }}>· {v.matchedCount}</span>
            {isAI && (
              <span className="upper mono" style={{ fontSize: 8, padding: "2px 6px", borderRadius: 4,
                    background: "rgba(139,127,214,0.15)", color: "#8B7FD6", letterSpacing: "0.08em" }}>✨ AI</span>
            )}
          </div>
          {v.subtitle && <div className="ink3" style={{ fontSize: 12, marginTop: 2 }}>{v.subtitle}</div>}
        </div>
        <div className="row" style={{ gap: 16, flexShrink: 0, alignItems: "flex-start" }}>
          {v.metrics.map((m) => (
            <div key={m.label} style={{ textAlign: "right" }}>
              <div className="mono" style={{ fontSize: 18, fontWeight: 600, lineHeight: 1, color: metricColor(m.label, m.value) }}>{m.value}</div>
              <div className="upper ink3" style={{ fontSize: 9, letterSpacing: "0.1em", marginTop: 4 }}>{m.label}</div>
            </div>
          ))}
          {isAI && onDismiss && (
            <button onClick={() => onDismiss(v)} title="Dismiss this view"
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink3)",
                       fontSize: 16, lineHeight: 1, padding: "0 2px" }}>×</button>
          )}
        </div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr>
              {v.columns.map((c) => (
                <th key={c} style={{ textAlign: "left", padding: "8px 14px", color: "var(--ink3)",
                     fontWeight: 600, fontSize: 11, whiteSpace: "nowrap" }}>{c}</th>
              ))}
              <th style={{ width: 28 }} />
            </tr>
          </thead>
          <tbody>
            {v.rows.map((r, i) => (
              <tr key={`${r.documentId}-${i}`} onClick={() => onOpen?.(r.documentId)} className="hover-bg" style={{ cursor: "pointer" }}>
                {r.cells.map((cell, ci) => (
                  <td key={ci} style={{ padding: "9px 14px", borderTop: "1px solid var(--line)",
                       color: ci === 0 ? "var(--ink)" : "var(--ink2)", whiteSpace: "nowrap" }}>
                    <span className="row gap-2" style={{ alignItems: "center" }}>
                      {ci === 0 && flagColor(r.flag) && (
                        <span style={{ width: 6, height: 6, borderRadius: "50%", background: flagColor(r.flag), flexShrink: 0 }} />
                      )}
                      {cell != null && cell !== "" ? cell : <span className="ink4">—</span>}
                    </span>
                  </td>
                ))}
                <td style={{ padding: "9px 10px", borderTop: "1px solid var(--line)", textAlign: "right" }}>
                  <Icon name="chevronr" size={13} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Group({ severity, alerts, onOpen }) {
  if (!alerts.length) return null;
  const s = SEV[severity];
  return (
    <div className="bg1 border rounded-xl" style={{ overflow: "hidden" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center",
           padding: "12px 14px", background: s.bg }}>
        <div className="row gap-2" style={{ alignItems: "center" }}>
          <Icon name={s.icon} size={15} />
          <span style={{ fontSize: 13, fontWeight: 600, color: s.color }}>{s.label}</span>
        </div>
        <span className="mono" style={{ fontSize: 12, color: s.color }}>{alerts.length}</span>
      </div>
      <div>{alerts.map((a, i) => <AlertRow key={`${a.documentId}-${a.type}-${i}`} a={a} onOpen={onOpen} />)}</div>
    </div>
  );
}

export default function IntelligenceDashboard({ onOpenDocument }) {
  const { data, loading, error } = useApiResource(fetchIntelligenceOverview);
  const { data: viewsData, setData: setViewsData } = useApiResource(fetchIntelligenceViews);
  const [proposing, setProposing] = useState(false);
  const [proposeNote, setProposeNote] = useState(null);

  const suggest = async () => {
    setProposing(true); setProposeNote(null);
    try {
      const res = await proposeIntelligenceViews();
      setViewsData({ views: res.views || [] });
      const reason = res.reason || "";
      if (res.created > 0) {
        setProposeNote(`Added ${res.created} view${res.created === 1 ? "" : "s"}.`);
      } else if (reason === "llm_unavailable") {
        setProposeNote("AI is temporarily unavailable — please try again in a moment.");
      } else if (reason.includes("no ready") || reason === "no_documents") {
        setProposeNote("Add and process some documents first, then I can suggest views.");
      } else if (reason === "no_views") {
        setProposeNote("The AI couldn't generate views right now — please try again.");
      } else if ((res.views || []).length > 0) {
        setProposeNote("Your views are already up to date.");
      } else {
        setProposeNote("No new views to suggest right now.");
      }
    } catch (e) {
      setProposeNote("Couldn't generate views — try again.");
    } finally {
      setProposing(false);
    }
  };
  const dismissView = async (v) => {
    setViewsData((d) => ({ views: (d?.views || []).filter((x) => x.id !== v.id) }));
    try { await updateIntelligenceView(v.id, { dismissed: true }); } catch { /* ignore */ }
  };

  if (loading) return <LoadingState label="Reading your documents…" />;
  if (error) return <ErrorState message={error.message || "Could not load intelligence"} />;
  const views = viewsData?.views || [];

  const p = data?.portfolio || {};
  const alerts = data?.alerts || [];
  const counts = data?.alertCounts || {};
  const bySev = { high: [], warn: [], review: [] };
  for (const a of alerts) (bySev[a.severity] || bySev.review).push(a);

  // Drill-down: open the specific document the alert is about.
  const openDoc = (docId) => onOpenDocument?.(docId);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* header */}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h1 className="serif" style={{ fontSize: 24, lineHeight: 1.1 }}>Intelligence</h1>
          <p className="ink2" style={{ fontSize: 13, marginTop: 4 }}>
            What needs your attention across {p.totalDocs ?? 0} document{(p.totalDocs ?? 0) === 1 ? "" : "s"}.
          </p>
        </div>
        <button onClick={() => onOpenDocument?.(null)} className="border bg2 hover-bg row gap-2"
                style={{ padding: "8px 12px", borderRadius: 8, fontSize: 13, alignItems: "center" }}>
          <Icon name="file" size={14} /> All documents
        </button>
      </div>

      {/* portfolio KPIs */}
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <Kpi label="Documents" value={p.totalDocs ?? 0} sub={`${p.readyDocs ?? 0} ready`} />
        <Kpi label="Types" value={p.typeCount ?? 0} sub="document kinds" accent="#8B7FD6" />
        <Kpi label="Need attention" value={p.needsAttention ?? 0} sub="across your library"
             accent={(p.needsAttention ?? 0) > 0 ? "#D8625E" : "#3FA47A"} />
        <Kpi label="Action now" value={counts.high ?? 0} sub="overdue / expired / failed" accent="#D8625E" />
      </div>

      {/* alert groups */}
      {alerts.length === 0 ? (
        <div className="bg1 border rounded-xl" style={{ padding: "40px 20px", textAlign: "center" }}>
          <Icon name="check" size={22} />
          <div className="serif" style={{ fontSize: 18, marginTop: 10 }}>All clear</div>
          <p className="ink3" style={{ fontSize: 13, marginTop: 6 }}>
            Nothing needs attention right now. New alerts appear as documents are added or deadlines approach.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Group severity="high" alerts={bySev.high} onOpen={openDoc} />
          <Group severity="warn" alerts={bySev.warn} onOpen={openDoc} />
          <Group severity="review" alerts={bySev.review} onOpen={openDoc} />
        </div>
      )}

      {/* views — assembled tables of extracted fields, every row → its source doc */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
          <div className="upper ink3" style={{ fontSize: 11, letterSpacing: "0.12em" }}>Views</div>
          <div className="row gap-3" style={{ alignItems: "center" }}>
            {proposeNote && <span className="ink3" style={{ fontSize: 11 }}>{proposeNote}</span>}
            <button onClick={suggest} disabled={proposing}
              className="border bg2 hover-bg row gap-2"
              style={{ padding: "6px 12px", borderRadius: 8, fontSize: 12.5, alignItems: "center",
                       color: "var(--gold2)", opacity: proposing ? 0.6 : 1 }}>
              <Icon name="sparkle" size={13} /> {proposing ? "Thinking…" : "Suggest views with AI"}
            </button>
          </div>
        </div>
        {views.length > 0
          ? views.map((v) => <ViewCard key={v.id} v={v} onOpen={openDoc} onDismiss={dismissView} />)
          : <div className="ink3" style={{ fontSize: 12.5, fontStyle: "italic" }}>No views yet — add documents, or let AI suggest some.</div>}
      </div>

      <div className="ink4" style={{ fontSize: 11, fontStyle: "italic" }}>
        Computed privately from your documents — no AI, nothing leaves your workspace.
      </div>
    </div>
  );
}
