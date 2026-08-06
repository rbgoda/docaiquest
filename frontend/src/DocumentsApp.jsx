// M46 · Documents System — standalone shell for the `documents` product.
//
// Reuses the existing document surface read-only: <AllDocuments/> already does
// upload + list + per-document chat (DocumentChatPanel: chat / markdown / json
// / PII) AND "Chat across all documents" (WorkspaceChat). So this shell is just
// the auth gate + a slim header + AllDocuments — no audit/framework chrome.
//
// Deliberately a SEPARATE entry from App.jsx (selected in main.jsx by the
// product flag) so the audit app's App.jsx/Sidebar are never touched — keeping
// Frameworks/Auditing work collision-free.
import React, { useEffect, useState } from "react";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AuthProvider, useAuth } from "./auth/AuthContext.jsx";
import { DialogHost } from "./components/ConfirmDialog.jsx";
import { LoadingState, Logo } from "./components/Shell.jsx";
import DocumentsAuth from "./views/DocumentsAuth.jsx";
import DocumentsLanding from "./views/DocumentsLanding.jsx";
import DocumentsDashboard from "./views/DocumentsDashboard.jsx";
import DashboardView from "./views/DashboardView.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import AllDocuments from "./views/AllDocuments.jsx";
import DocumentsConnectors from "./views/DocumentsConnectors.jsx";
import DocumentsGroups from "./views/DocumentsGroups.jsx";
import DeveloperKeys from "./views/DeveloperKeys.jsx";
import DocumentsUserMenu from "./components/DocumentsUserMenu.jsx";
import DocumentChatPanel from "./views/DocumentChatPanel.jsx";
import { FeedbackDialog } from "./components/FeedbackFab.jsx";
import RestorePrompt from "./components/RestorePrompt.jsx";
import VerifyBanner from "./components/VerifyBanner.jsx";
import { useIsMobile } from "./useIsMobile.js";
import Icon from "./components/Icon.jsx";
import { fetchDocument } from "./api/index.js";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function NavTab({ active, onClick, children }) {
  return (
    <button onClick={onClick}
      style={{
        background: "none", border: "none", cursor: "pointer", padding: "6px 2px",
        fontSize: 13, color: active ? "var(--ink)" : "var(--ink3)",
        borderBottom: active ? "2px solid var(--gold2)" : "2px solid transparent",
        fontWeight: active ? 600 : 400,
      }}>
      {children}
    </button>
  );
}

// M47 · trial countdown / free-usage / Pro badge in the header.
// P2 · in OSS mode shows a simple "DocAIQ OSS" badge (no upgrade path).
function PlanBadge({ sub, isCloud }) {
  if (!sub && !isCloud) return null;
  // OSS deployment — simple badge, no plan/upgrade chrome
  if (!isCloud) {
    return (
      <span className="mono" style={{
        fontSize: 11, color: "var(--green)", border: "1px solid var(--green)",
        borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap",
      }}>DocAIQuest OSS</span>
    );
  }
  if (!sub) return null;
  const eff = sub.effectivePlan;
  const upgrade = () => { window.location.href = "https://github.com/rbgoda/docaiquest"; };
  let label, tone = "var(--ink3)", showUpgrade = false;
  if (eff === "trial") {
    const d = sub.trialDaysLeft ?? 0;
    label = `Trial · ${d} day${d === 1 ? "" : "s"} left`;
    tone = "var(--gold2)"; showUpgrade = true;
  } else if (eff === "free") {
    const u = sub.usage?.docs ?? 0, cap = sub.limits?.docs ?? 25;
    label = `Free · ${u}/${cap} docs`;
    tone = u >= cap ? "var(--rose)" : "var(--ink3)"; showUpgrade = true;
  } else if (eff === "enterprise") {
    label = "Enterprise";
  } else {
    label = "Pro";
    tone = "var(--gold2)";
  }
  return (
    <div className="row gap-2" style={{ alignItems: "center" }}>
      <span className="mono" style={{
        fontSize: 11, color: tone, border: `1px solid ${tone}`,
        borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap",
      }}>{label}</span>
      {showUpgrade && (
        <button onClick={upgrade} style={{
          background: "var(--gold2)", color: "#1a1408", border: "none", cursor: "pointer",
          borderRadius: 999, padding: "4px 11px", fontSize: 11, fontWeight: 600,
        }}>Upgrade</button>
      )}
    </div>
  );
}

