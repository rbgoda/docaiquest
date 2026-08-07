// M48 · Documents System — public marketing landing page (the anonymous view).
//
// Shown at `/` to logged-OUT visitors instead of jumping straight to the
// sign-in form. "Sign in with Google" uses the SAME /api/auth/google/login
// flow DocumentsAuth uses (OAuth callback lands the user back on `/`, now
// signed in → DocGate renders the app). "Sign in" / "Open the app" reveal the
// existing email form via the onSignIn prop. No nginx / OAuth-redirect changes
// — the landing simply IS the anon experience.
//
// Styling reuses the design-system CSS variables (so it respects the dark/light
// theme) via a scoped <style> block with ld-* prefixed classes, so it never
// collides with globals.css.
import React from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { submitContact } from "../api";
import LandingDemo from "./LandingDemo.jsx";
import ProductFlow from "./ProductFlow.jsx";

const GoogleG = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.3-.4-3.5z"/>
    <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
    <path fill="#4CAF50" d="M24 44c5.2 0 10-2 13.6-5.2l-6.3-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.1-11.3-7.5l-6.5 5C9.5 39.6 16.2 44 24 44z"/>
    <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4 5.6l6.3 5.2C41.4 36.3 44 30.7 44 24c0-1.3-.1-2.3-.4-3.5z"/>
  </svg>
);

// Render a plan-matrix cell: "yes"/"no" → ✓/✗ chips, anything else as text.
function cell(v) {
  if (v === "yes") return <span className="ld-yes">✓</span>;
  if (v === "no") return <span className="ld-no">✕</span>;
  return <span className="ld-lim">{v}</span>;
}

// Rotating hero taglines — the brand's voice in motion.
const OSS_TAGLINES = [
  "Talk to your documents.",
  "Documents → Data → AI-IQ.",
  "Get the intel — skip the reading.",
  "Don't miss anything.",
  "Ask. Cited. Done.",
  "Your docs, your data, real answers.",
  "From a pile of PDFs to instant answers.",
  "Self-hosted. BYO keys. MIT licensed.",
  "Ask across all your documents at once.",
  "Totals, comparisons, counts — in one question.",
  "Answers in clean tables, not walls of text.",
  "100+ document types, deeply understood.",
];
const CLOUD_TAGLINES = [
  "Talk to your documents.",
  "Documents → Data → AI-IQ.",
  "Get the intel — skip the reading.",
  "Don't miss anything.",
  "Ask. Cited. Done.",
  "Your docs, your Drive, real answers.",
  "From a pile of PDFs to instant answers.",
  "Reads everything. Cites everything. Your Drive stays yours.",
  "Ask across all your documents at once.",
  "Totals, comparisons, counts — in one question.",
  "Answers in clean tables, not walls of text.",
  "100+ document types, deeply understood.",
];

