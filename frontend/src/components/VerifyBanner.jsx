// M48 · email-verification banner. Shown under the header when the signed-in
// user hasn't confirmed their email (email/password signups). Offers a Resend
// link and reacts to the ?verified=1|0 flag the /api/auth/verify redirect sets.
import React, { useEffect, useState } from "react";
import { resendVerification } from "../api/documents";

export default function VerifyBanner({ user }) {
  const [state, setState] = useState("idle");   // idle | sending | sent | error
  const [justVerified, setJustVerified] = useState(false);
  const [linkExpired, setLinkExpired] = useState(false);

  // Read the ?verified flag the verify-link redirect lands on, then clean the URL.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("verified") === "1") setJustVerified(true);
    if (p.get("verified") === "0") setLinkExpired(true);
    if (p.has("verified")) {
      p.delete("verified");
      const qs = p.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? "?" + qs : ""));
    }
  }, []);

  // Success flash after clicking the link (works even before /me refreshes).
  if (justVerified) {
    return <Bar tone="var(--emerald)">
      <span>✓ Your email is verified. Thanks!</span>
      <button style={dismiss} onClick={() => setJustVerified(false)}>Dismiss</button>
    </Bar>;
  }

  // Verified accounts (Google, grandfathered, or just-confirmed) → nothing.
  if (!user || user.emailVerified !== false) return null;

  const resend = async () => {
    setState("sending");
    try { await resendVerification(); setState("sent"); }
    catch (e) {
      // 429 from the resend rate-limit, or any error
      setState("error");
    }
  };

  return (
    <Bar tone="var(--amber)">
      <span>
        {linkExpired ? "That verification link expired or was already used. " : ""}
        Please verify your email{user.email ? ` (${user.email})` : ""} — check your inbox for the confirmation link.
      </span>
      <span className="row gap-2" style={{ marginLeft: "auto", alignItems: "center" }}>
        {state === "sent"
          ? <span style={{ color: "var(--emerald)", fontSize: 12 }}>Sent — check your inbox.</span>
          : state === "error"
          ? <span style={{ color: "var(--rose)", fontSize: 12 }}>Couldn’t resend — try again shortly.</span>
          : (
            <button style={btn} disabled={state === "sending"} onClick={resend}>
              {state === "sending" ? "Sending…" : "Resend email"}
            </button>
          )}
      </span>
    </Bar>
  );
}

const Bar = ({ children, tone }) => (
  <div className="row gap-2" style={{
    alignItems: "center", padding: "8px 16px", fontSize: 13,
    borderBottom: "1px solid var(--line)", background: "var(--panel)", color: "var(--ink2)",
    borderLeft: `3px solid ${tone}`,
  }}>{children}</div>
);
const btn = {
  background: "var(--gold2)", color: "#1a1408", border: "none", cursor: "pointer",
  borderRadius: 999, padding: "5px 13px", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap",
};
const dismiss = {
  background: "none", color: "var(--ink2)", border: "1px solid var(--line)", cursor: "pointer",
  borderRadius: 999, padding: "4px 11px", fontSize: 12, marginLeft: "auto",
};
