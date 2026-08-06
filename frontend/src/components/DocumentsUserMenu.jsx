// M46 · Documents · top-right user/settings menu.
//
// Concept borrowed from xpenseaiq-v5's UserMenu (avatar dropdown → portalled
// settings modal with per-section panels), customized to DocAIQ's editorial
// theme tokens and the Documents product's needs: the menu surfaces the signed-
// in Google account, and the Settings modal carries Profile / Appearance /
// Privacy panels plus shortcuts to Connectors and Sign out.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useConfirm } from "./ConfirmDialog.jsx";
import { fetchLearnedTypes, fetchTypeCandidates, applyTypeToDocs, exportMyData, eraseMyAccount, fetchWorkspaceStatus, syncWorkspace, fetchDriveStatus, setDriveEncryption, submitFeedback, redeemPromo, fetchRestoreStatus, runRestore } from "../api/documents";
import DocumentsGroups from "../views/DocumentsGroups.jsx";
import DocumentsConnectors from "../views/DocumentsConnectors.jsx";
import DeveloperKeys from "../views/DeveloperKeys.jsx";

const THEME_KEY = "docaiq.documents.theme";

function applyTheme(t) {
  document.body.className = t;
  try { localStorage.setItem(THEME_KEY, t); } catch { /* ignore */ }
}

const MENU = [
  { id: "profile", icon: "👤", label: "Profile" },
  { id: "appearance", icon: "🎨", label: "Appearance" },
  { id: "learned", icon: "🏷️", label: "Learned types" },
  { id: "workspace", icon: "🗄️", label: "Your data in Drive" },
  { id: "privacy", icon: "🛡️", label: "Privacy & data" },
  { id: "redeem", icon: "🎟️", label: "Redeem code" },
  { id: "feedback", icon: "💬", label: "Send feedback" },
];

