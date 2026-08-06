import React, { Fragment, useState } from "react";
import Icon from "./Icon.jsx";

// ─────────────────────────────────────────────────────────────
// Logo
// ─────────────────────────────────────────────────────────────
export const Logo = () => (
  <div className="row gap-2" style={{ gap: 10 }}>
    <div className="brand-mark">Aiq</div>
    <div className="serif font-semibold app-logo-text" style={{ fontSize: 17, letterSpacing: "-0.01em" }}>
      DocAIQ<span className="italic font-normal ink2"> / audit</span>
    </div>
  </div>
);

// ─────────────────────────────────────────────────────────────
// StatusDot
// ─────────────────────────────────────────────────────────────
export const StatusDot = ({ status }) => {
  const cfg = { ok: { icon: "check" }, warn: { label: "!" }, miss: { label: "×" }, todo: { label: "·" } }[status] || {};
  return (
    <div className={`s-${status}`} style={{ width: 18, height: 18, borderRadius: "50%", display: "grid", placeItems: "center", fontSize: 10, fontWeight: 600, flexShrink: 0 }}>
      {cfg.icon ? <Icon name={cfg.icon} size={10} /> : cfg.label}
    </div>
  );
};

// ─────────────────────────────────────────────────────────────
// Pill
// ─────────────────────────────────────────────────────────────
export const Pill = ({ children, color = "neutral" }) => {
  const styles = {
    neutral: { bg: "var(--bg3)", color: "var(--ink2)" },
    gold: { bg: "rgba(200,160,76,0.10)", color: "var(--gold2)", border: "1px solid rgba(200,160,76,.25)" },
    green: { bg: "rgba(63,164,122,.15)", color: "#3FA47A" },
    amber: { bg: "rgba(224,162,59,.15)", color: "#E0A23B" },
    rose: { bg: "rgba(216,98,94,.15)", color: "#D8625E" },
    violet: { bg: "rgba(139,127,214,.15)", color: "#8B7FD6" },
  }[color] || {};
  return (
    <span style={{ background: styles.bg, color: styles.color, border: styles.border, padding: "2px 8px", borderRadius: 4, fontSize: 10, letterSpacing: "0.10em", textTransform: "uppercase", fontWeight: 600 }}>
      {children}
    </span>
  );
};

export const RiskPill = ({ risk }) => {
  const c = { high: "rose", medium: "amber", low: "green" }[risk];
  return <Pill color={c}>{risk} risk</Pill>;
};

// ─────────────────────────────────────────────────────────────
// Avatar
// ─────────────────────────────────────────────────────────────
export const Avatar = ({ initials = "EC", size = 30, ai = false }) => (
  <div style={{
    width: size, height: size, borderRadius: "50%", display: "grid", placeItems: "center",
    fontWeight: 600, fontSize: size === 30 ? 11 : Math.max(9, size / 3),
    background: ai ? "radial-gradient(circle at 30% 30%, #E2BC68 0%, #8A6B23 60%, #3A2B0E 100%)"
                  : "linear-gradient(135deg, #3a3d4a, #1a1c22)",
    color: "#E2BC68", border: "1px solid var(--line2)",
    boxShadow: ai ? "0 0 0 1px #5C4517, inset 0 0 6px rgba(255,255,255,.18)" : "none",
  }}>{!ai && initials}</div>
);

// ─────────────────────────────────────────────────────────────
// Chip
// ─────────────────────────────────────────────────────────────
export const Chip = ({ children, active, onClick }) => (
  <button onClick={onClick} className="text-sm" style={{
    padding: "4px 10px", borderRadius: 9999,
    border: `1px solid ${active ? "var(--line2)" : "var(--line)"}`,
    background: active ? "var(--bg3)" : "transparent",
    color: active ? "var(--ink)" : "var(--ink2)",
  }}>{children}</button>
);

// ─────────────────────────────────────────────────────────────
// KpiCard
// ─────────────────────────────────────────────────────────────
export const KpiCard = ({ label, value, sublabel, accent, trend }) => (
  <div className="bg1 border rounded-xl p-4">
    <div className="upper ink3">{label}</div>
    <div className="serif font-semibold tracking-tight mt-2" style={{ fontSize: 28, lineHeight: 1, color: accent }}>{value}</div>
    <div className="text-sm ink3 mt-2">{sublabel}</div>
    {trend && <div className="mt-1" style={{ fontSize: 10, color: accent || "var(--ink2)" }}>{trend}</div>}
  </div>
);

