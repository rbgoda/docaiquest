// M46 · ChatFeedbackModal — the "box style" feedback form, modelled on
// xpenseaiq-v5's FeedbackForm (category chips → comments → suggestion → rating
// → submit), adapted to a chat *answer* and DocAIQ's editorial theme tokens.
//
// Opened from ChatFeedback when the reviewer clicks 👎. Submits the structured
// signal to POST /api/chat-feedback, which logs it to the improvement queue and
// demotes the answer in the reflexion cache so it isn't reused.
import React, { useEffect, useState } from "react";
import { submitChatFeedback } from "../api/documents";

const MAX_IMAGES = 3;

// Downscale + JPEG-compress in the browser so a 4MB screenshot becomes a
// ~100KB data URL before it ever hits the network. (Same approach as
// xpenseaiq's FeedbackForm.)
function fileToCompressedDataUrl(file, maxDim = 1280, quality = 0.7) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
        const w = Math.round(img.width * scale);
        const h = Math.round(img.height * scale);
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        resolve(c.toDataURL("image/jpeg", quality));
      };
      img.onerror = reject;
      img.src = e.target.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// Chat-tailored categories (vs xpenseaiq's Bug/Idea/Praise/Other) — what was
// wrong with *this answer*.
const CATEGORIES = [
  { id: "wrong",      icon: "⚠️", label: "Wrong" },
  { id: "incomplete", icon: "➕", label: "Incomplete" },
  { id: "offtopic",   icon: "🎯", label: "Off-topic" },
  { id: "other",      icon: "💬", label: "Other" },
];

const COMMENT_LABEL = {
  wrong:      "What was wrong?",
  incomplete: "What was missing?",
  offtopic:   "What did you actually ask for?",
  other:      "Comments",
};
const COMMENT_PLACEHOLDER = {
  wrong:      "What did the answer get wrong? What's the correct fact?",
  incomplete: "What did it leave out — which document or detail?",
  offtopic:   "What were you expecting it to answer instead?",
  other:      "Tell us what happened — the more specific, the more we can act on it.",
};