// §5 · build + push the user's encrypted workspace to their own Google Drive.
function WorkspacePanel() {
  const [status, setStatus] = useState(undefined);
  const [busy, setBusy] = useState(false);
  const [encOn, setEncOn] = useState(null);     // per-user encrypt-files
  const [encBusy, setEncBusy] = useState(false);
  const [rstat, setRstat] = useState(undefined); // restore-from-backup status
  const [rbusy, setRbusy] = useState(false);
  const [rmsg, setRmsg] = useState(null);
  const [rpw, setRpw] = useState("");
  const confirmDialog = useConfirm();
  const load = () => fetchWorkspaceStatus().then(setStatus).catch(() => setStatus(null));
  useEffect(() => {
    load();
    fetchDriveStatus().then(d => setEncOn(!!d.encryptFiles)).catch(() => setEncOn(null));
    fetchRestoreStatus().then(setRstat).catch(() => setRstat(null));
  }, []);
  const doRestore = async () => {
    setRbusy(true); setRmsg(null);
    try {
      const r = await runRestore(true, rpw || null);
      const imported = r?.originalsSynced ?? 0;
      setRmsg(`✓ Restored ${imported} document${imported === 1 ? "" : "s"}` +
        (r?.restored?.typesRestored ? `, ${r.restored.typesRestored} classifications` : "") + ".");
    } catch (e) { setRmsg("Restore failed: " + (e?.message || "")); }
    finally { setRbusy(false); }
  };
  const toggleEnc = async () => {
    const turningOn = !encOn;
    if (turningOn) {
      const ok = await confirmDialog({
        title: "Encrypt your files in Google Drive?",
        body: "Your files stored in Drive will be replaced with encrypted versions — Google can't read them, and you'll only be able to open them through DocAIQuest (not directly in Drive). Existing files are re-encrypted now. You can turn this off later.",
        confirmLabel: "Encrypt my Drive files",
      });
      if (!ok) return;
    }
    setEncBusy(true);
    try { const r = await setDriveEncryption(turningOn); setEncOn(r.enabled);
      if (turningOn) alert(`Encrypted ${r.reencrypted} file(s) in your Drive${r.errors ? ` (${r.errors} failed)` : ""}.`); }
    catch (e) { alert("Failed: " + (e.message || "")); }
    finally { setEncBusy(false); }
  };
  const sync = async () => {
    setBusy(true);
    try { await syncWorkspace(); await load(); }
    catch (e) { alert("Sync failed: " + (e.message || "")); }
    finally { setBusy(false); }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="bg2 border rounded-md" style={{ padding: "12px 14px" }}>
        <div className="font-semibold" style={{ fontSize: 13 }}>Your workspace, in your Drive</div>
        <div className="ink3" style={{ fontSize: 12, marginTop: 4, lineHeight: 1.5 }}>
          We can package everything we hold for you — documents, extracted data,
          chat, and learned types — into a single <strong>encrypted</strong> file
          and store it in <strong>your own Google Drive</strong>
          (<code>docaiq_docs/.workspace/</code>). Only you (via DocAIQuest) can decrypt it.
        </div>
      </div>
      {status === undefined ? <div className="ink4" style={{ fontSize: 12 }}>Loading…</div>
        : status === null ? <div className="ink4" style={{ fontSize: 12 }}>Status unavailable.</div>
        : status.synced ? (
          <div className="ink3" style={{ fontSize: 12 }}>
            ✓ Last synced: {status.syncedAt ? new Date(status.syncedAt).toLocaleString() : "—"} ·
            {" "}{status.docCount} docs · {Math.round((status.sizeBytes || 0) / 1024)} KB
          </div>
        ) : <div className="ink4" style={{ fontSize: 12 }}>Not yet synced to your Drive.</div>}
      <button onClick={sync} disabled={busy} className="btn-gold" style={{ padding: "10px 14px", borderRadius: 10, fontSize: 13, opacity: busy ? 0.6 : 1 }}>
        {busy ? "Packaging + uploading…" : "Sync my workspace to Drive"}
      </button>

      {rstat && rstat.available && (
        <div className="bg2 border rounded-md" style={{ padding: "12px 14px", marginTop: 4 }}>
          <div className="font-semibold" style={{ fontSize: 13 }}>Restore from your Drive backup</div>
          <div className="ink3" style={{ fontSize: 12, marginTop: 4, lineHeight: 1.5 }}>
            A backup from <strong>{rstat.savedAt ? new Date(rstat.savedAt).toLocaleDateString() : "—"}</strong>
            {rstat.sizeBytes ? ` (${Math.round(rstat.sizeBytes / 1024)} KB)` : ""} is in your Drive.
            Restoring re-imports your original files{rstat.snapshotEncrypted ? "; enter your backup password to also bring back chat + learned types" : ""}.
          </div>
          {rstat.snapshotEncrypted && (
            <input type="password" value={rpw} onChange={(e) => setRpw(e.target.value)} placeholder="Backup password (optional)"
              autoComplete="off" style={{ width: "100%", boxSizing: "border-box", marginTop: 8, padding: "8px 10px", borderRadius: 8, fontSize: 12, background: "var(--bg2)", border: "1px solid var(--line)", color: "var(--ink)" }} />
          )}
          <button onClick={doRestore} disabled={rbusy} className="btn-gold" style={{ marginTop: 10, padding: "9px 14px", borderRadius: 10, fontSize: 13, opacity: rbusy ? 0.6 : 1 }}>
            {rbusy ? "Restoring…" : "Restore from backup"}
          </button>
          {rmsg && <div className="ink3" style={{ fontSize: 12, marginTop: 8 }}>{rmsg}</div>}
        </div>
      )}

      <div className="bg2 border rounded-md" style={{ padding: "12px 14px", marginTop: 4 }}>
        <label className="row between" style={{ alignItems: "center", cursor: "pointer", gap: 10 }}>
          <span>
            <div className="font-semibold" style={{ fontSize: 13 }}>Encrypt my Drive files</div>
            <div className="ink3" style={{ fontSize: 11, marginTop: 2, lineHeight: 1.5 }}>
              Replace your files in Drive with encrypted versions. Google can't read them;
              you open them via DocAIQuest only (not directly in Drive).
            </div>
          </span>
          <input type="checkbox" checked={!!encOn} disabled={encBusy || encOn === null}
            onChange={toggleEnc} style={{ accentColor: "var(--gold2)", cursor: "pointer", transform: "scale(1.3)", flexShrink: 0 }} />
        </label>
        {encBusy && <div className="ink4" style={{ fontSize: 11, marginTop: 6 }}>Re-encrypting your Drive files…</div>}
      </div>
      <div className="ink4" style={{ fontSize: 11 }}>
        Experimental. Your data also stays in DocAIQuest for now — this is the first
        step toward DocAIQuest keeping nothing durable.
      </div>
    </div>
  );
}

