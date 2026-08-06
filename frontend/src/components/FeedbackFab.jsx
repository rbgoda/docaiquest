import { useState } from "react";
import { submitFeedback } from "../api/documents";

// Persistent floating "Feedback" button — present on every page (mounted once in
// DocumentsApp). Opens a compact feedback form that posts to the same /feedback inbox
// as the user-menu "Send feedback" panel (LLM auto-triage on the backend). The full
// screenshot-rich form still lives in the user menu; this is the one-click, from-anywhere
// quick path the user asked for.

const CATS = [["bug", "🐞", "Bug"], ["idea", "💡", "Idea"], ["praise", "💚", "Praise"], ["other", "💬", "Other"]];
const COPY = {
  bug: ["What went wrong?", "What you expected vs. what happened…"],
  idea: ["Your idea", "What would you like to see?"],
  praise: ["What do you love?", "Tell us what's working well…"],
  other: ["What's on your mind?", "Anything you'd like to share…"],
};

const MAX_SHOTS = 3;
// Downscale + JPEG-compress in-browser so a 4MB shot → ~100KB data URL before it hits the
// network (same approach as the user-menu feedback form).
function fileToCompressedDataUrl(file, maxDim = 1280, quality = 0.7) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", quality));
      };
      img.onerror = reject; img.src = e.target.result;
    };
    reader.onerror = reject; reader.readAsDataURL(file);
  });
}

export default function FeedbackFab() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Send feedback"
        style={{
          position: "fixed", right: 20, bottom: 20, zIndex: 900,
          display: "flex", alignItems: "center", gap: 8,
          padding: "10px 16px", borderRadius: 999, border: "none", cursor: "pointer",
          background: "var(--gold, #E2BC68)", color: "#1a1710", fontWeight: 600, fontSize: 13,
          boxShadow: "0 6px 24px rgba(0,0,0,.28)",
        }}>
        <span style={{ fontSize: 16, lineHeight: 1 }}>💬</span> Feedback
      </button>
      {open && <FeedbackDialog onClose={() => setOpen(false)} />}
    </>
  );
}

export function FeedbackDialog({ onClose }) {
  const [category, setCategory] = useState("bug");
  const [comments, setComments] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [rating, setRating] = useState(0);
  const [shots, setShots] = useState([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState("");

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

  const addFiles = async (e) => {
    const files = Array.from(e.target.files || []); e.target.value = "";
    const room = MAX_SHOTS - shots.length;
    if (room <= 0) { setErr(`Up to ${MAX_SHOTS} screenshots.`); return; }
    try {
      const next = [];
      for (const f of files.slice(0, room)) next.push(await fileToCompressedDataUrl(f));
      setShots((s) => [...s, ...next]);
    } catch { setErr("Couldn't read that image."); }
  };

  const [descLabel, descPlaceholder] = COPY[category] || COPY.other;
  const field = { width: "100%", padding: "8px 10px", borderRadius: 8, color: "var(--ink)", fontSize: 13 };

  return (
    <div onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,.5)",
        display: "flex", alignItems: "flex-end", justifyContent: "flex-end", padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} className="bg1"
        style={{ width: 380, maxWidth: "94vw", maxHeight: "86vh", overflowY: "auto",
          borderRadius: 16, border: "1px solid var(--line)", padding: 20,
          boxShadow: "0 20px 60px rgba(0,0,0,.4)" }}>
        <div className="row between" style={{ alignItems: "center", marginBottom: 6 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>Send feedback</div>
          <button onClick={onClose} title="Close"
            style={{ background: "none", border: "none", color: "var(--ink3)", cursor: "pointer", fontSize: 20, lineHeight: 1 }}>×</button>
        </div>

        {done ? (
          <div style={{ textAlign: "center", padding: "22px 0" }}>
            <div style={{ fontSize: 34, marginBottom: 8 }}>🙏</div>
            <div style={{ color: "var(--ink)", fontWeight: 600, marginBottom: 4 }}>Thanks for the feedback!</div>
            <div className="ink3" style={{ fontSize: 12, marginBottom: 14 }}>We read every note — it helps us improve DocAIQ.</div>
            <button onClick={onClose} className="border bg2"
              style={{ padding: "8px 16px", borderRadius: 8, cursor: "pointer", fontSize: 13 }}>Close</button>
          </div>
        ) : (
          <>
            <div className="ink3" style={{ fontSize: 12, marginBottom: 12 }}>
              Bugs, ideas, anything — from any page.
            </div>
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
                placeholder={descPlaceholder} className="border bg2" style={{ ...field, resize: "vertical" }} />
            </div>
            <div style={{ marginBottom: 12 }}>
              <div className="ink3" style={{ fontSize: 11, marginBottom: 4 }}>
                Suggestion <span style={{ opacity: 0.6 }}>(optional)</span>
              </div>
              <textarea value={suggestion} onChange={(e) => setSuggestion(e.target.value)} rows={2}
                placeholder="e.g. 'add an undo button'…" className="border bg2" style={{ ...field, resize: "vertical" }} />
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
                      style={{ position: "absolute", top: -6, right: -6, width: 18, height: 18, borderRadius: 9, border: "none", background: "var(--rose, #D8625E)", color: "#fff", cursor: "pointer", fontSize: 11, lineHeight: "18px", padding: 0 }}>×</button>
                  </div>
                ))}
                {shots.length < MAX_SHOTS && (
                  <label className="border bg2" title="Attach a screenshot"
                    style={{ width: 48, height: 48, borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", fontSize: 20, color: "var(--ink3)" }}>
                    +<input type="file" accept="image/*" multiple onChange={addFiles} style={{ display: "none" }} />
                  </label>
                )}
              </div>
            </div>
            <div style={{ marginBottom: 14 }}>
              <div className="ink3" style={{ fontSize: 11, marginBottom: 4 }}>Overall rating <span style={{ opacity: 0.6 }}>(optional)</span></div>
              <div className="row" style={{ gap: 6 }}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} onClick={() => setRating(n === rating ? 0 : n)} type="button" title={`${n} star${n > 1 ? "s" : ""}`}
                    style={{ background: "none", border: "none", cursor: "pointer", fontSize: 24, lineHeight: 1, padding: 0,
                      filter: n <= rating ? "none" : "grayscale(1) opacity(0.4)" }}>⭐</button>
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
          </>
        )}
      </div>
    </div>
  );
}