export default function ChatFeedbackModal({ messagePk, onClose, onSubmitted }) {
  const [category, setCategory] = useState("wrong");
  const [comments, setComments] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [rating, setRating] = useState(0);
  const [screenshots, setScreenshots] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [err, setErr] = useState("");

  // a11y · close on Escape (matches DocumentsUserMenu / AllDocuments overlay).
  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") onClose?.(); };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [onClose]);

  const canSubmit = !submitting && (comments.trim() || suggestion.trim() || rating > 0 || screenshots.length);

  const handleAddImages = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    const remaining = MAX_IMAGES - screenshots.length;
    if (remaining <= 0) { setErr(`Up to ${MAX_IMAGES} screenshots.`); return; }
    setErr("");
    const next = [];
    for (const f of files.slice(0, remaining)) {
      try { next.push(await fileToCompressedDataUrl(f)); } catch { /* skip bad image */ }
    }
    setScreenshots((prev) => [...prev, ...next]);
  };

  const handleSubmit = async () => {
    setSubmitting(true); setErr("");
    try {
      await submitChatFeedback({
        messagePk,
        direction: "down",
        category,
        feedback: comments.trim() || null,
        suggestion: suggestion.trim() || null,
        rating: rating || null,
        screenshots: screenshots.length ? screenshots : null,
      });
      setSubmitted(true);
      onSubmitted?.();
    } catch (e) {
      setErr("Failed to submit: " + (e?.message || "unknown error"));
    } finally {
      setSubmitting(false);
    }
  };

  const overlay = {
    position: "fixed", inset: 0, zIndex: 9999, background: "rgba(0,0,0,0.62)",
    display: "flex", alignItems: "center", justifyContent: "center", padding: 12,
  };

  if (submitted) {
    return (
      <div style={overlay} onClick={onClose}>
        <div className="bg1 border" style={{ borderRadius: 16, padding: 32, maxWidth: 380, textAlign: "center" }}
          onClick={(e) => e.stopPropagation()}>
          <div style={{ fontSize: 44, marginBottom: 10 }}>🎉</div>
          <div className="font-semibold" style={{ fontSize: 17, marginBottom: 6 }}>Thank you!</div>
          <div className="ink3" style={{ fontSize: 13, marginBottom: 18, lineHeight: 1.5 }}>
            Feedback logged. We use every one to tune extraction and answers — and this answer won't be reused.
          </div>
          <button onClick={onClose} className="btn-gold" style={{ padding: "9px 22px", borderRadius: 10, fontSize: 13 }}>Close</button>
        </div>
      </div>
    );
  }

  const catBtn = ({ id, icon, label }) => {
    const active = category === id;
    return (
      <button key={id} type="button" onClick={() => setCategory(id)}
        className={active ? "" : "border bg2"}
        style={{
          flex: 1, padding: "8px 6px", borderRadius: 8, fontSize: 11, fontWeight: 700,
          border: active ? "1.5px solid var(--gold)" : undefined,
          background: active ? "rgba(200,160,76,0.14)" : undefined,
          color: active ? "var(--gold)" : "var(--ink2)",
          cursor: "pointer", display: "flex", flexDirection: "column", alignItems: "center", gap: 3,
        }}>
        <span style={{ fontSize: 16 }}>{icon}</span>
        <span>{label}</span>
      </button>
    );
  };

  const ta = {
    width: "100%", padding: "8px 10px", borderRadius: 8, fontSize: 12,
    color: "var(--ink)", outline: "none", resize: "vertical", boxSizing: "border-box", lineHeight: 1.5,
  };

  return (
    <div style={overlay} onClick={onClose}>
      <div className="bg1 border" role="dialog" aria-modal="true" aria-label="Send feedback" style={{
        borderRadius: 16, width: "100%", maxWidth: 460, maxHeight: "90vh",
        display: "flex", flexDirection: "column", boxShadow: "0 16px 60px rgba(0,0,0,0.45)",
      }} onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="row between p-3 border-b" style={{ alignItems: "center" }}>
          <div>
            <div className="font-semibold" style={{ fontSize: 15 }}>📝 Send feedback</div>
            <div className="ink3" style={{ fontSize: 10, marginTop: 2 }}>Helps us improve future answers</div>
          </div>
          <button onClick={onClose} className="ink3"
            style={{ background: "none", border: "none", fontSize: 19, cursor: "pointer", lineHeight: 1 }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ flex: "1 1 auto", overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="row gap-2">{CATEGORIES.map(catBtn)}</div>

          <div>
            <div className="ink3" style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>{COMMENT_LABEL[category]}</div>
            <textarea value={comments} onChange={(e) => setComments(e.target.value)} rows={4} autoFocus
              placeholder={COMMENT_PLACEHOLDER[category]} className="bg2 border" style={ta} />
          </div>

          <div>
            <div className="ink3" style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>
              Suggestion <span style={{ fontWeight: 400, opacity: 0.6 }}>(what would the right answer be?)</span>
            </div>
            <textarea value={suggestion} onChange={(e) => setSuggestion(e.target.value)} rows={2}
              placeholder="Optional. e.g. 'should have cited the Aadhaar card', 'the total is ₹4,200'…"
              className="bg2 border" style={ta} />
          </div>

          <div>
            <div className="ink3" style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>
              Screenshots <span style={{ fontWeight: 400, opacity: 0.6 }}>(up to {MAX_IMAGES} — show us what you saw)</span>
            </div>
            <div className="row gap-2" style={{ flexWrap: "wrap" }}>
              {screenshots.map((src, i) => (
                <div key={i} style={{ position: "relative", width: 78, height: 78, borderRadius: 6, overflow: "hidden", border: "1px solid var(--line)" }}>
                  <img src={src} alt={`screenshot ${i + 1}`} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  <button type="button" onClick={() => setScreenshots((prev) => prev.filter((_, j) => j !== i))}
                    style={{ position: "absolute", top: 2, right: 2, width: 18, height: 18, background: "rgba(0,0,0,0.6)",
                      color: "#fff", border: "none", borderRadius: 9, fontSize: 11, cursor: "pointer", lineHeight: 1 }}>✕</button>
                </div>
              ))}
              {screenshots.length < MAX_IMAGES && (
                <label className="bg2" style={{ width: 78, height: 78, borderRadius: 6, border: "1px dashed var(--line)",
                  display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column",
                  fontSize: 10, color: "var(--ink3)", cursor: "pointer", gap: 2 }}>
                  <span style={{ fontSize: 19 }}>📎</span>
                  <span>Add</span>
                  <input type="file" accept="image/*" multiple onChange={handleAddImages} style={{ display: "none" }} />
                </label>
              )}
            </div>
          </div>

          <div>
            <div className="ink3" style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>
              Overall rating <span style={{ fontWeight: 400, opacity: 0.6 }}>(optional)</span>
            </div>
            <div className="row gap-2">
              {[1, 2, 3, 4, 5].map((n) => (
                <button key={n} type="button" onClick={() => setRating(rating === n ? 0 : n)}
                  style={{
                    width: 36, height: 36, borderRadius: 8, fontSize: 18, cursor: "pointer",
                    border: rating >= n ? "2px solid var(--gold)" : "1px solid var(--line)",
                    background: rating >= n ? "rgba(200,160,76,0.16)" : "transparent",
                  }}>
                  {rating >= n ? "★" : "☆"}
                </button>
              ))}
            </div>
          </div>

          {err && (
            <div style={{ padding: "8px 12px", background: "rgba(216,98,94,0.1)", border: "1px solid rgba(216,98,94,0.3)", borderRadius: 8, color: "var(--rose)", fontSize: 11 }}>
              {err}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-3 border-t">
          <button onClick={handleSubmit} disabled={!canSubmit} className="btn-gold"
            style={{ width: "100%", padding: "11px 0", borderRadius: 10, fontSize: 14, fontWeight: 700,
              cursor: canSubmit ? "pointer" : "not-allowed", opacity: canSubmit ? 1 : 0.5 }}>
            {submitting ? "Submitting…" : "Submit feedback"}
          </button>
        </div>
      </div>
    </div>
  );
}
