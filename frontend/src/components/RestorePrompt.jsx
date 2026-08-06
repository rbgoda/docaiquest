// M47 · §5 · disaster-recovery prompt. On login we ask the backend whether a
// workspace.sqlite backup exists in the user's own Google Drive. If the account
// looks wiped (0 docs) we pop a modal offering a one-click restore of documents
// + classifications + chunks/embeddings + learned vocabulary + chat. If the
// account already has docs, we show a dismissible banner so a restore is still
// one click away. The snapshot is kept fresh automatically after every sync.
import React, { useEffect, useState } from "react";
import { fetchRestoreStatus, runRestore, connectDrive } from "../api/documents";

function fmtBytes(n) {
  if (!n) return "";
  const mb = n / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(n / 1024)} KB`;
}
function fmtDate(s) {
  if (!s) return "";
  try { return new Date(s).toLocaleString(); } catch { return s; }
}

const DISMISS_KEY = "docaiq.documents.restoreDismissed";

export default function RestorePrompt({ onRestored }) {
  const [status, setStatus] = useState(null);   // backend status payload
  const [phase, setPhase] = useState("idle");    // idle | running | done | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [password, setPassword] = useState("");
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    let live = true;
    fetchRestoreStatus()
      .then((s) => { if (live) setStatus(s); })
      .catch(() => { /* Drive not connected / not documents product — stay silent */ });
    return () => { live = false; };
  }, []);

  // Drive token expired/revoked (the backend flags driveAuthError on the restore
  // probe). Surface a one-click reconnect instead of letting Drive sync/backup
  // fail silently. Login itself is unaffected — this is purely a Drive nudge.
  const reconnect = async () => {
    setReconnecting(true);
    try {
      const r = await connectDrive();
      if (r?.authUrl) { window.location.href = r.authUrl; return; }
    } catch { /* fall through — let the user retry */ }
    setReconnecting(false);
  };
  if (status && status.driveAuthError && !dismissed) {
    return (
      <Bar tone="var(--amber)">
        <span>⚠️ Your Google Drive connection expired — reconnect to resume sync &amp; backup.
          Your documents and account are unaffected.</span>
        <span className="row gap-2" style={{ marginLeft: "auto" }}>
          <button style={btn("var(--amber)")} disabled={reconnecting} onClick={reconnect}>
            {reconnecting ? "Redirecting…" : "Reconnect Google Drive"}
          </button>
          <button style={btnGhost} onClick={() => setDismissed(true)}>Dismiss</button>
        </span>
      </Bar>
    );
  }

  if (!status || !status.available || dismissed) return null;
  if (phase === "done") {
    // Drive-first restore: originals are always re-imported. The snapshot extras
    // (classifications/chat) apply only when the backup was readable.
    const imported = result?.originalsSynced ?? 0;
    const undec = result?.undecryptable ?? 0;
    const snapBad = result?.snapshotStatus && result.snapshotStatus !== "applied";
    // Nothing imported AND files were encrypted with a lost key → tell the user
    // plainly to re-upload, instead of a silent success.
    if (imported === 0 && undec > 0) {
      return (
        <Bar tone="var(--rose)">
          <span>⚠️ {undec} file{undec === 1 ? "" : "s"} in your Drive are encrypted with a key that’s no
            longer available, so they couldn’t be imported. Re-upload the original files (Documents → upload)
            to recover them — new uploads are stored safely.</span>
          <button style={btn("var(--rose)")} onClick={() => { setDismissed(true); onRestored?.(); }}>Got it</button>
        </Bar>
      );
    }
    return (
      <Bar tone={snapBad ? "var(--amber)" : "var(--emerald)"}>
        {snapBad ? (
          <span>✓ Re-imported {imported} document{imported === 1 ? "" : "s"} from your Drive.
            {undec > 0 ? ` (${undec} older encrypted file${undec === 1 ? "" : "s"} couldn’t be read — re-upload those.)` : ""}
            {" "}Your saved backup couldn’t be read (made under an older key), so chat history and
            learned types weren’t restored.</span>
        ) : (
          <span>✓ Restored {imported ? `${imported} documents, ` : ""}
            {result?.restored?.typesRestored ?? 0} classifications
            {result?.restored?.chatRestored ? `, ${result.restored.chatRestored} chat messages` : ""}.</span>
        )}
        <button style={btn(snapBad ? "var(--amber)" : "var(--emerald)")}
          onClick={() => { setDismissed(true); onRestored?.(); }}>Done</button>
      </Bar>
    );
  }

  const emptyAccount = status.currentDocs === 0;
  const snap = status.snapshot; // counts present only when account is empty

  const doRestore = async () => {
    setPhase("running"); setError(null);
    try {
      const r = await runRestore(true, password || null);
      setResult(r); setPhase("done");
    } catch (e) {
      setError(e?.message || "Restore failed"); setPhase("error");
    }
  };
  const dismiss = () => {
    setDismissed(true);
    try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ }
  };

  // Disaster case → modal. Otherwise → slim banner.
  if (emptyAccount) {
    return (
      <Overlay>
        <div style={card}>
          <div className="serif" style={{ fontSize: 20, marginBottom: 6 }}>Restore from your Google Drive backup?</div>
          <p style={{ color: "var(--ink2)", fontSize: 13, margin: "0 0 14px", lineHeight: 1.5 }}>
            We found a backup in your Drive (<code>docaiq_docs/workspace</code>), last saved{" "}
            <b>{fmtDate(status.savedAt)}</b>{status.sizeBytes ? ` · ${fmtBytes(status.sizeBytes)}` : ""}.
            It can bring everything back into this account.
          </p>
          {snap && (
            <div style={statsBox}>
              <Stat n={snap.documents} label="documents" />
              <Stat n={snap.chunks} label="chunks" />
              <Stat n={snap.learnedTypes} label="learned types" />
              <Stat n={snap.chatMessages} label="chat msgs" />
            </div>
          )}
          {status.snapshotEncrypted && (
            <div style={{ margin: "4px 0 2px" }}>
              <div style={{ color: "var(--ink2)", fontSize: 12, marginBottom: 6 }}>
                🔒 This backup is password-protected. Enter your backup password to restore its extras
                (your documents re-import either way).
              </div>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                placeholder="Backup password" autoComplete="off"
                style={{ width: "100%", boxSizing: "border-box", padding: "9px 12px", borderRadius: 6,
                         fontSize: 13, background: "var(--bg2)", border: "1px solid var(--line)", color: "var(--ink)" }} />
            </div>
          )}
          {error && <div style={{ color: "var(--rose)", fontSize: 12, margin: "8px 0" }}>{error}</div>}
          <div className="row gap-3" style={{ marginTop: 16, justifyContent: "flex-end" }}>
            <button style={btnGhost} disabled={phase === "running"} onClick={dismiss}>Not now</button>
            <button style={btn("var(--gold2)")} disabled={phase === "running"} onClick={doRestore}>
              {phase === "running" ? "Restoring…" : "Restore everything"}
            </button>
          </div>
        </div>
      </Overlay>
    );
  }

  // Account already has documents → NO persistent top-bar banner. A restore is
  // still available on demand from Settings → "Your data in Drive". We only
  // interrupt for the disaster case (wiped account, handled above) and the
  // Drive-auth-expired nudge.
  return null;
}

const Stat = ({ n, label }) => (
  <div style={{ textAlign: "center" }}>
    <div className="mono" style={{ fontSize: 20 }}>{n ?? 0}</div>
    <div style={{ fontSize: 11, color: "var(--ink3)" }}>{label}</div>
  </div>
);

const Bar = ({ children, tone }) => (
  <div className="row gap-2" style={{
    alignItems: "center", padding: "8px 16px", fontSize: 13,
    borderBottom: "1px solid var(--line)", background: "var(--panel)", color: "var(--ink2)",
    borderLeft: `3px solid ${tone}`,
  }}>{children}</div>
);

const Overlay = ({ children }) => (
  <div style={{
    position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 1000,
    display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
  }}>{children}</div>
);

const card = {
  background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 16,
  padding: 24, maxWidth: 460, width: "100%", boxShadow: "0 20px 60px rgba(0,0,0,.4)",
};
const statsBox = {
  display: "flex", gap: 18, justifyContent: "space-around",
  padding: "12px 8px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)",
};
const btn = (bg) => ({
  background: bg, color: "#1a1408", border: "none", cursor: "pointer",
  borderRadius: 999, padding: "7px 16px", fontSize: 13, fontWeight: 600,
});
const btnGhost = {
  background: "none", color: "var(--ink2)", border: "1px solid var(--line)",
  cursor: "pointer", borderRadius: 999, padding: "7px 16px", fontSize: 13,
};