// §2 · the user's self-learned doc-type vocabulary + apply-to-similar.
function LearnedTypesPanel() {
  const [types, setTypes] = useState(null);
  const [cands, setCands] = useState({});   // {slug: []|"loading"|"done:N"}
  useEffect(() => { fetchLearnedTypes().then(r => setTypes(r.types || [])).catch(() => setTypes([])); }, []);
  const findSimilar = async (slug) => {
    setCands(c => ({ ...c, [slug]: "loading" }));
    try { const r = await fetchTypeCandidates(slug); setCands(c => ({ ...c, [slug]: r.candidates || [] })); }
    catch { setCands(c => ({ ...c, [slug]: [] })); }
  };
  const applyAll = async (slug, list) => {
    try { const r = await applyTypeToDocs(slug, list.map(x => x.docId)); setCands(c => ({ ...c, [slug]: "done:" + r.count })); }
    catch { /* ignore */ }
  };
  if (types === null) return <div className="ink3" style={{ fontSize: 13 }}>Loading…</div>;
  if (!types.length) return <div className="ink3" style={{ fontSize: 13, fontStyle: "italic" }}>No learned types yet. As your documents are classified, the vocabulary grows here.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="ink3" style={{ fontSize: 12 }}>Types DocAIQuest learned from your docs. <strong>Distilled</strong> = similar docs auto-type with no AI call.</div>
      {types.map(t => {
        const c = cands[t.slug];
        return (
          <div key={t.slug} className="bg2 border rounded-md" style={{ padding: "10px 12px" }}>
            <div className="row between" style={{ alignItems: "center" }}>
              <div>
                <span className="font-medium" style={{ fontSize: 13 }}>{(t.label || t.slug)}</span>
                <span className="mono ink4 ml-2" style={{ fontSize: 10 }}>{t.slug}</span>
              </div>
              <div className="row gap-2" style={{ alignItems: "center", fontSize: 11 }}>
                <span className="mono ink3">×{t.seenCount}</span>
                <span style={{ color: t.source === "human" ? "var(--gold2)" : "var(--violet)" }}>{t.source === "human" ? "you" : "AI"}</span>
                {t.distilled && <span className="ink4">{"· auto ✓"}</span>}
                {t.distilled && <button onClick={() => findSimilar(t.slug)} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, color: "var(--gold2)" }}>find similar</button>}
              </div>
            </div>
            {c === "loading" && <div className="ink4" style={{ fontSize: 11, marginTop: 6 }}>Searching…</div>}
            {typeof c === "string" && c.startsWith("done:") && <div className="ink3" style={{ fontSize: 11, marginTop: 6 }}>✓ Applied to {c.slice(5)} doc(s).</div>}
            {Array.isArray(c) && (c.length === 0
              ? <div className="ink4" style={{ fontSize: 11, marginTop: 6 }}>No untyped docs look like this.</div>
              : <div style={{ marginTop: 8 }}>
                  <div className="row between" style={{ alignItems: "center", marginBottom: 4 }}>
                    <span className="ink3" style={{ fontSize: 11 }}>{c.length} look like this</span>
                    <button onClick={() => applyAll(t.slug, c)} className="btn-gold" style={{ padding: "3px 8px", borderRadius: 5, fontSize: 11 }}>Apply to all {c.length}</button>
                  </div>
                  {c.map(x => <div key={x.docId} className="row between" style={{ fontSize: 11 }}><span className="truncate ink2">{x.name}</span><span className="mono ink4">{Math.round(x.similarity * 100)}%</span></div>)}
                </div>)}
          </div>
        );
      })}
    </div>
  );
}