// ─────────────────────────────────────────────────────────────
// LoadingState / ErrorState — shown by views while their API call
// resolves. Same shell so every view feels consistent during transitions.
// ─────────────────────────────────────────────────────────────
export const LoadingState = ({ label = "Loading…" }) => (
  <div className="grow overflow-auto">
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 32 }}>
      <div className="ink3 mono text-sm">{label}</div>
    </div>
  </div>
);

export const ErrorState = ({ message, onRetry }) => (
  <div className="grow overflow-auto">
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 32 }}>
      <div className="bg1 border rounded-xl p-5">
        <div className="serif font-semibold text-lg mb-2" style={{ color: "#D8625E" }}>
          Could not load data
        </div>
        <div className="ink2 text-base">{message || "Something went wrong while loading this page."}</div>
        <div className="ink3 text-sm mt-3">
          If this keeps happening, contact your administrator.
        </div>
        <div className="row gap-2 mt-4">
          <button onClick={() => (onRetry ? onRetry() : window.location.reload())}
                  className="btn-gold"
                  style={{ padding: "6px 14px", borderRadius: 6, fontSize: 12 }}>
            Try again
          </button>
        </div>
      </div>
    </div>
  </div>
);

// ─────────────────────────────────────────────────────────────
// Sidebar
// ─────────────────────────────────────────────────────────────
export const Sidebar = ({ view, setView, hasRole = () => true, isViewing = () => false, collapsed = false, onToggleCollapsed }) => {
  // Sidebar variants:
  //   isVendorOnly       — vendor users see only their docs + RFIs + profile
  //   isReviewerOnly     — reviewers see the audit-doing surfaces only.
  //                        Everything operator-y (routing, learning,
  //                        LLM/billing/keys) is dropped.
  //   default            — admin/owner sees the admin/oversight surface.
  //
  // Vendor Portal is gated to the REVIEWER effective persona (single
  // role = reviewer, OR admin/owner persona-toggled to reviewer). For
  // admins on default persona, the equivalent vendor surface is the
  // "Vendors" oversight view (which carries the admin-only actions:
  // assign primary reviewer, start audit per vendor). Admins who want
  // to see the reviewer workspace toggle persona to reviewer.
  const isVendorOnly = hasRole("vendor") && !hasRole("admin") && !hasRole("owner") && !hasRole("reviewer");
  const isReviewerOnly = hasRole("reviewer") && !hasRole("admin") && !hasRole("owner");
  const showVendorPortal = isReviewerOnly || isViewing("reviewer");

  const items = isVendorOnly ? [
    // Vendor users get a deliberately tiny nav. The VendorHome view is
    // a single-page workspace — identity + urgent items + uploads + reqs.
    // Profile is the only secondary destination (for password / email).
    { id: "vendor-home", icon: "building", label: "Home" },
    { id: "settings", icon: "cog", label: "Profile" },
  ] : isReviewerOnly ? [
    { id: "dashboard", icon: "dashboard", label: "Dashboard" },
    { id: "vendors", icon: "building", label: "Vendor Portal" },
    { id: "history", icon: "history", label: "History" },
    { id: "settings", icon: "cog", label: "Settings" },
  ] : [
    { id: "dashboard", icon: "dashboard", label: "Dashboard" },
    // Vendor Portal · reviewer workspace. Only shown when the effective
    // persona is reviewer (admin can persona-toggle to get here).
    ...(showVendorPortal ? [{ id: "vendors", icon: "building", label: "Vendor Portal" }] : []),
    // Admin's vendor surface — replaces Vendor Portal in the default
    // admin persona. Hosts the two admin-only vendor actions (assign
    // primary reviewer, start audit per vendor) inline.
    { id: "admin-vendors", icon: "shield", label: "Vendors", requiresRole: "admin" },
    { id: "reviewers", icon: "users", label: "Reviewers", requiresRole: "admin" },
    // KYC Subjects tab removed 2026-05-25 · KYC is now treated as just
    // another framework (use the KYC / KYB / AML pack in Audit Frameworks).
    // See commit 6386cbd for rationale.
    { id: "history", icon: "history", label: "History" },
    // M43 · Modules section. Frameworks Marketplace = cross-tenant
    // catalog browse + install. Matchmaking shows doc × requirement
    // matrix. Documents Extraction = existing AllDocuments surface
    // (entered through Vendor Portal today; standalone module surface
    // pending in M43 follow-up).
    { id: "marketplace", icon: "star", label: "Frameworks" },
    { id: "matchmaking", icon: "compare", label: "Matchmaking" },
    // Custom Frameworks · tenant-local pack customizer (was "Audit
    // Frameworks"). Kept for orgs that author their own packs alongside
    // the marketplace.
    { id: "frameworks", icon: "layers", label: "Custom Frameworks", requiresRole: "admin" },
    { id: "routing", icon: "cpu", label: "LLM Routing", requiresRole: "admin" },
    { id: "learning", icon: "sparkle", label: "Learning Loop" },
    { id: "settings", icon: "cog", label: "Settings" },
  ].filter(it => !it.requiresRole || hasRole(it.requiresRole));

  const width = collapsed ? 56 : 200;

  return (
    <aside className="bg1 border-r flex col"
           style={{ width, flexShrink: 0, transition: "width 140ms ease" }}>
      {/* Header · logo + a small toggle button on the right (or centred when
          collapsed). `<` closes (icons only); `>` opens (full labels). One
          button, two states — no confusing "hidden" mode. */}
      <div className="border-b row"
           style={{ height: 52, alignItems: "center",
                    justifyContent: collapsed ? "center" : "space-between",
                    padding: collapsed ? "0" : "0 8px 0 16px" }}>
        {!collapsed && <Logo />}
        <button onClick={onToggleCollapsed}
                title={collapsed ? "Open sidebar · [" : "Close sidebar · ["}
                aria-label={collapsed ? "Open sidebar" : "Close sidebar"}
                className="hover-bg ink2"
                style={{
                  width: 28, height: 28, borderRadius: 4,
                  display: "grid", placeItems: "center",
                  fontSize: 12, fontWeight: 700,
                }}>
          {collapsed ? "›" : "‹"}
        </button>
      </div>

      <nav className="flex col p-3 gap-1 grow">
        {items.map(it => (
          <button key={it.id} onClick={() => setView(it.id)}
            title={collapsed ? it.label : undefined}
            className="row hover-bg" style={{
              padding: collapsed ? "8px 0" : "8px 10px",
              borderRadius: 6, gap: 10, fontSize: 12.5,
              justifyContent: collapsed ? "center" : "flex-start",
              background: view === it.id ? "var(--bg3)" : "transparent",
              color: view === it.id ? "var(--ink)" : "var(--ink2)",
              borderLeft: view === it.id ? "2px solid var(--gold)" : "2px solid transparent",
              fontWeight: view === it.id ? 600 : 500,
              position: "relative",
            }}>
            <Icon name={it.icon} />
            {!collapsed && (
              <span className="grow" style={{ textAlign: "left" }}>{it.label}</span>
            )}
            {it.badge && !collapsed && (
              <span style={{ background: "rgba(216,98,94,.15)", color: "#D8625E", fontSize: 10, padding: "1px 6px", borderRadius: 9999, fontWeight: 600 }}>
                {it.badge}
              </span>
            )}
            {it.badge && collapsed && (
              <span style={{
                position: "absolute", top: 4, right: 4,
                background: "#D8625E", color: "white", fontSize: 8,
                padding: "0 4px", borderRadius: 9999, fontWeight: 700,
                minWidth: 14, textAlign: "center",
              }}>{it.badge}</span>
            )}
          </button>
        ))}
      </nav>

    </aside>
  );
};