// ── Mobile app chrome ──────────────────────────────────────────────────────
const MOBILE_TABS = [
  { v: "dashboard", label: "Home", icon: "dashboard" },
  { v: "documents", label: "Docs", icon: "file" },
  { v: "insights", label: "Insights", icon: "trending" },
  { v: "__more", label: "More", icon: "menu" },
];
const MORE_VIEWS = ["groups", "connectors", "developer"];

function MobileTabBar({ view, setView, onMore }) {
  return (
    <nav style={{ flex: "0 0 auto", display: "flex", borderTop: "1px solid var(--line)", background: "color-mix(in srgb, var(--bg1) 88%, transparent)", backdropFilter: "blur(10px)", WebkitBackdropFilter: "blur(10px)", boxShadow: "var(--shadow-nav)", paddingBottom: "env(safe-area-inset-bottom,0px)" }}>
      {MOBILE_TABS.map((t) => {
        const active = t.v === "__more" ? MORE_VIEWS.includes(view) : view === t.v;
        return (
          <button key={t.v} onClick={() => (t.v === "__more" ? onMore() : setView(t.v))}
            style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, padding: "9px 0 10px",
                     background: "none", border: "none", cursor: "pointer", color: active ? "var(--gold2)" : "var(--ink3)",
                     fontSize: 10.5, fontWeight: active ? 600 : 500 }}>
            <span style={{ display: "grid", placeItems: "center", height: 24 }}><Icon name={t.icon} size={22} /></span>
            {t.label}
          </button>
        );
      })}
    </nav>
  );
}

function MoreSheet({ onClose, setView, onFeedback, onSignOut }) {
  const Item = ({ icon, label, onClick, danger }) => (
    <button onClick={onClick} style={{ width: "100%", display: "flex", alignItems: "center", gap: 15, padding: "15px 22px",
      background: "none", border: "none", color: danger ? "var(--rose)" : "var(--ink)", fontSize: 15, cursor: "pointer", textAlign: "left" }}>
      <span style={{ width: 24, display: "grid", placeItems: "center", color: danger ? "var(--rose)" : "var(--ink2)" }}>
        {icon ? <Icon name={icon} size={19} /> : null}
      </span>{label}
    </button>
  );
  const go = (v) => { setView(v); onClose(); };
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.5)", zIndex: 300, display: "flex", alignItems: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()} className="bg1" style={{ width: "100%", borderTopLeftRadius: 18, borderTopRightRadius: 18, paddingBottom: "max(14px, env(safe-area-inset-bottom))", borderTop: "1px solid var(--line)", maxHeight: "80vh", overflowY: "auto" }}>
        <div style={{ width: 38, height: 4, borderRadius: 4, background: "var(--line2)", margin: "8px auto 10px" }} />
        <Item icon="users" label="Groups" onClick={() => go("groups")} />
        <Item icon="link" label="Connectors" onClick={() => go("connectors")} />
        <Item icon="code" label="API keys" onClick={() => go("developer")} />
        <div style={{ height: 1, background: "var(--line)", margin: "6px 0" }} />
        <Item icon="message" label="Send feedback" onClick={() => { onFeedback(); onClose(); }} />
        <Item icon="x" label="Sign out" danger onClick={onSignOut} />
      </div>
    </div>
  );
}