// Contact-us popup — collects name + BUSINESS email + message, posts to /api/contact
// (which emails the team). Business-email check mirrors the server (no free-mail).
function ContactModal({ open, onClose, isCloud }) {
  const [f, setF] = React.useState({ firstName: "", lastName: "", businessEmail: "", description: "" });
  const [status, setStatus] = React.useState("idle");   // idle · sending · done · error
  const [err, setErr] = React.useState("");
  if (!open) return null;
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const FREE = /@(gmail|googlemail|yahoo|outlook|hotmail|live|msn|icloud|me|mac|aol|proton|protonmail|pm|gmx|mail|yandex|zoho|qq|163)\./i;
  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    const fn = f.firstName.trim(), ln = f.lastName.trim(), em = f.businessEmail.trim(), d = f.description.trim();
    if (!fn || !ln || !em || !d) { setErr("All fields are required."); return; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) { setErr("Please enter a valid email address."); return; }
    if (isCloud && FREE.test(em)) { setErr("Please use your business email (not a personal Gmail/Yahoo/Outlook address)."); return; }
    setStatus("sending");
    try {
      await submitContact({ firstName: fn, lastName: ln, businessEmail: em, description: d });
      setStatus("done");
    } catch (ex) {
      setStatus("error");
      setErr((ex && ex.message) || "Something went wrong — please try again.");
    }
  };
  return (
    <div className="ld-modal-ov" onClick={onClose}>
      <div className="ld-modal" onClick={(e) => e.stopPropagation()}>
        <button className="ld-modal-x" onClick={onClose} aria-label="Close">✕</button>
        {status === "done" ? (
          <div className="ld-modal-done">
            <div className="ld-modal-tick">✓</div>
            <h3>Thanks — we'll be in touch.</h3>
            <p className="ld-lead">Your message is on its way to our team.</p>
            <button className="ld-btn ld-btn-primary" onClick={onClose}>Close</button>
          </div>
        ) : (
          <form onSubmit={submit} className="ld-modal-form">
            <h3>Contact us</h3>
            <p className="ld-lead" style={{ marginTop: 2, marginBottom: 4 }}>Tell us a bit about you — we'll get back to you.</p>
            <div className="ld-modal-row">
              <input placeholder="First name" value={f.firstName} onChange={set("firstName")} autoFocus />
              <input placeholder="Last name" value={f.lastName} onChange={set("lastName")} />
            </div>
            <input type="email" placeholder="Business email" value={f.businessEmail} onChange={set("businessEmail")} />
            <textarea placeholder="How can we help?" rows={4} value={f.description} onChange={set("description")} />
            {err && <div className="ld-modal-err">{err}</div>}
            <button type="submit" className="ld-btn ld-btn-primary" disabled={status === "sending"}
                    style={{ width: "100%", justifyContent: "center" }}>
              {status === "sending" ? "Sending…" : "Send message"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default function DocumentsLanding({ onSignIn }) {
  const { config, isCloud } = useAuth();
  const googleEnabled = config?.googleLoginEnabled;
  const [signingIn, setSigningIn] = React.useState(false);
  const [contactOpen, setContactOpen] = React.useState(false);

  const handlePrimaryLogin = async () => {
    if (signingIn) return;
    setSigningIn(true);
    try {
      window.location.href = "/api/auth/google/login";
    } finally {
      setSigningIn(false);
    }
  };

  // Cycle the hero tagline every ~2.8s (pure presentation; cleaned up on unmount).
  const TAGLINES = isCloud ? CLOUD_TAGLINES : OSS_TAGLINES;
  const [taglineIdx, setTaglineIdx] = React.useState(0);
  React.useEffect(() => {
    const id = setInterval(() => setTaglineIdx((i) => (i + 1) % TAGLINES.length), 2800);
    return () => clearInterval(id);
  }, [TAGLINES.length]);

  // Primary CTA: Google when available, else reveal the email form.
  const GoogleBtn = ({ block }) =>
    googleEnabled ? (
      <button className="ld-btn ld-btn-primary" onClick={handlePrimaryLogin} disabled={signingIn}
              style={block ? { width: "100%", justifyContent: "center" } : undefined}>
        <GoogleG /> {signingIn ? "Signing in…" : "Sign in with Google"}
      </button>
    ) : (
      <button className="ld-btn ld-btn-primary" onClick={onSignIn}
              style={block ? { width: "100%", justifyContent: "center" } : undefined}>
        {isCloud ? "Get started — it's free" : "Get started"}
      </button>
    );

  return (
    <div className="ld-root">
      <style>{LD_CSS}</style>

      {/* NAV */}
      <nav className="ld-nav">
        <div className="ld-wrap ld-navrow">
          <a className="ld-brand" href="#top">
            <span className="ld-mark">D</span>
            <span className="ld-wordmark">Doc<span>AIQuest</span></span>
          </a>
          <div className="ld-links">
            <a href="#flow">How it works</a>
            <a href="#features">Features</a>
            {isCloud && <a href="#plans">Plans</a>}
            <a href="#compare">Compare</a>
            <a href="#faq">FAQ</a>
          </div>
          <GoogleBtn />
        </div>
      </nav>

      {/* HERO */}
      <header className="ld-hero" id="top">
        <div className="ld-wrap">
          <div className="ld-eyebrow">Privacy-native document intelligence</div>
          <h1 className="ld-h1">Talk to your documents.<br/><span className="ld-gold">Get the answers. Miss nothing.</span></h1>
          <div className="ld-rotline" key={taglineIdx}>{TAGLINES[taglineIdx]}</div>
          <p className="ld-lede ld-flowline">Your documents <span className="ld-gold">→ data → intelligence → answers.</span></p>
          <div className="ld-cta-row">
            <GoogleBtn />
          </div>
          <div className="ld-tagstrip">
            {(isCloud
              ? ["Trust score","Cited answers","Never makes it up","Any document type","Your Google Drive","Purge anytime","PII-safe · GDPR · PDPA"]
              : ["Trust score","Cited answers","Never makes it up","Any document type","Self-hosted","BYO LLM keys","MIT licensed","PII-safe"])
              .map((t) => <span key={t} className="ld-fw">{t}</span>)}
          </div>
        </div>
      </header>

      {/* LIVE DEMO — interactive showcase (bbox · highlight · extraction · analytics) */}
      <LandingDemo />

      {/* PRODUCT FLOW — Documents → Data → Intelligence → Dashboard → Chat */}
      <ProductFlow />

      {/* WHY DIFFERENT — the three genuinely unique values, tightened */}
      <section id="why" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">Why DocAIQuest</div>
          <h2 className="ld-h2">Three things <span className="ld-gold">no one else does together.</span></h2>
          <div className="ld-grid ld-g3 ld-why">
            <div className="ld-uc ld-uc1">
              <div className="ld-uc-ic">⭐</div>
              <h3>A trust score for every document</h3>
              <p>One fused 0–1 score — so you know at a glance what to trust and what to check.</p>
            </div>
            <div className="ld-uc ld-uc2">
              <div className="ld-uc-ic">✓</div>
              <h3>Cited answers, or honest silence</h3>
              <p>Every answer traces to its exact source — and says so when the evidence is thin, never guessing.</p>
            </div>
            <div className="ld-uc ld-uc3">
              <div className="ld-uc-ic">🔒</div>
              <h3>Private by design</h3>
              <p>{isCloud ? "Any document type, out of the box — and your files never leave your own Google Drive." : "Any document type, out of the box — self-hosted on your own infrastructure."}</p>
            </div>
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section id="features" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">What it does</div>
          <h2 className="ld-h2">Everything else you need to <span className="ld-gold">work with documents.</span></h2>
          <div className="ld-grid ld-g4">
            {[
              ["📥","Extract the facts","Any document type → clean fields, each with a confidence score."],
              ["🏷️","Auto-organize","Every file is sorted and tagged on arrival."],
              ["💬","Ask across everything","Totals, counts and answers across all your files at once — with clickable sources."],
              ["📊","Export to spreadsheet","Your documents' extracted data → CSV or Excel in one click."],
              ["🔀","Compare side by side","See what differs between documents at a glance — as a clean table."],
              (isCloud
                ? ["📂","Drive-native","Connect a folder; new files sync automatically."]
                : ["💻","Self-hosted","Runs on your infrastructure — Docker, one command."]),
              ["🤝","Share & review","Invite teammates or reviewers by email."],
              ["🛡️","PII-safe","Sensitive details are masked before processing."],
            ].map(([ic, h, p]) => (
              <div className="ld-card" key={h}><span className="ld-ic">{ic}</span><h3>{h}</h3><p>{p}</p></div>
            ))}
          </div>
        </div>
      </section>

      {/* COMPARE */}
      <section id="compare" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">Competitive landscape</div>
          <h2 className="ld-h2">They ground AI on your data — <span className="ld-gold">but keep it on their cloud.</span></h2>
          <div className="ld-tablewrap">
            <table className="ld-cmp">
              <thead><tr><th>Capability</th><th>Office AI suite</th><th>Workspace AI</th><th>AI chatbot</th><th className="ld-col-us">DocAIQuest</th></tr></thead>
              <tbody>
                <tr><td>Where your data lives</td><td>Vendor cloud</td><td>Vendor cloud</td><td>Vendor cloud</td><td className="ld-col-us ld-us">{isCloud ? "Your own Drive" : "Your own server"}</td></tr>
                <tr><td>Keeps a copy of your originals</td><td className="ld-no">Yes</td><td className="ld-no">Yes</td><td className="ld-no">Yes</td><td className="ld-col-us ld-us">No — they stay in your Drive</td></tr>
                <tr><td>Encryption with your own key</td><td className="ld-no">No</td><td className="ld-no">No</td><td className="ld-no">No</td><td className="ld-col-us ld-yes">Optional</td></tr>
                <tr><td>Any document type</td><td>Suite-bound</td><td>Workspace-bound</td><td>Manual uploads</td><td className="ld-col-us ld-us">Universal + self-learning</td></tr>
                <tr><td>Cross-doc cited chat</td><td className="ld-yes">Yes</td><td className="ld-yes">Yes</td><td>Partial</td><td className="ld-col-us ld-yes">Yes, cited</td></tr>
                <tr><td>Delete = truly gone</td><td>Vendor policy</td><td>Vendor policy</td><td>Retention policy</td><td className="ld-col-us ld-us">Delete account → fully purged</td></tr>
                <tr><td>Cost model</td><td>Per-seat</td><td>Per-seat add-on</td><td>Subscription</td><td className="ld-col-us ld-us">Pay-per-use, ~$0 idle</td></tr>
              </tbody>
            </table>
          </div>
          <div className="ld-kpis">
            <div className="ld-kpi"><div className="ld-n">{isCloud ? "~$0" : "100%"}</div><div className="ld-l">{isCloud ? "storage at idle — it's your own Drive" : "open source — it's your code"}</div></div>
            {isCloud && <div className="ld-kpi"><div className="ld-n">7-day</div><div className="ld-l">free trial, full access</div></div>}
            {isCloud && <div className="ld-kpi"><div className="ld-n">0</div><div className="ld-l">kept once you delete your account</div></div>}
            <div className="ld-kpi"><div className="ld-n">Any</div><div className="ld-l">document type, self-learning</div></div>
          </div>
        </div>
      </section>

      {/* 100+ CAPABILITIES */}
      <section id="capabilities" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">Handles anything you throw at it</div>
          <h2 className="ld-h2">One product. <span className="ld-gold">100+ of everything.</span></h2>
          <div className="ld-statband">
            <div className="ld-stat"><div className="ld-bignum">100+</div><div className="ld-statl">document types</div><p>From invoices to passports to lab reports — new kinds just work.</p></div>
            <div className="ld-stat"><div className="ld-bignum">100+</div><div className="ld-statl">languages</div><p>Including mixed-script documents, kept in their original form.</p></div>
            <div className="ld-stat"><div className="ld-bignum">30+</div><div className="ld-statl">file formats</div><p>PDF, scans &amp; images, Office, OpenDocument, email, CSV, text.</p></div>
          </div>
          <div className="ld-typegrid">
            {["Invoice","Passport","National ID","Driver licence","Certificate of insurance","Bank statement",
              "Credit-card statement","Contract / MSA","NDA","Lab result","Medical report","SOC 2 report",
              "ISO certificate","Purchase order","Receipt","Pay slip","Tax form (W-2/1099)","Bill of lading",
              "ESTA / visa","Board deck","Policy document","Resume / CV","Utility bill","…and any new type"]
              .map((t) => <span key={t} className="ld-type">{t}</span>)}
          </div>
        </div>
      </section>

      {/* PLANS / OSS — cloud gets the full pricing matrix; OSS gets a simple self-hosted blurb */}
      {isCloud ? (
      <section id="plans" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">Plans · what's included</div>
          <h2 className="ld-h2">Start free. <span className="ld-gold">Scale when you're ready.</span></h2>
          <p className="ld-lead">Test free with <b>7 single-page documents</b> + a <b>7-day full-feature trial</b> — no card.</p>
          <div className="ld-grid ld-g3 ld-plans">
            <div className="ld-plan">
              <div className="ld-plan-name">Free</div>
              <div className="ld-plan-price">$0<small>/to test</small></div>
              <ul className="ld-plan-ul">
                <li><b>7 single-page documents</b> — try it out</li>
                <li>Core extraction + trust score</li>
                <li>Basic cited chat (50 msgs/mo)</li>
                <li>PII-safe redaction</li>
              </ul>
              <div className="ld-plan-note">On the free plan, your uploads may be used to improve our AI models. Upgrade so your uploads are never used for training.</div>
              <GoogleBtn block />
            </div>
            <div className="ld-plan ld-plan-pro">
              <div className="ld-plan-flag">Most popular</div>
              <div className="ld-plan-name">Pro</div>
              <div className="ld-plan-price">Pay-per-use<small>~$0 idle</small></div>
              <ul className="ld-plan-ul">
                <li><b>Private</b> — your data never trains our models</li>
                <li>Unlimited pages · <b>100+ types</b>, self-learning</li>
                <li>Unlimited cited chat + abstention</li>
                <li>Full fields + confidence + trust score</li>
                <li>Export to CSV/Excel · Drive sync · share</li>
                <li>Intelligence dashboard + AI views</li>
              </ul>
              <button className="ld-btn ld-btn-primary" onClick={() => setContactOpen(true)}
                      style={{ width: "100%", justifyContent: "center" }}>Contact us</button>
            </div>
            <div className="ld-plan">
              <div className="ld-plan-name">Enterprise</div>
              <div className="ld-plan-price">Custom<small>talk to us</small></div>
              <ul className="ld-plan-ul">
                <li>Everything in Pro, unlimited</li>
                <li>Custom-trained types · all formats</li>
                <li>SSO · roles · priority models</li>
                <li>DPA &amp; compliance docs · SLA</li>
              </ul>
              <button className="ld-btn ld-btn-ghost" onClick={() => setContactOpen(true)}
                      style={{ width: "100%", justifyContent: "center" }}>Contact us</button>
            </div>
          </div>
          <details className="ld-plans-more">
            <summary>Compare every feature, side by side</summary>
          <div className="ld-tablewrap">
            <table className="ld-cmp ld-tiers">
              <thead>
                <tr>
                  <th>Functionality</th>
                  <th>Free</th>
                  <th className="ld-col-us">Pro</th>
                  <th>Enterprise</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["Documents", "7 · single-page (test)", "~30 / month", "Unlimited"],
                  ["Pages per document", "1 (single-page)", "Unlimited", "Unlimited"],
                  ["Data used to improve our models", "Yes — free tier", "No", "No"],
                  ["Document types understood", "Common types (invoice, receipt, ID)", "100+ · self-learning", "100+ · custom-trained"],
                  ["File formats", "Core (PDF, images, TXT, CSV)", "30+ (incl. DOCX/XLSX/PPTX, ODF)", "30+ · all + custom"],
                  ["Languages", "English + major", "100+", "100+"],
                  ["AI model quality", "Light / efficient (low token use)", "Premium models", "Premium · priority"],
                  ["Auto-classify & tag", "yes", "yes · self-learning", "yes"],
                  ["Field extraction + confidence", "Basic fields", "Full + per-field confidence + trust score", "Full"],
                  ["Cited cross-document chat", "Limited (50 msgs / mo)", "Unlimited, cited", "Unlimited"],
                  ["Faithfulness: abstain + per-sentence cites", "yes", "yes", "yes"],
                  ["PII-safe redaction", "yes", "yes", "yes"],
                  ["Extract to table / CSV / Excel", "no", "yes", "yes"],
                  ["Compare & summarize", "Limited", "yes", "yes"],
                  ["Drive-native sync (connect a folder)", "no", "yes", "yes"],
                  ["Groups · share & review", "no", "yes", "yes · with roles"],
                  ["Encrypted Drive backup & restore", "no", "yes", "yes"],
                  ["Intelligence dashboard (alerts + AI views)", "Basic alerts", "Full + AI-proposed views", "Full"],
                  ["Bulk operations", "no", "yes", "yes"],
                  ["Partner / integration API", "no", "Limited", "yes"],
                  ["SSO · roles & permissions", "no", "no", "yes"],
                  ["DPA & compliance documentation", "no", "no", "yes"],
                  ["Support", "Community", "Email", "Priority · SLA"],
                ].map(([feat, free, pro, ent]) => (
                  <tr key={feat}>
                    <td>{feat}</td>
                    <td>{cell(free)}</td>
                    <td className="ld-col-us">{cell(pro)}</td>
                    <td>{cell(ent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </details>
          <p className="ld-fineprint">Your files always live in your own Google Drive (~$0 idle); free-tier uploads may help improve our models, paid plans never do.</p>
        </div>
      </section>
      ) : (
      <section id="oss" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">Self-hosted · MIT licensed</div>
          <h2 className="ld-h2">Free. Open source. <span className="ld-gold">Your server, your data.</span></h2>
          <p className="ld-lead">DocAIQuest runs on your own infrastructure. Bring your own LLM keys — nothing leaves your server except the API calls you configure. Parse, chunk, embed, retrieve, and chat across your documents with cited answers. All <a href="https://github.com/rbgoda/docaiquest" style={{color: "var(--gold2)", textDecoration: "underline"}}>MIT licensed on GitHub</a>.</p>
          <div className="ld-grid ld-g3" style={{marginTop: 28}}>
            {[
              ["📦","Self-hosted","One command: docker compose up. PostgreSQL, Redis, MinIO, FastAPI, React — all in one stack."],
              ["🔑","BYO LLM keys","Bring your own DashScope, OpenAI, Anthropic, or Gemini keys. No managed LLM access — you control cost and privacy."],
              ["🛡️","Privacy-native","Your documents stay on your hardware. PII redaction before LLM calls. Zero telemetry. No training on your data."],
              ["🔍","Hybrid RAG","BM25 + vector search with cross-encoder reranking. Per-sentence citations. Abstains when evidence is thin."],
              ["📄","Deep parsing","PDF, DOCX, XLSX, CSV, images, EML — layout-aware parsing with OCR. Tables, figures, multi-column text."],
              ["🧩","Entity graph","Deterministic entity resolution across documents. Zero-LLM canonicalization. Graph traversal for related docs."],
            ].map(([icon, title, desc]) => (
              <div className="ld-uc" key={title}>
                <div className="ld-uc-ic">{icon}</div>
                <h3>{title}</h3>
                <p>{desc}</p>
              </div>
            ))}
          </div>
          <div style={{textAlign: "center", marginTop: 24}}>
            <a href="https://github.com/rbgoda/docaiquest" className="ld-btn ld-btn-ghost" style={{justifyContent: "center"}}>
              View on GitHub →
            </a>
          </div>
        </div>
      </section>
      )}

      {/* FAQ */}
      <section id="faq" className="ld-sec">
        <div className="ld-wrap">
          <div className="ld-kicker">FAQ</div>
          <h2 className="ld-h2">Questions, <span className="ld-gold">answered.</span></h2>
          <div className="ld-faq">
            {[
              ['How does it work?',
               isCloud
                 ? 'Upload documents or connect a Drive folder → DocAIQuest extracts the key facts, builds dashboards, and answers questions across everything — with answers you can trace to the source.'
                 : 'Upload documents → DocAIQuest parses, chunks, embeds, and indexes them. Ask questions across your library and get cited answers traceable to the exact source. Everything runs on your server with your own LLM keys.'],
              ['Where do my documents live? Do you keep a copy?',
               isCloud
                 ? 'Always in your own Google Drive (encryption optional) — delete your account anytime to purge all DocAIQuest metadata; your Drive and files stay with you.'
                 : 'On your own server. DocAIQuest is self-hosted — all documents, embeddings, and metadata live in your own PostgreSQL and MinIO instances. No cloud dependency beyond the LLM providers you configure yourself.'],
              ['Is my private data safe?',
               isCloud
                 ? 'Yes — sensitive details (passport, account number, DOB, email…) are masked before processing; on paid plans your uploads are never used for training.'
                 : 'Yes — sensitive details (passport, account number, DOB, email…) are masked before processing. Your data never leaves your server and is never used for training.'],
              ['Can it answer across all my documents?',
               'Yes — ask in plain language, trace every answer to its source, and it says so when evidence is weak instead of guessing.'],
              ['What does it cost?',
               isCloud
                 ? 'Test free (7 single-page docs + a 7-day full-feature trial), then pay only for what you use — no per-seat fees.'
                 : 'DocAIQuest is free and MIT licensed. You only pay for your own LLM provider usage (DashScope, OpenAI, Anthropic, etc.) and your server costs.'],
            ].map(([q, a]) => (
              <details className="ld-q" key={q}>
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA BAND */}
      <section className="ld-sec ld-ctaband">
        <div className="ld-wrap">
          <h2 className="ld-h2">{isCloud ? <>Most document AI keeps your documents.<br/><span className="ld-gold">DocAIQuest keeps them in your Drive.</span></> : <>Self-hosted document intelligence.<br/><span className="ld-gold">Your server. Your data. Your keys.</span></>}</h2>
          <p className="ld-ctap">{isCloud ? "Any document, in your own Drive, purge anytime — start free in seconds." : "One command to deploy. MIT licensed. Free forever."}</p>
          <div className="ld-cta-row" style={{ justifyContent: "center" }}>
            <GoogleBtn />
          </div>
          <div className="ld-ctmeta">{isCloud ? "your docs · your Drive · purge anytime" : "your docs · your server · MIT licensed"}</div>
        </div>
      </section>

      <footer className="ld-foot">
        <div className="ld-wrap ld-footrow">
          <span>DocAIQuest — universal, privacy-native document intelligence</span>
          <span>
            <button type="button" className="ld-foot-link" onClick={() => setContactOpen(true)}>Contact us</button>
            {" · "}<a href="/privacy">Privacy</a> · <a href="/termsofservice">Terms</a>
            {isCloud
              ? " · PII-safe · GDPR · PDPA · your data stays yours"
              : " · PII-safe · self-hosted · MIT licensed"}
            {" · "}<span className="ld-powered">Powered by DocAIQuest</span>
          </span>
        </div>
      </footer>

      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} isCloud={isCloud} />
    </div>
  );
}

const LD_CSS = `
.ld-root{position:fixed;inset:0;overflow-y:auto;background:var(--bg);color:var(--ink);
  font-family:var(--font-sans,"IBM Plex Sans",system-ui,sans-serif);line-height:1.6;
  background-image:radial-gradient(1100px 640px at 82% -8%, rgba(200,160,76,.10), transparent 60%),
    radial-gradient(820px 560px at -8% 28%, rgba(139,127,214,.06), transparent 60%);}
.ld-root a{color:inherit;text-decoration:none;}
.ld-wrap{max-width:1060px;margin:0 auto;padding:0 24px;}
.ld-gold{color:var(--gold2);}
.ld-eyebrow,.ld-kicker{font-family:var(--font-mono,"IBM Plex Mono",monospace);font-size:11px;
  letter-spacing:.16em;text-transform:uppercase;color:var(--gold2);}
.ld-kicker{margin-bottom:14px;}
.ld-h1,.ld-h2,.ld-card h3,.ld-kpi .ld-n{font-family:var(--font-serif,"Fraunces",Georgia,serif);}
.ld-h1{font-weight:600;font-size:clamp(34px,6vw,54px);line-height:1.05;margin:18px 0 22px;}
.ld-h2{font-weight:600;font-size:clamp(24px,4vw,30px);line-height:1.12;margin-bottom:14px;}
.ld-lead{color:var(--ink2);font-size:16px;max-width:64ch;}

/* nav */
.ld-nav{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--bg) 82%,transparent);
  backdrop-filter:blur(10px);border-bottom:1px solid var(--line);}
.ld-navrow{display:flex;align-items:center;gap:22px;height:62px;}
.ld-brand{display:flex;align-items:center;gap:11px;}
.ld-mark{width:32px;height:32px;border-radius:9px;background:linear-gradient(150deg,var(--gold2),var(--gold));
  display:grid;place-items:center;color:#1a1407;font-family:var(--font-serif,"Fraunces",serif);font-weight:700;font-size:18px;
  box-shadow:0 4px 14px rgba(200,160,76,.35);}
.ld-wordmark{font-family:var(--font-serif,"Fraunces",serif);font-weight:600;font-size:18px;}
.ld-wordmark span{color:var(--gold2);}
.ld-wordmark small{font-family:inherit;font-size:11px;color:var(--ink3);margin-left:6px;letter-spacing:.04em;}
.ld-links{display:flex;gap:22px;align-items:center;margin-left:auto;}
.ld-links a,.ld-link-btn{font-size:13.5px;color:var(--ink2);background:none;border:none;cursor:pointer;
  font-family:inherit;padding:0;}
.ld-links a:hover,.ld-link-btn:hover{color:var(--ink);}
.ld-btn{display:inline-flex;align-items:center;gap:9px;font-family:inherit;font-weight:600;font-size:14px;
  padding:11px 20px;border-radius:10px;cursor:pointer;border:none;transition:transform .08s ease;}
.ld-btn:hover{transform:translateY(-1px);}
.ld-btn-primary{background:linear-gradient(150deg,var(--gold2),var(--gold));color:#1a1407;
  box-shadow:0 6px 20px rgba(200,160,76,.30);}
.ld-btn-ghost{border:1px solid var(--line2);color:var(--ink);background:none;}
.ld-nav .ld-btn{padding:9px 16px;font-size:13px;}

/* hero */
.ld-hero{padding:90px 0 70px;text-align:center;}
.ld-lede{color:var(--ink2);font-size:18px;max-width:62ch;margin:0 auto 30px;}
.ld-flowline{font-family:'Fraunces',serif;font-size:26px;letter-spacing:-.01em;color:var(--ink);margin-bottom:26px;}
.ld-flowline .ld-gold{white-space:nowrap;}
@media(max-width:600px){.ld-flowline{font-size:21px;}.ld-flowline .ld-gold{white-space:normal;}}
.ld-lede b{color:var(--ink);}
.ld-rotline{font-family:var(--font-mono,"IBM Plex Mono",monospace);font-size:15px;letter-spacing:.3px;
  color:var(--gold);margin:-8px 0 22px;min-height:1.4em;animation:ld-rot .5s ease;}
@keyframes ld-rot{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}
.ld-cta-row{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;}
.ld-tagstrip{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:34px;}
.ld-fw{font-family:var(--font-mono,monospace);font-size:11px;color:var(--ink2);background:var(--bg2);
  border:1px solid var(--line);border-radius:999px;padding:5px 11px;}

/* sections */
.ld-sec{padding:78px 0;border-top:1px solid var(--line);}
.ld-grid{display:grid;gap:16px;margin-top:30px;}
.ld-g3{grid-template-columns:repeat(3,1fr);}
.ld-g4{grid-template-columns:repeat(4,1fr);}
.ld-card{background:var(--bg1);border:1px solid var(--line);border-radius:14px;padding:22px;}
.ld-card .ld-ic{font-size:20px;margin-bottom:12px;display:block;}
.ld-card h3{font-size:15.5px;color:var(--ink);margin-bottom:7px;font-weight:600;}
.ld-card p{font-size:13.5px;color:var(--ink2);margin:0;}
.ld-trust .ld-card h3{display:flex;align-items:center;gap:8px;}
.ld-shield{width:7px;height:7px;border-radius:50%;background:var(--emerald);display:inline-block;}
@media(max-width:820px){.ld-g4{grid-template-columns:repeat(2,1fr);}.ld-g3{grid-template-columns:1fr;}}

/* why-different (the three uniques) */
.ld-why{margin-top:26px;}
.ld-uc{position:relative;background:var(--bg1);border:1px solid var(--line);border-radius:16px;
  padding:26px 24px;overflow:hidden;}
.ld-uc::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;}
.ld-uc1::before{background:linear-gradient(90deg,var(--gold2),var(--gold));}
.ld-uc2::before{background:linear-gradient(90deg,#3fa47a,#68a0c9);}
.ld-uc3::before{background:linear-gradient(90deg,#8b7fd6,#c8a04c);}
.ld-uc-ic{width:42px;height:42px;border-radius:11px;display:grid;place-items:center;font-size:20px;
  background:rgba(200,160,76,.14);margin-bottom:15px;}
.ld-uc2 .ld-uc-ic{background:rgba(63,164,122,.16);}
.ld-uc3 .ld-uc-ic{background:rgba(139,127,214,.16);}
.ld-uc h3{font-family:var(--font-serif,"Fraunces",Georgia,serif);font-size:18px;font-weight:600;
  color:var(--ink);margin:0 0 9px;line-height:1.25;}
.ld-uc p{font-size:14px;color:var(--ink2);margin:0;line-height:1.55;}

/* moat / network-effect */
.ld-moat{background:linear-gradient(180deg,transparent,rgba(139,127,214,.05));}
.ld-moat-steps{display:flex;align-items:stretch;gap:12px;margin:28px 0 6px;flex-wrap:wrap;}
.ld-ms{flex:1;min-width:190px;background:var(--bg1);border:1px solid var(--line);border-radius:14px;padding:20px;}
.ld-ms-n{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;
  background:linear-gradient(150deg,var(--gold2),var(--gold));color:#1a1407;font-weight:700;font-size:13px;margin-bottom:10px;}
.ld-ms b{display:block;color:var(--ink);font-size:15px;margin-bottom:5px;}
.ld-ms p{margin:0;font-size:13px;color:var(--ink2);}
.ld-ms-ar{display:flex;align-items:center;color:var(--ink3);font-size:20px;}
@media(max-width:760px){.ld-ms-ar{display:none;}}

/* plan cards */
.ld-plans{margin-top:24px;align-items:stretch;}
.ld-plan{position:relative;background:var(--bg1);border:1px solid var(--line);border-radius:16px;
  padding:26px 22px;display:flex;flex-direction:column;gap:14px;}
.ld-plan-pro{border-color:rgba(200,160,76,.55);box-shadow:0 10px 40px rgba(200,160,76,.10);}
.ld-plan-flag{position:absolute;top:-11px;left:22px;font-family:var(--font-mono,monospace);font-size:10px;
  letter-spacing:.12em;text-transform:uppercase;background:linear-gradient(150deg,var(--gold2),var(--gold));
  color:#1a1407;padding:4px 10px;border-radius:999px;font-weight:700;}
.ld-plan-name{font-family:var(--font-serif,"Fraunces",Georgia,serif);font-size:20px;font-weight:600;color:var(--ink);}
.ld-plan-price{font-size:24px;font-weight:700;color:var(--ink);letter-spacing:-.5px;}
.ld-plan-price small{font-size:12px;font-weight:500;color:var(--ink3);margin-left:6px;letter-spacing:0;}
.ld-plan-ul{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:9px;flex:1;}
.ld-plan-ul li{font-size:13.5px;color:var(--ink2);padding-left:22px;position:relative;line-height:1.4;}
.ld-plan-ul li::before{content:"✓";position:absolute;left:0;color:var(--emerald);font-weight:700;}
.ld-plan-ul li b{color:var(--ink);}
.ld-plan-note{font-size:11px;line-height:1.45;color:var(--ink3);background:rgba(224,162,59,.08);
  border:1px solid rgba(224,162,59,.22);border-radius:9px;padding:9px 11px;margin-top:2px;}
.ld-plans-more{margin-top:18px;}
.ld-plans-more>summary{cursor:pointer;font-size:13.5px;color:var(--gold2);font-weight:600;
  list-style:none;display:inline-flex;align-items:center;gap:7px;padding:8px 0;}
.ld-plans-more>summary::before{content:"▸";transition:transform .2s;font-size:11px;}
.ld-plans-more[open]>summary::before{transform:rotate(90deg);}
.ld-plans-more>summary::-webkit-details-marker{display:none;}

/* steps + flow */
.ld-steps{counter-reset:s;list-style:none;display:grid;gap:14px;margin-top:26px;padding:0;}
.ld-steps li{counter-increment:s;position:relative;background:var(--bg1);border:1px solid var(--line);
  border-radius:13px;padding:20px 22px 20px 64px;}
.ld-steps li::before{content:counter(s);position:absolute;left:18px;top:18px;width:30px;height:30px;
  border-radius:8px;background:var(--bg3);border:1px solid var(--line2);color:var(--gold2);
  font-family:var(--font-mono,monospace);font-weight:600;display:grid;place-items:center;font-size:14px;}
.ld-steps b{color:var(--ink);}
.ld-steps span{color:var(--ink2);font-size:13.5px;}
.ld-flow{display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin-top:24px;}
.ld-node{font-family:var(--font-mono,monospace);font-size:12px;background:var(--bg2);border:1px solid var(--line);
  border-radius:9px;padding:9px 13px;color:var(--ink2);}
.ld-node-g{background:linear-gradient(150deg,rgba(226,188,104,.16),rgba(200,160,76,.08));
  border-color:var(--gold);color:var(--gold2);}
.ld-arr{color:var(--ink3);}

/* compare */
.ld-tablewrap{overflow-x:auto;margin-top:26px;}
.ld-cmp{width:100%;border-collapse:collapse;font-size:13px;background:var(--bg1);border:1px solid var(--line);
  border-radius:14px;overflow:hidden;}
.ld-cmp th,.ld-cmp td{padding:12px 14px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
.ld-cmp thead th{background:var(--bg2);color:var(--ink2);font-weight:600;font-size:12px;}
.ld-cmp .ld-col-us{background:rgba(200,160,76,.07);}
.ld-cmp th.ld-col-us{color:var(--gold2);}
.ld-cmp td:first-child{color:var(--ink2);}
.ld-cmp .ld-us{color:var(--ink);font-weight:600;}
.ld-cmp .ld-yes{color:var(--emerald);}
.ld-cmp .ld-no{color:var(--rose);}

/* kpis */
.ld-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:28px;}
.ld-kpi{background:var(--bg1);border:1px solid var(--line);border-radius:13px;padding:22px 14px;text-align:center;}
.ld-kpi .ld-n{font-family:var(--font-mono,monospace);font-size:24px;font-weight:600;color:var(--gold2);letter-spacing:-.02em;}
.ld-kpi .ld-l{font-size:11.5px;color:var(--ink3);margin-top:9px;line-height:1.4;}

/* plans / tier matrix */
.ld-tiers td,.ld-tiers th{white-space:normal;vertical-align:top;}
.ld-tiers th.ld-col-us{position:relative;}
.ld-tiers thead th.ld-col-us::after{content:"Popular";position:absolute;top:-1px;right:8px;
  font-family:var(--font-mono,monospace);font-size:8.5px;letter-spacing:.1em;text-transform:uppercase;
  color:#1a1407;background:var(--gold2);border-radius:0 0 5px 5px;padding:2px 6px;}
.ld-tiers td:first-child{color:var(--ink);font-weight:500;}
.ld-lim{color:var(--ink2);font-size:12.5px;}
.ld-tiers .ld-col-us .ld-lim{color:var(--ink);}
.ld-tiers .ld-yes{color:var(--emerald);font-weight:700;}
.ld-tiers .ld-no{color:var(--ink4,var(--ink3));opacity:.5;}

/* 100+ capabilities */
.ld-statband{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:30px;}
.ld-stat{background:var(--bg1);border:1px solid var(--line);border-radius:14px;padding:24px 22px;}
.ld-bignum{font-family:var(--font-serif,"Fraunces",serif);font-weight:600;font-size:46px;line-height:1;color:var(--gold2);}
.ld-statl{font-size:14px;color:var(--ink);font-weight:600;margin:8px 0 10px;}
.ld-stat p{font-size:13px;color:var(--ink2);margin:0;}
.ld-typegrid{display:flex;flex-wrap:wrap;gap:8px;margin-top:24px;}
.ld-type{font-family:var(--font-mono,monospace);font-size:11.5px;color:var(--ink2);background:var(--bg2);
  border:1px solid var(--line);border-radius:8px;padding:6px 11px;}
.ld-fineprint{font-size:13px;color:var(--ink3);margin-top:18px;max-width:72ch;}

/* faq */
.ld-faq{margin-top:26px;display:grid;gap:10px;}
.ld-q{background:var(--bg1);border:1px solid var(--line);border-radius:13px;padding:0 20px;}
.ld-q summary{cursor:pointer;list-style:none;padding:18px 0;font-size:15px;color:var(--ink);font-weight:600;
  display:flex;align-items:center;justify-content:space-between;gap:12px;}
.ld-q summary::-webkit-details-marker{display:none;}
.ld-q summary::after{content:"+";color:var(--gold2);font-size:20px;font-weight:400;line-height:1;}
.ld-q[open] summary::after{content:"–";}
.ld-q p{font-size:14px;color:var(--ink2);margin:0;padding:0 0 20px;line-height:1.65;max-width:78ch;}

/* cta band + footer */
.ld-ctaband{background:linear-gradient(160deg,var(--bg2),var(--bg));text-align:center;}
.ld-ctap{color:var(--ink2);max-width:58ch;margin:0 auto 26px;}
.ld-ctmeta{margin-top:20px;font-family:var(--font-mono,monospace);font-size:11px;color:var(--ink3);letter-spacing:.04em;}
.ld-foot{padding:30px 0 50px;border-top:1px solid var(--line);}
.ld-footrow{display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;}
.ld-foot span{font-family:var(--font-mono,monospace);font-size:11px;color:var(--ink4);letter-spacing:.04em;}
.ld-foot a{color:var(--ink3,#9a937f);text-decoration:underline;}
.ld-foot a:hover{color:var(--gold,#E2BC68);}
.ld-foot-link{background:none;border:none;padding:0;font:inherit;color:var(--ink3,#9a937f);text-decoration:underline;cursor:pointer;}
.ld-foot-link:hover{color:var(--gold,#E2BC68);}
/* contact modal */
.ld-modal-ov{position:fixed;inset:0;background:rgba(0,0,0,.6);backdrop-filter:blur(3px);display:grid;place-items:center;z-index:1000;padding:20px;}
.ld-modal{position:relative;width:100%;max-width:440px;background:var(--bg1,#15171d);border:1px solid var(--line,rgba(255,255,255,.12));border-radius:14px;padding:26px 24px;box-shadow:0 20px 60px rgba(0,0,0,.5);}
.ld-modal-x{position:absolute;top:12px;right:14px;background:none;border:none;color:var(--ink3,#9aa0ad);font-size:16px;cursor:pointer;}
.ld-modal h3{margin:0;font-size:20px;color:var(--ink,#fff);}
.ld-modal-form{display:flex;flex-direction:column;gap:10px;}
.ld-modal-row{display:flex;gap:10px;}
.ld-modal-row input{flex:1 1 0;min-width:0;}
.ld-modal input,.ld-modal textarea{width:100%;background:var(--bg2,rgba(255,255,255,.04));border:1px solid var(--line,rgba(255,255,255,.14));border-radius:8px;padding:10px 12px;color:var(--ink,#eef);font-size:14px;font-family:inherit;}
.ld-modal input:focus,.ld-modal textarea:focus{outline:none;border-color:var(--gold,#c8a04c);}
.ld-modal textarea{resize:vertical;}
.ld-modal-err{background:rgba(216,98,94,.14);border:1px solid rgba(216,98,94,.4);color:#e69b97;font-size:12px;padding:8px 10px;border-radius:7px;}
.ld-modal-done{text-align:center;display:flex;flex-direction:column;gap:8px;align-items:center;padding:8px 0;}
.ld-modal-tick{width:48px;height:48px;border-radius:50%;background:rgba(63,164,122,.18);border:1px solid rgba(63,164,122,.5);color:#7fd6ab;display:grid;place-items:center;font-size:24px;}

@media (max-width:860px){
  .ld-g3,.ld-g4{grid-template-columns:1fr 1fr;}
  .ld-kpis,.ld-statband{grid-template-columns:1fr 1fr;}
  .ld-sec{padding:56px 0;}
  .ld-links{display:none;}
}
@media (max-width:560px){ .ld-g3,.ld-g4{grid-template-columns:1fr;} }
`;
