import React from "react";
import Icon from "./Icon.jsx";

// ─────────────────────────────────────────────────────────────
// Logo
// ─────────────────────────────────────────────────────────────
export const Logo = () => (
  <div className="row gap-2" style={{ gap: 10 }}>
    <div className="brand-mark">Aiq</div>
    <div className="serif font-semibold app-logo-text" style={{ fontSize: 17, letterSpacing: "-0.01em" }}>
      DocAIQuest
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