function DocumentsShell() {
  const { user, logout, isCloud } = useAuth();
  const mobile = useIsMobile(820);
  const [moreOpen, setMoreOpen] = useState(false);
  const [view, setView] = useState("dashboard");  // "dashboard" | "documents" | "connectors"
  // Expose the current view on <body> so feedback (FeedbackFab + the user-menu form)
  // can tag which page it was sent from.
  useEffect(() => { document.body.dataset.view = view; }, [view]);
  // When set, the Documents view opens straight to this document (e.g. from an
  // Intelligence alert). Cleared when the Documents tab is opened normally.
  const [openDocId, setOpenDocId] = useState(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const openDocument = (docId) => { setOpenDocId(docId || null); setView("documents"); };
  // M47 · Workbench panel widths (persisted)
  const [leftW, setLeftW] = useState(() => { try { return Number(localStorage.getItem("docaiq.wb.leftW")) || 280; } catch { return 280; } });
  const [rightW, setRightW] = useState(() => { try { return Number(localStorage.getItem("docaiq.wb.rightW")) || 300; } catch { return 300; } });
  const [chatH, setChatH] = useState(() => { try { return Number(localStorage.getItem("docaiq.wb.chatH")) || 300; } catch { return 300; } });
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(false);
  const saveWb = (k, v) => { try { localStorage.setItem("docaiq.wb."+k, String(v)); } catch {} };
  const isWorkbench = !mobile && openDocId && view === "documents";
  return (
    // Fixed header + scrollable <main>. The shared globals.css sets
    // body{overflow:hidden} for the audit app's internal-scroll shell, so the
    // Documents product makes its own scroll region here instead of relying on
    // body scroll (which is clipped) — that's the "can't scroll down" fix.
    <div style={{ height: mobile ? "100dvh" : "100vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {mobile ? (
        <header className="row between" style={{ padding: "8px 14px", borderBottom: "1px solid var(--line)", flex: "0 0 auto", alignItems: "center" }}>
          <Logo />
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <PlanBadge sub={user?.subscription} isCloud={isCloud} />
            <button onClick={() => setFeedbackOpen(true)} title="Send feedback"
              style={{ width: 40, height: 40, borderRadius: 999, background: "var(--bg2)", border: "1px solid var(--line)", cursor: "pointer", color: "var(--ink2)", display: "grid", placeItems: "center" }}>
              <Icon name="message" size={18} /></button>
            <DocumentsUserMenu user={user} onSignOut={logout} onOpenConnectors={() => setView("connectors")} onOpenDeveloper={() => setView("developer")} compact />
          </div>
        </header>
      ) : (
      <header
        className="row app-header"
        style={{
          justifyContent: "space-between", alignItems: "center",
          padding: "12px 20px", borderBottom: "1px solid var(--line)", flex: "0 0 auto",
          position: "relative", zIndex: 10,
        }}
      >
        <div className="row gap-4 app-left" style={{ alignItems: "center", minWidth: 0 }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <Logo />
            <span className="serif app-brand" style={{ fontSize: 18 }}>Documents</span>
          </div>
          <nav className="row gap-4 app-nav" style={{ alignItems: "center", marginLeft: 8 }}>
            <NavTab active={view === "dashboard"} onClick={() => setView("dashboard")}>Dashboard</NavTab>
            <NavTab active={view === "insights"} onClick={() => setView("insights")}>Insights</NavTab>
            <NavTab active={view === "documents"} onClick={() => { setOpenDocId(null); setView("documents"); }}>Documents</NavTab>
          </nav>
        </div>
        <div className="row gap-3" style={{ alignItems: "center", flex: "0 0 auto" }}>
          <span className="app-planbadge"><PlanBadge sub={user?.subscription} isCloud={isCloud} /></span>
          <button onClick={() => setFeedbackOpen(true)} title="Send feedback"
            className="border bg2 hover-bg row gap-2"
            style={{ alignItems: "center", padding: "6px 12px", borderRadius: 999, fontSize: 12,
                     color: "var(--ink2)", cursor: "pointer" }}>
            <span style={{ fontSize: 14, lineHeight: 1 }}>💬</span> <span className="app-fb-text">Feedback</span>
          </button>
          <DocumentsUserMenu
            user={user}
            onSignOut={logout}
            onOpenDocuments={() => setView("documents")}
            onOpenGroups={() => setView("groups")}
            onOpenConnectors={() => setView("connectors")}
            onOpenDeveloper={() => setView("developer")}
          />
        </div>
      </header>
      )}
      <VerifyBanner user={user} />
      <RestorePrompt onRestored={() => setView("documents")} />
      <main className="app-main" style={{
        flex: "1 1 auto", overflowY: "auto",
        // dashboard + documents are full-width 2/3-pane workspaces (docked detail pane); others stay narrow
        padding: view === "dashboard" ? "18px 20px" : (view === "documents" ? 0 : 20),
        maxWidth: (view === "dashboard" || view === "documents" || view === "insights") ? "none" : 1200,
        margin: "0 auto", width: "100%", boxSizing: "border-box",
      }}>
        {view === "dashboard" && (
          <DocumentsDashboard
            onOpenDocuments={() => setView("documents")}
            onOpenConnectors={() => setView("connectors")}
          />
        )}
        {view === "insights" && <ErrorBoundary><DashboardView onOpenDocument={openDocument} /></ErrorBoundary>}
        {view === "documents" && !isWorkbench && (
          <AllDocuments vendorPk={null} openDocId={openDocId} onOpenGroups={() => setView("groups")} />
        )}
        {/* M47 · Workbench: multi-panel IDE-style layout when a document is open */}
        {isWorkbench && <WorkbenchView
          openDocId={openDocId} setOpenDocId={setOpenDocId}
          leftOpen={leftOpen} setLeftOpen={setLeftOpen} leftW={leftW} setLeftW={(w) => { setLeftW(w); saveWb("leftW", w); }}
          rightOpen={rightOpen} setRightOpen={setRightOpen} rightW={rightW} setRightW={(w) => { setRightW(w); saveWb("rightW", w); }}
        />}
        {view === "groups" && <DocumentsGroups onOpenDocuments={() => setView("documents")} />}
        {view === "connectors" && <DocumentsConnectors onSynced={() => setView("documents")} />}
        {view === "developer" && <DeveloperKeys />}
      </main>
      {mobile && (
        <MobileTabBar view={view} onMore={() => setMoreOpen(true)}
          setView={(v) => { setOpenDocId(null); setView(v); }} />
      )}
      {mobile && moreOpen && (
        <MoreSheet onClose={() => setMoreOpen(false)}
          setView={(v) => { setOpenDocId(null); setView(v); }}
          onFeedback={() => setFeedbackOpen(true)} onSignOut={logout} />
      )}
      {feedbackOpen && <FeedbackDialog onClose={() => setFeedbackOpen(false)} />}
    </div>
  );
}

function DocGate() {
  const { status } = useAuth();
  // Anonymous visitors see the marketing landing first; "Sign in"/"with email"
  // reveals the existing auth form. "Sign in with Google" on the landing goes
  // straight to /api/auth/google/login (same flow DocumentsAuth uses).
  const [showAuth, setShowAuth] = useState(false);
  if (status === "booting") return <LoadingState label="Loading your documents…" />;
  if (status === "anon") {
    return showAuth
      ? <DocumentsAuth onBack={() => setShowAuth(false)} />
      : <DocumentsLanding onSignIn={() => setShowAuth(true)} />;
  }
  return <DocumentsShell />;
}

// M47 · Clean Workbench — 3 resizable columns. No wrapper div interference.
function WorkbenchView({ openDocId, setOpenDocId, leftOpen, setLeftOpen, leftW, setLeftW, rightOpen, setRightOpen, rightW, setRightW }) {
  const [doc, setDoc] = React.useState(null);
  React.useEffect(() => {
    if (!openDocId) return;
    let c = false;
    fetchDocument(openDocId).then(d => { if (!c) setDoc(d); }).catch(() => {});
    return () => { c = true; };
  }, [openDocId]);

  if (!doc) return <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
    <span className="ink3">Loading…</span></div>;

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0, overflow: "hidden" }}>
      {/* LEFT: Document Index */}
      <Panel column left open={leftOpen} onToggle={() => setLeftOpen(!leftOpen)}
        w={leftW} setW={setLeftW} minW={180} maxW={500} title="Documents">
        <AllDocuments vendorPk={null} openDocId={openDocId} />
      </Panel>

      {/* CENTER: Document Viewer */}
      <div style={{ flex: "1 1 0", minWidth: 320, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <DocumentChatPanel doc={doc} onClose={() => setOpenDocId(null)} onDocUpdated={setDoc} workbench />
      </div>

      {/* RIGHT: Advanced */}
      <Panel column right open={rightOpen} onToggle={() => setRightOpen(!rightOpen)}
        w={rightW} setW={setRightW} minW={240} maxW={600} title="Advanced">
        <div style={{ padding: 10, fontSize: 12, color: "var(--ink2)" }}>
          <div className="ink3" style={{ textAlign: "center", padding: 20 }}>Fields, chunks, and schema views go here.</div>
        </div>
      </Panel>
    </div>
  );
}

// A single collapsible/resizable side panel (left or right)
function Panel({ column, left, right, open, onToggle, w, setW, minW, maxW, title, children }) {
  const [dragging, setDragging] = React.useState(false);
  React.useEffect(() => {
    if (!dragging) return;
    const m = (e) => setW(p => Math.max(minW, Math.min(maxW, p + (left ? e.movementX : -e.movementX))));
    const u = () => setDragging(false);
    window.addEventListener("mousemove", m);
    window.addEventListener("mouseup", u);
    return () => { window.removeEventListener("mousemove", m); window.removeEventListener("mouseup", u); };
  }, [dragging, setW, minW, maxW, left]);

  const borderSide = left ? { borderRight: "1px solid var(--line)" } : { borderLeft: "1px solid var(--line)" };

  if (!open) {
    return (
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", background: "var(--bg2)", ...borderSide }}>
        <button onClick={onToggle} title={`Open ${title}`}
          style={{ writingMode: "vertical-rl", padding: "8px 6px", fontSize: 10, fontWeight: 600,
            color: "var(--ink3)", background: "none", border: "none", cursor: "pointer",
            letterSpacing: "0.05em", textTransform: "uppercase" }}>
          {title}
        </button>
      </div>
    );
  }

  return (
    <div style={{ width: w, minWidth, maxWidth, flex: "0 0 auto", display: "flex", flexDirection: "column", overflow: "hidden", position: "relative", ...borderSide }}>
      <div className="row between border-b" style={{ flex: "0 0 auto", padding: "5px 8px", alignItems: "center", background: "var(--bg2)" }}>
        <span className="upper" style={{ fontSize: 9, letterSpacing: ".06em", color: "var(--ink3)" }}>{title}</span>
        <button onClick={onToggle} title={`Close ${title}`}
          style={{ background: "none", border: "none", cursor: "pointer", fontSize: 13, color: "var(--ink3)", padding: 0, lineHeight: 1 }}>✕</button>
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>{children}</div>
      {/* Resize handle on outer edge */}
      <div onMouseDown={() => setDragging(true)} title="Drag to resize"
        style={{ position: "absolute", [right ? "left" : "right"]: -3, top: 0, bottom: 0, width: 6, cursor: "col-resize", zIndex: 10 }} />
    </div>
  );
}

// M47 · Advanced panel tabs
function AdvancedWorkbenchPanel({ docId }) {
  const TABS = [["fields","📋 Fields"],["chunks","🧩 Chunks"],["schema","📐 Schema"],["linked","🔗 Linked"],["markdown","📄 MD"]];
  const [tab, setTab] = React.useState("fields");
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="row gap-1" style={{ flex: "0 0 auto", flexWrap: "wrap", marginBottom: 8 }}>
        {TABS.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            style={{ padding: "4px 8px", borderRadius: 4, fontSize: 10.5, cursor: "pointer",
              background: tab === id ? "var(--bg3)" : "transparent",
              color: tab === id ? "var(--ink)" : "var(--ink3)",
              border: tab === id ? "1px solid var(--line)" : "1px solid transparent",
              fontWeight: tab === id ? 600 : 400, whiteSpace: "nowrap" }}>
            {label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: "0 4px" }}>
        <div className="ink3" style={{ fontSize: 11, textAlign: "center", padding: 20 }}>
          Open a document to see {tab} details here.
        </div>
      </div>
    </div>
  );
}

export default function DocumentsApp() {
  useEffect(() => {
    // Honor the theme the user picked in Settings → Appearance (else dark).
    let saved;
    try { saved = localStorage.getItem("docaiq.documents.theme"); } catch { /* ignore */ }
    document.body.className = saved === "light" ? "light" : "dark";
  }, []);
  return (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <DialogHost>
            <DocGate />
          </DialogHost>
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  );
}