// ─────────────────────────────────────────────────────────────
// Topbar
// ─────────────────────────────────────────────────────────────
const initialsOf = (name) =>
  (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join("") || "?";

const primaryRole = (roles = []) =>
  ["owner", "admin", "reviewer", "vendor"].find(r => roles.includes(r)) || "member";

export const Topbar = ({
  theme, onToggleTheme, breadcrumbs = [], user, onLogout, onOpenSearch,
  onOpenHelp,
  // Persona toggle props — passed by AuthedApp when the user holds 2+ roles.
  viewAs, availablePersonas = [], onSetViewAs,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [personaOpen, setPersonaOpen] = useState(false);
  const showPersona = availablePersonas.length >= 2;
  // What is the user currently viewing as? Default to their "highest"
  // role (first in availablePersonas, which is owner > admin > reviewer > vendor).
  const currentPersona = viewAs || availablePersonas[0] || null;
  return (
  <header className="border-b row between px-5" style={{ height: 52, background: "linear-gradient(180deg, var(--bg1), var(--bg))", flexShrink: 0, position: "relative" }}>
    <div className="row gap-3">
      {breadcrumbs.map((b, i) => (
        <Fragment key={i}>
          {i > 0 && <span className="ink4">›</span>}
          <span className={`text-base ${b.active ? "ink font-medium" : "ink3"}`}>{b.label}</span>
        </Fragment>
      ))}
    </div>
    <div className="row gap-2">
      {showPersona && (
        <div style={{ position: "relative" }}>
          <button
            onClick={() => setPersonaOpen(o => !o)}
            className="row gap-2 border bg2 hover-bg"
            style={{ padding: "5px 10px", borderRadius: 6, fontSize: 11 }}
            title="Switch which role's UI shell you see (your permissions don't change)"
          >
            <span className="ink3">Viewing as</span>
            <span className="mono font-semibold" style={{ color: "var(--gold)" }}>{currentPersona}</span>
            <span className="ink3" style={{ fontSize: 10 }}>▾</span>
          </button>
          {personaOpen && (
            <div className="bg1 border rounded-md"
                 style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, minWidth: 220, zIndex: 10, boxShadow: "0 6px 18px rgba(0,0,0,0.25)" }}>
              <div className="p-3 border-b">
                <div className="text-sm font-medium">Preview role</div>
                <div className="ink3 text-xs mt-1">UI shell only · backend perms unchanged</div>
              </div>
              {availablePersonas.map(r => (
                <button
                  key={r}
                  onClick={() => { onSetViewAs && onSetViewAs(r === availablePersonas[0] ? null : r); setPersonaOpen(false); }}
                  className="w-full hover-bg row between"
                  style={{ padding: "8px 12px", fontSize: 12, textAlign: "left" }}
                >
                  <span className={r === currentPersona ? "font-medium ink" : "ink2"}>{r}</span>
                  {r === currentPersona && <span className="ink3" style={{ fontSize: 10 }}>active</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <button onClick={onOpenSearch} className="bg2 border rounded row gap-2 ink3 px-3 hover-bg"
              style={{ padding: "6px 10px", fontSize: 12, width: 220, cursor: "pointer" }}>
        <Icon name="search" size={13} />
        <span>Find anything…</span>
        <kbd className="mono ink3" style={{ marginLeft: "auto", fontSize: 10, border: "1px solid var(--line2)", borderRadius: 3, padding: "1px 5px", background: "var(--bg1)" }}>⌘K</kbd>
      </button>
      <button className="hover-bg ink2" onClick={onToggleTheme} style={{ width: 30, height: 30, borderRadius: 6, display: "grid", placeItems: "center" }}>
        <Icon name={theme === "dark" ? "sun" : "moon"} />
      </button>
      <button
        className="hover-bg ink2"
        onClick={onOpenHelp}
        title="Help & FAQ"
        style={{ width: 30, height: 30, borderRadius: 6, display: "grid", placeItems: "center", fontWeight: 600, fontSize: 14 }}
      >?</button>
      <button className="hover-bg ink2" style={{ width: 30, height: 30, borderRadius: 6, display: "grid", placeItems: "center" }}>
        <Icon name="bell" />
      </button>
      {user && (
        <div style={{ position: "relative" }}>
          <button onClick={() => setMenuOpen(o => !o)}
                  className="row gap-2 hover-bg"
                  style={{ padding: "4px 8px 4px 4px", borderRadius: 6 }}>
            <Avatar initials={initialsOf(user.name)} size={26}/>
            <div className="col" style={{ textAlign: "left" }}>
              <div className="text-sm font-medium" style={{ lineHeight: 1.1 }}>{user.name}</div>
              <div className="upper ink3" style={{ fontSize: 9 }}>{primaryRole(user.roles)} · {user.orgId}</div>
            </div>
          </button>
          {menuOpen && (
            <div className="bg1 border rounded-md"
                 style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, minWidth: 200, zIndex: 10, boxShadow: "0 6px 18px rgba(0,0,0,0.25)" }}>
              <div className="p-3 border-b">
                <div className="text-sm font-medium">{user.email}</div>
                <div className="text-xs ink3 mt-1">Roles: <span className="mono">{(user.roles || []).join(", ") || "none"}</span></div>
              </div>
              <button onClick={() => { setMenuOpen(false); onLogout(); }}
                      className="w-full hover-bg ink2"
                      style={{ padding: "8px 12px", fontSize: 12, textAlign: "left" }}>
                Sign out
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  </header>
  );
};

// StatusBar (bottom footer with fake demo stats — "71% / 22% / 7%", "184
// audits · $11.40", "v 1.4.2") removed: hardcoded placeholders nobody reads.
// If a real status surface is needed later, build it from /api/usage or the
// analytics endpoints rather than seed-time literals.