// ── Settings panels ─────────────────────────────────────────────────────────
function ProfilePanel({ user }) {
  const { updateProfile } = useAuth();
  const currentName = user?.name || user?.email?.split("@")[0] || "You";
  const initial = (currentName || "U").charAt(0).toUpperCase();
  const email = user?.email || "";
  // Google accounts use the gmail address as the stable identity; the local
  // part is just the seed for the default display name.
  const isGoogle = !user?.hasPassword;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(currentName);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [savedAt, setSavedAt] = useState(false);

  const startEdit = () => { setDraft(user?.name || ""); setErr(""); setEditing(true); };
  const cancel = () => { setEditing(false); setErr(""); };
  const save = async () => {
    const next = draft.trim();
    if (!next) { setErr("Name can't be empty."); return; }
    setSaving(true); setErr("");
    try {
      await updateProfile({ name: next });
      setEditing(false); setSavedAt(true);
      setTimeout(() => setSavedAt(false), 2200);
    } catch (e) {
      setErr(e?.message || "Couldn't save. Try again.");
    } finally { setSaving(false); }
  };

  const row = (label, value) => (
    <div className="row between" style={{ alignItems: "center", padding: "11px 0", borderTop: "1px solid var(--line)" }}>
      <span className="ink3" style={{ fontSize: 12 }}>{label}</span>
      <span className="truncate" style={{ fontSize: 13, color: "var(--ink)", maxWidth: 240, textAlign: "right" }}>{value}</span>
    </div>
  );

  return (
    <div>
      <div className="row gap-3" style={{ alignItems: "center", marginBottom: 6 }}>
        <div style={{ width: 52, height: 52, borderRadius: "50%", background: "linear-gradient(135deg,#C8A04C,#E2BC68)",
          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, fontWeight: 700, color: "#1a1408", flexShrink: 0 }}>
          {initial}
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="serif" style={{ fontSize: 18 }}>{currentName}</div>
          <div className="ink3 truncate" style={{ fontSize: 12 }}>{email}</div>
        </div>
      </div>

      {/* Display name — editable */}
      <div style={{ marginTop: 10 }}>
        <div className="row between" style={{ alignItems: "center", marginBottom: 6 }}>
          <span className="ink3" style={{ fontSize: 12, fontWeight: 700 }}>Display name</span>
          {!editing && (
            <button onClick={startEdit} className="row gap-1" style={{ alignItems: "center", background: "none", border: "none", cursor: "pointer", color: "var(--gold2)", fontSize: 12 }}>
              <Icon name="pen" size={12} /> Edit
            </button>
          )}
        </div>
        {editing ? (
          <div>
            <input value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus maxLength={200}
              onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") cancel(); }}
              className="bg2 border" style={{ width: "100%", padding: "9px 12px", borderRadius: 8, fontSize: 13, color: "var(--ink)", outline: "none", boxSizing: "border-box" }} />
            <div className="row gap-2" style={{ marginTop: 8 }}>
              <button onClick={save} disabled={saving} className="btn-gold" style={{ padding: "7px 14px", borderRadius: 8, fontSize: 12, opacity: saving ? 0.6 : 1 }}>
                {saving ? "Saving…" : "Save"}
              </button>
              <button onClick={cancel} disabled={saving} className="border bg2" style={{ padding: "7px 12px", borderRadius: 8, fontSize: 12 }}>Cancel</button>
            </div>
          </div>
        ) : (
          <div className="bg2 border rounded-md row between" style={{ alignItems: "center", padding: "9px 12px" }}>
            <span style={{ fontSize: 13, color: "var(--ink)" }}>{currentName}</span>
            {savedAt && <span className="mono" style={{ fontSize: 10, color: "var(--emerald)" }}>✓ saved</span>}
          </div>
        )}
        {err && <div style={{ fontSize: 11, color: "var(--rose)", marginTop: 6 }}>{err}</div>}
      </div>

      <div style={{ marginTop: 12 }}>
        {row("Signed in with", isGoogle ? "Google" : "Email + password")}
        {row("Email (profile ID)", email || "—")}
        {row("Role", user?.role || (user?.roles?.[0]) || "owner")}
        {row("Workspace", "Private to you")}
      </div>
      <div className="ink4" style={{ fontSize: 11, marginTop: 14, lineHeight: 1.5 }}>
        Your email is your DocAIQuest profile ID — it's how your private workspace is keyed. Documents, extractions, and chats are isolated to this account; no one else can see them.
      </div>
    </div>
  );
}

// Downscale + JPEG-compress a screenshot in the browser so a 4MB image becomes a
// ~100KB data URL before it hits the network (same approach as ChatFeedbackModal).
function fileToCompressedDataUrl(file, maxDim = 1280, quality = 0.7) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * scale);
        c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", quality));
      };
      img.onerror = reject;
      img.src = e.target.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ── 'Redeem code' panel — enter a promo code → upgrade the plan ─────────────
