// M46 · Documents System auth screen — sign in OR create a free account.
//
// A SEPARATE component from the shared views/Login.jsx (which the auditing app
// uses and must stay invite-only). The documents product is self-service, so
// this screen adds a "Create account" tab wired to AuthContext.register, which
// hits POST /api/auth/register (403 on the auditing product). New accounts get
// their own physically-isolated workspace — enforced server-side by the
// per-user owner scope (M46).
import React, { useState } from "react";
import { Logo } from "../components/Shell.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { register as apiRegister } from "../api/documents";

export default function DocumentsAuth({ onBack }) {
  const { config, login } = useAuth();
  const [mode, setMode] = useState("signin");   // "signin" | "signup"
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [consent, setConsent] = useState(false);   // §compliance · signup consent
  const [error, setError] = useState(null);

  const devEnabled = config?.devLoginEnabled;
  const googleEnabled = config?.googleLoginEnabled;
  const isSignup = mode === "signup";

  const onSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (isSignup) {
        if (!consent) { setError("Please consent to processing to create an account."); setSubmitting(false); return; }
        // Register sets the session cookie server-side; reload re-bootstraps
        // AuthProvider straight into the signed-in session (keeps the shared
        // AuthContext free of any documents-only register surface).
        await apiRegister({ email, password, name, consent: true });
        window.location.reload();
        return;
      }
      await login(email, password);
    } catch (err) {
      setError(err.message || (isSignup ? "Registration failed" : "Sign-in failed"));
    } finally {
      setSubmitting(false);
    }
  };

  const switchMode = (next) => {
    setMode(next);
    setError(null);
  };

  return (
    <div className="flex col" style={{ height: "100vh", justifyContent: "center", alignItems: "center" }}>
      <div className="bg1 border rounded-xl" style={{ width: 420, padding: 36 }}>
        {onBack && (
          <button type="button" onClick={onBack}
            style={{ background: "none", border: "none", cursor: "pointer", padding: 0,
                     fontSize: 12, color: "var(--ink3)", marginBottom: 14 }}>
            ← Back to home
          </button>
        )}
        <div className="row gap-2 mb-2" style={{ alignItems: "center" }}>
          <Logo />
          <span className="serif" style={{ fontSize: 18 }}>Documents</span>
        </div>

        <h1 className="serif font-semibold tracking-tight" style={{ fontSize: 28, lineHeight: 1.1, marginTop: 14 }}>
          {isSignup
            ? <>Create <em className="italic font-normal ink2">account</em></>
            : <>Sign <em className="italic font-normal ink2">in</em></>}
        </h1>
        <p className="ink2 mt-3 leading" style={{ fontSize: 13 }}>
          {isSignup
            ? "Upload, parse and chat with your documents. Your workspace is private — only you can see your files."
            : "Welcome back. Your documents are waiting."}
        </p>

        {error && (
          <div className="border rounded-md mt-4" style={{ padding: "8px 12px", fontSize: 12, background: "rgba(216,98,94,0.08)", borderColor: "rgba(216,98,94,.30)", color: "#D8625E" }}>
            <span className="mono">{error}</span>
          </div>
        )}

        {googleEnabled && (
          <div style={{ marginTop: 20 }}>
            <a href="/api/auth/google/login"
               className="border bg2 hover-bg row gap-2"
               style={{ width: "100%", boxSizing: "border-box", justifyContent: "center", alignItems: "center",
                        padding: "10px 14px", borderRadius: 8, fontSize: 13, color: "var(--ink)", textDecoration: "none" }}>
              <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
                <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.3-.4-3.5z"/>
                <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
                <path fill="#4CAF50" d="M24 44c5.2 0 10-2 13.6-5.2l-6.3-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.1-11.3-7.5l-6.5 5C9.5 39.6 16.2 44 24 44z"/>
                <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4 5.6l6.3 5.2C41.4 36.3 44 30.7 44 24c0-1.3-.1-2.3-.4-3.5z"/>
              </svg>
              Continue with Google
            </a>
            {devEnabled && (
              <div className="row" style={{ alignItems: "center", gap: 10, margin: "16px 0 0" }}>
                <div style={{ flex: 1, height: 1, background: "var(--line)" }} />
                <span className="ink3" style={{ fontSize: 11 }}>or with email</span>
                <div style={{ flex: 1, height: 1, background: "var(--line)" }} />
              </div>
            )}
          </div>
        )}

        {!devEnabled ? (
          !googleEnabled && (
            <div className="ink3 mt-4" style={{ fontSize: 12 }}>
              Password accounts are disabled in this environment. Configure{" "}
              <span className="mono">DOCAIQ_AUTH_PROVIDER=dev</span>{" "}or set the Google OAuth env vars.
            </div>
          )
        ) : (
          <form onSubmit={onSubmit} style={{ marginTop: 20 }}>
            {isSignup && (
              <>
                <label className="upper ink3 mb-1" style={{ display: "block" }}>Name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  className="bg2 border"
                  style={{ width: "100%", padding: "9px 12px", borderRadius: 6, fontSize: 13, color: "var(--ink)", outline: "none", marginBottom: 12 }}
                  placeholder="Your name (optional)"
                />
              </>
            )}
            <label className="upper ink3 mb-1" style={{ display: "block" }}>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
              className="bg2 border"
              style={{ width: "100%", padding: "9px 12px", borderRadius: 6, fontSize: 13, color: "var(--ink)", outline: "none", marginBottom: 12 }}
              placeholder="you@example.com"
            />
            <label className="upper ink3 mb-1" style={{ display: "block" }}>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isSignup ? "new-password" : "current-password"}
              required
              minLength={isSignup ? 8 : undefined}
              className="bg2 border"
              style={{ width: "100%", padding: "9px 12px", borderRadius: 6, fontSize: 13, color: "var(--ink)", outline: "none" }}
            />
            {isSignup && (
              <div className="ink3 mt-1" style={{ fontSize: 11 }}>At least 8 characters.</div>
            )}
            {isSignup && (
              <label className="row gap-2 mt-3" style={{ alignItems: "flex-start", fontSize: 11.5, cursor: "pointer", lineHeight: 1.4 }}>
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}
                  style={{ marginTop: 2, accentColor: "var(--gold2)", flexShrink: 0 }} />
                <span className="ink2">
                  I consent to DocAIQuest processing my documents to extract data and answer
                  questions, including sending <strong>redacted</strong> text to third-party
                  AI providers. My original files stay in my own Google Drive. I agree to the{" "}
                  <a href="/terms.html" target="_blank" rel="noopener" style={{ color: "var(--gold2)" }}>Terms</a>
                  {" "}and{" "}
                  <a href="/privacy.html" target="_blank" rel="noopener" style={{ color: "var(--gold2)" }}>Privacy Policy</a>.
                </span>
              </label>
            )}
            {!isSignup && devEnabled && (
              <button type="button" onClick={() => { setEmail("demo@example.com"); setPassword("demo"); }}
                className="border bg2 hover-bg"
                style={{ marginTop: 12, padding: "8px 14px", borderRadius: 8, fontSize: 12, cursor: "pointer", width: "100%" }}>
                🧪 Quick Demo Login
              </button>
            )}
            <button type="submit" disabled={submitting || (isSignup && !consent)}
                    className="btn-gold w-full"
                    style={{ marginTop: 12, padding: "10px 14px", borderRadius: 8, fontSize: 13, opacity: submitting ? 0.6 : 1 }}>
              {submitting
                ? (isSignup ? "Creating account…" : "Signing in…")
                : (isSignup ? "Create account" : "Sign in")}
            </button>

            <div className="ink3 mt-4" style={{ fontSize: 12, textAlign: "center" }}>
              {isSignup ? (
                <>Already have an account?{" "}
                  <button type="button" onClick={() => switchMode("signin")}
                          style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 12, color: "var(--gold2)" }}>
                    Sign in
                  </button>
                </>
              ) : (
                <>New here?{" "}
                  <button type="button" onClick={() => switchMode("signup")}
                          style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 12, color: "var(--gold2)" }}>
                    Create a free account
                  </button>
                </>
              )}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