function RedeemPanel() {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState(null);
  const [err, setErr] = useState("");
  const submit = async () => {
    const c = (code || "").trim();
    if (!c || busy) return;
    setBusy(true); setErr(""); setOk(null);
    try { setOk(await redeemPromo(c)); }
    catch (e) { setErr(e?.message || "Couldn't redeem that code."); }
    finally { setBusy(false); }
  };
  if (ok) return (
    <div style={{ textAlign: "center", padding: "22px 0" }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>🎉</div>
      <div style={{ color: "var(--ink)", fontWeight: 600, marginBottom: 4 }}>
        You're on <b>{ok.plan}</b> for {ok.durationDays} days!
      </div>
      <div className="ink3" style={{ fontSize: 12, marginBottom: 14 }}>Reload to see your new plan &amp; limits.</div>
      <button onClick={() => window.location.reload()} className="btn-gold"
        style={{ padding: "8px 18px", borderRadius: 8, fontSize: 13, cursor: "pointer" }}>Reload</button>
    </div>
  );
  return (
    <div>
      <div className="ink3" style={{ fontSize: 12, marginBottom: 12 }}>
        Have a promo code? Enter it to upgrade your plan.
      </div>
      <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
        onKeyDown={(e) => e.key === "Enter" && submit()} placeholder="PROMO CODE"
        className="border bg2" style={{ width: "100%", padding: "10px 12px", borderRadius: 8,
          color: "var(--ink)", fontSize: 14, letterSpacing: 1.5, textTransform: "uppercase" }} />
      {err && <div style={{ color: "var(--rose, #D8625E)", fontSize: 12, marginTop: 8 }}>{err}</div>}
      <button onClick={submit} disabled={busy || !code.trim()}
        style={{ width: "100%", marginTop: 12, padding: "10px 14px", borderRadius: 10, border: "none",
          cursor: (busy || !code.trim()) ? "default" : "pointer", background: "var(--gold)",
          color: "#1a1710", fontWeight: 600, fontSize: 13, opacity: (busy || !code.trim()) ? 0.6 : 1 }}>
        {busy ? "Redeeming…" : "Redeem"}
      </button>
    </div>
  );
}


// ── 'Send feedback' panel (app-level product feedback) ──────────────────────
function FeedbackPanel() {
  const [rating, setRating] = useState(0);
  const [category, setCategory] = useState("bug");
  const [comments, setComments] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [shots, setShots] = useState([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState("");
  const MAX_SHOTS = 3;
  const addFiles = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    const room = MAX_SHOTS - shots.length;
    if (room <= 0) { setErr(`Up to ${MAX_SHOTS} screenshots.`); return; }
    try {
      const next = [];
      for (const f of files.slice(0, room)) next.push(await fileToCompressedDataUrl(f));
      setShots((s) => [...s, ...next]);
    } catch { setErr("Couldn't read that image."); }
  };
  const submit = async () => {
    if (!rating && !comments.trim() && !suggestion.trim()) {
      setErr("Add a rating, a comment, or a suggestion."); return;
    }
    setBusy(true); setErr("");
    try {
      await submitFeedback({
        rating: rating || null, category,
        comments: comments.trim() || null, suggestion: suggestion.trim() || null,
        screenshots: shots.length ? shots : null,
        page: (document.body.dataset.view || "").slice(0, 64),
        appVersion: "docaiq-web", deviceInfo: (navigator.userAgent || "").slice(0, 200),
      });
      setDone(true);
    } catch (e) { setErr(e?.message || "Failed to send feedback"); }
    finally { setBusy(false); }
  };
  if (done) return (
    <div style={{ textAlign: "center", padding: "24px 0" }}>
      <div style={{ fontSize: 34, marginBottom: 8 }}>🙏</div>
      <div style={{ color: "var(--ink)", fontWeight: 600, marginBottom: 4 }}>Thanks for the feedback!</div>
      <div className="ink3" style={{ fontSize: 12 }}>We read every note — it helps us improve DocAIQuest.</div>
    </div>
  );
  const CATS = [["bug", "🐞", "Bug"], ["idea", "💡", "Idea"], ["praise", "💚", "Praise"], ["other", "💬", "Other"]];
  const COPY = {
    bug: ["What went wrong?", "Steps to reproduce, what you expected vs. what happened…"],
    idea: ["Your idea", "What would you like to see? What problem would it solve?"],
    praise: ["What do you love?", "Tell us what's working well for you…"],
    other: ["What's on your mind?", "Anything else you'd like to share…"],
  };
  const [descLabel, descPlaceholder] = COPY[category] || COPY.other;
  const field = { width: "100%", padding: "8px 10px", borderRadius: 8, color: "var(--ink)", fontSize: 13 };
  return (
    <div>
      <div className="ink3" style={{ fontSize: 12, marginBottom: 12 }}>
        Tell us how DocAIQuest is working for you — bugs, ideas, anything.
      </div>
      {/* category tabs (Bug / Idea / Praise / Other) */}
      <div className="row" style={{ gap: 8, marginBottom: 14 }}>
        {CATS.map(([v, ic, l]) => {
          const on = category === v;
          return (
            <button key={v} onClick={() => setCategory(v)} type="button"
              style={{ flex: 1, padding: "10px 4px", borderRadius: 10, cursor: "pointer",
                background: on ? "var(--gold-soft, rgba(226,188,104,.12))" : "var(--bg2, rgba(255,255,255,.03))",
                border: `1px solid ${on ? "var(--gold, #E2BC68)" : "var(--line, rgba(255,255,255,.12))"}`,
                color: on ? "var(--gold, #E2BC68)" : "var(--ink2)", fontWeight: on ? 600 : 500, fontSize: 12,
                display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <span style={{ fontSize: 18 }}>{ic}</span>{l}
            </button>
          );
        })}
      </div>
      <div style={{ marginBottom: 12 }}>
        <div className="ink3" style={{ fontSize: 11, marginBottom: 4, fontWeight: 600 }}>{descLabel}</div>
        <textarea value={comments} onChange={(e) => setComments(e.target.value)} rows={4}
          placeholder={descPlaceholder}
          className="border bg2" style={{ ...field, resize: "vertical" }} />
      </div>
      <div style={{ marginBottom: 12 }}>
        <div className="ink3" style={{ fontSize: 11, marginBottom: 4 }}>
          Suggestion <span style={{ opacity: 0.6 }}>(what would fix or improve this?)</span>
        </div>
        <textarea value={suggestion} onChange={(e) => setSuggestion(e.target.value)} rows={2}
          placeholder="Optional. e.g. 'add an undo button', 'remember last category'…"
          className="border bg2" style={{ ...field, resize: "vertical" }} />
      </div>
      <div style={{ marginBottom: 12 }}>
        <div className="ink3" style={{ fontSize: 11, marginBottom: 4 }}>
          Screenshots <span style={{ opacity: 0.6 }}>(optional, up to {MAX_SHOTS})</span>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          {shots.map((src, i) => (
            <div key={i} style={{ position: "relative", width: 48, height: 48 }}>
              <img src={src} alt="" style={{ width: 48, height: 48, objectFit: "cover", borderRadius: 6, border: "1px solid var(--line)" }} />
              <button onClick={() => setShots((s) => s.filter((_, j) => j !== i))} title="Remove"
                style={{ position: "absolute", top: -6, right: -6, width: 18, height: 18, borderRadius: 9,
                  border: "none", background: "var(--rose, #D8625E)", color: "#fff", cursor: "pointer", fontSize: 11, lineHeight: "18px", padding: 0 }}>×</button>
            </div>
          ))}
          {shots.length < MAX_SHOTS && (
            <label className="border bg2" style={{ width: 48, height: 48, borderRadius: 6, display: "flex",
              alignItems: "center", justifyContent: "center", cursor: "pointer", fontSize: 20, color: "var(--ink3)" }}>
              +<input type="file" accept="image/*" multiple onChange={addFiles} style={{ display: "none" }} />
            </label>
          )}
        </div>
      </div>
      <div style={{ marginBottom: 14 }}>
        <div className="ink3" style={{ fontSize: 11, marginBottom: 4 }}>
          Overall rating <span style={{ opacity: 0.6 }}>(optional)</span>
        </div>
        <div className="row" style={{ gap: 6 }}>
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} onClick={() => setRating(n === rating ? 0 : n)} type="button" title={`${n} star${n > 1 ? "s" : ""}`}
              style={{ background: "none", border: "none", cursor: "pointer", fontSize: 26, lineHeight: 1,
                padding: 0, filter: n <= rating ? "none" : "grayscale(1) opacity(0.4)" }}>⭐</button>
          ))}
        </div>
      </div>
      {err && <div style={{ color: "var(--rose, #D8625E)", fontSize: 12, marginBottom: 8 }}>{err}</div>}
      <button onClick={submit} disabled={busy}
        style={{ width: "100%", padding: "10px 14px", borderRadius: 10, border: "none",
          cursor: busy ? "default" : "pointer", background: "var(--gold)", color: "#1a1710",
          fontWeight: 600, fontSize: 13, opacity: busy ? 0.6 : 1 }}>
        {busy ? "Sending…" : "Submit feedback"}
      </button>
    </div>
  );
}


function AppearancePanel() {
  const [theme, setTheme] = useState(() => (document.body.className === "light" ? "light" : "dark"));
  const pick = (t) => { setTheme(t); applyTheme(t); };
  const card = (id, label, desc, swatch) => {
    const active = theme === id;
    return (
      <button onClick={() => pick(id)}
        className={active ? "" : "border bg2"}
        style={{ flex: 1, textAlign: "left", padding: 14, borderRadius: 12, cursor: "pointer",
          border: active ? "2px solid var(--gold)" : undefined, background: active ? "rgba(200,160,76,0.10)" : undefined }}>
        <div style={{ height: 46, borderRadius: 8, marginBottom: 10, border: "1px solid var(--line)", background: swatch }} />
        <div className="row between" style={{ alignItems: "center" }}>
          <span style={{ fontSize: 13, color: "var(--ink)", fontWeight: 600 }}>{label}</span>
          {active && <span className="mono" style={{ fontSize: 10, color: "var(--gold2)" }}>✓ active</span>}
        </div>
        <div className="ink3" style={{ fontSize: 11, marginTop: 2 }}>{desc}</div>
      </button>
    );
  };
  return (
    <div>
      <div className="ink3" style={{ fontSize: 12, marginBottom: 12 }}>Choose how DocAIQuest looks. Saved to this browser.</div>
      <div className="row gap-3">
        {card("dark", "Dark", "Editorial default", "linear-gradient(135deg,#15130f,#211c14)")}
        {card("light", "Light", "Warm paper cream", "linear-gradient(135deg,#F4EFE6,#E8E0D2)")}
      </div>
    </div>
  );
}

function PrivacyPanel({ onOpenConnectors }) {
  const item = (icon, title, body) => (
    <div className="bg2 border rounded-md" style={{ padding: "12px 14px", display: "flex", gap: 11, alignItems: "flex-start" }}>
      <span style={{ fontSize: 16, lineHeight: 1.3 }}>{icon}</span>
      <div>
        <div style={{ fontSize: 13, color: "var(--ink)", fontWeight: 600 }}>{title}</div>
        <div className="ink3" style={{ fontSize: 12, marginTop: 2, lineHeight: 1.5 }}>{body}</div>
      </div>
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {item("🔒", "Per-user isolation", "Every document, extraction, and answer is scoped to your account at the database layer.")}
      {item("🛡️", "PII-safe AI", "Personal identifiers are redacted before any text is sent to an LLM, and the redactions are logged to an audit ledger.")}
      {item("🗂️", "Your data, your control", "Your original files stay in your own Google Drive. The actions below cover the data DocAIQuest processes server-side.")}
      <DataRightsActions />
      {onOpenConnectors && (
        <button onClick={onOpenConnectors} className="border bg2 hover-bg row gap-2"
          style={{ alignItems: "center", justifyContent: "center", padding: "10px 14px", borderRadius: 10, fontSize: 13, marginTop: 2 }}>
          <Icon name="link" size={14} /> Manage connected sources (Google Drive)
        </button>
      )}
    </div>
  );
}

// §compliance · real DSAR export + account erasure (GDPR Arts 15/17/20).
function DataRightsActions() {
  const { logout } = useAuth();
  const confirmDialog = useConfirm();
  const [busy, setBusy] = useState(null);
  const onExport = async () => {
    setBusy("export");
    try {
      const data = await exportMyData();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a"); a.href = url; a.download = "docaiquest-my-data.json"; a.click(); URL.revokeObjectURL(url);
    } catch (e) { alert("Export failed: " + (e.message || "")); } finally { setBusy(null); }
  };
  const onErase = async () => {
    const ok = await confirmDialog({ title: "Delete your account and all data?", body: "This permanently erases every document, chat, learned type, and group you own, plus your account. Your Google Drive files are NOT touched. This cannot be undone.", confirmLabel: "Delete everything", destructive: true });
    if (!ok) return;
    setBusy("erase");
    try { await eraseMyAccount(); logout(); } catch (e) { alert("Deletion failed: " + (e.message || "")); setBusy(null); }
  };
  return (
    <div className="row gap-2" style={{ flexWrap: "wrap" }}>
      <button onClick={onExport} disabled={busy} className="border bg2" style={{ padding: "9px 14px", borderRadius: 10, fontSize: 13 }}>
        {busy === "export" ? "Preparing…" : "⬇ Download my data (JSON)"}
      </button>
      <button onClick={onErase} disabled={busy} style={{ padding: "9px 14px", borderRadius: 10, fontSize: 13, background: "var(--rose)", color: "#fff", border: "none", cursor: "pointer", opacity: busy ? 0.6 : 1 }}>
        {busy === "erase" ? "Deleting…" : "Delete account & all data"}
      </button>
    </div>
  );
}

// ── Menu + modal shell ───────────────────────────────────────────────────────
export default function DocumentsUserMenu({ user, onSignOut, onOpenConnectors, onOpenDeveloper, onOpenGroups, onOpenDocuments, compact }) {
  const { isCloud } = useAuth();
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState(null); // null | "profile" | "appearance" | "privacy"
  const ref = useRef(null);
  // P2 · hide cloud-only menu items in OSS deployments
  const visibleMenu = MENU.filter(m => m.id !== "redeem" || isCloud);

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") { setPanel(null); setOpen(false); } };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, []);

  const email = user?.email || "";
  const name = user?.name || email.split("@")[0] || "User";
  const initial = (name || "U").charAt(0).toUpperCase();
  const openPanel = (p) => { setPanel(p); setOpen(false); };
  // Kept for backward compat (mobile fallback) and for inner panel nav
  const goView = (v) => { setPanel(null); setOpen(false); if (v === "documents") onOpenDocuments?.(); else if (v === "groups") onOpenGroups?.(); else if (v === "connectors") onOpenConnectors?.(); else if (v === "developer") onOpenDeveloper?.(); };

  const PANELS = {
    profile:    { icon: "👤", label: "Profile",        w: 480 },
    appearance: { icon: "🎨", label: "Appearance",     w: 480 },
    learned:    { icon: "🏷️", label: "Learned types",  w: 480 },
    workspace:  { icon: "🗄️", label: "Your data in Drive", w: 480 },
    privacy:    { icon: "🛡️", label: "Privacy & data",  w: 480 },
    redeem:     { icon: "🎟️", label: "Redeem code",    w: 480 },
    feedback:   { icon: "💬", label: "Send feedback",  w: 480 },
    groups:     { icon: "👥", label: "Groups",          w: 640 },
    connectors: { icon: "🔗", label: "Connectors",      w: 680 },
    developer:  { icon: "🔑", label: "API keys",        w: 620 },
  };
  const pi = PANELS[panel] || {};

  return (
    <>
      <div ref={ref} style={{ position: "relative" }}>
        <button onClick={() => setOpen((v) => !v)} className="border bg2 hover-bg"
          style={{ display: "flex", alignItems: "center", gap: 8, borderRadius: 999, padding: compact ? "4px" : "4px 10px 4px 4px", cursor: "pointer", color: "var(--ink2)" }}
          title="Account & settings">
          <span style={{ width: 26, height: 26, borderRadius: "50%", background: "linear-gradient(135deg,#C8A04C,#E2BC68)",
            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, color: "#1a1408", flexShrink: 0 }}>{initial}</span>
          {!compact && <span className="truncate" style={{ fontSize: 12, maxWidth: 180 }}>{email}</span>}
          {!compact && <span className="ink3" style={{ fontSize: 10 }}>{open ? "▲" : "▾"}</span>}
        </button>

        {open && (
          <div className="bg1 border" style={{ position: "absolute", right: 0, top: "calc(100% + 8px)", minWidth: 248,
            borderRadius: 12, boxShadow: "0 16px 48px rgba(0,0,0,0.4)", zIndex: 200, overflow: "hidden", padding: "6px 0" }}>
            <div style={{ padding: "10px 16px 10px", borderBottom: "1px solid var(--line)" }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{name}</div>
              <div className="ink3 truncate" style={{ fontSize: 11, marginTop: 2 }}>{email}</div>
            </div>
            {visibleMenu.map((m) => (
              <button key={m.id} onClick={() => openPanel(m.id)} className="hover-bg"
                style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 16px",
                  background: "none", border: "none", color: "var(--ink2)", fontSize: 13, cursor: "pointer", textAlign: "left" }}>
                <span style={{ fontSize: 15, width: 20, textAlign: "center" }}>{m.icon}</span>{m.label}
              </button>
            ))}
            <button onClick={() => openPanel("groups")} className="hover-bg"
              style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 16px",
                background: "none", border: "none", color: "var(--ink2)", fontSize: 13, cursor: "pointer", textAlign: "left" }}>
              <span style={{ fontSize: 15, width: 20, textAlign: "center" }}>👥</span>Groups
            </button>
            <button onClick={() => openPanel("connectors")} className="hover-bg"
              style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 16px",
                background: "none", border: "none", color: "var(--ink2)", fontSize: 13, cursor: "pointer", textAlign: "left" }}>
              <span style={{ fontSize: 15, width: 20, textAlign: "center" }}>🔗</span>Connectors
            </button>
            <button onClick={() => openPanel("developer")} className="hover-bg"
              style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 16px",
                background: "none", border: "none", color: "var(--ink2)", fontSize: 13, cursor: "pointer", textAlign: "left" }}>
              <span style={{ fontSize: 15, width: 20, textAlign: "center" }}>🔑</span>API keys
            </button>
            <div style={{ borderTop: "1px solid var(--line)", marginTop: 4, paddingTop: 4 }}>
              <button onClick={() => { setOpen(false); onSignOut?.(); }} className="hover-bg"
                style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 16px",
                  background: "none", border: "none", color: "var(--rose)", fontSize: 13, cursor: "pointer", textAlign: "left" }}>
                <span style={{ fontSize: 15, width: 20, textAlign: "center" }}>↪</span>Sign out
              </button>
            </div>
          </div>
        )}
      </div>

      {panel && createPortal(
        <div style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.6)",
          display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }} onClick={() => setPanel(null)}>
          <div className="bg1 border" role="dialog" aria-modal="true" aria-label={pi.label || "Settings"} style={{ borderRadius: 16, width: "100%", maxWidth: pi.w || 480, maxHeight: "88vh",
            display: "flex", flexDirection: "column", boxShadow: "0 24px 80px rgba(0,0,0,0.5)" }} onClick={(e) => e.stopPropagation()}>
            <div className="row between p-3 border-b" style={{ alignItems: "center" }}>
              <div className="row gap-2" style={{ alignItems: "center" }}>
                <span style={{ fontSize: 17 }}>{pi.icon}</span>
                <span className="serif" style={{ fontSize: 16 }}>{pi.label}</span>
              </div>
              <button onClick={() => setPanel(null)} className="ink3"
                style={{ background: "none", border: "none", fontSize: 19, cursor: "pointer", lineHeight: 1 }}>✕</button>
            </div>
            <div style={{ padding: 18, overflowY: "auto" }}>
              {panel === "profile" && <ProfilePanel user={user} />}
              {panel === "appearance" && <AppearancePanel />}
              {panel === "learned" && <LearnedTypesPanel />}
              {panel === "workspace" && <WorkspacePanel />}
              {panel === "privacy" && <PrivacyPanel onOpenConnectors={() => openPanel("connectors")} />}
              {panel === "redeem" && <RedeemPanel />}
              {panel === "feedback" && <FeedbackPanel />}
              {panel === "groups" && <DocumentsGroups onOpenDocuments={() => goView("documents")} />}
              {panel === "connectors" && <DocumentsConnectors onSynced={() => setPanel(null)} />}
              {panel === "developer" && <DeveloperKeys />}
            </div>
          </div>
        </div>, document.body)}
    </>
  );
}
