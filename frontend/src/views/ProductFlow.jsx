// ProductFlow — the "from documents to decisions" walkthrough on the landing.
// Four realistic, self-contained product screens (dark-gold DocAIQ styling, no
// backend) that show the actual value path: Documents → Data → Intelligence →
// Dashboard → Chat. 100% dummy data. Scoped `.pf-*` styles.
import React, { useState } from "react";

const STAGES = [
  { key: "extract", n: "01", tab: "Documents → Data", title: "Every document becomes structured, cited data",
    blurb: "Upload any PDF, image or statement — no templates — and get typed fields, each with a confidence score you can trust or correct." },
  { key: "intel", n: "02", tab: "Data → Intelligence", title: "It connects the dots across everything you own",
    blurb: "People, amounts, renewals and due-dates linked across your whole library — so a renewal buried on page 4 surfaces." },
  { key: "dash", n: "03", tab: "Live dashboards", title: "One click turns your documents into a dashboard",
    blurb: "Pick a theme — finance, expenses, health — and get a live dashboard from your own data, with charts and an AI read-out." },
  { key: "chat", n: "04", tab: "Ask anything", title: "Chat across your library — every answer cited",
    blurb: "Ask in plain language; answers quote the exact source, or say so when it's not in your documents." },
];

/* ---- Screen 1 · Documents → Data ------------------------------------------ */
function ScreenExtract() {
  const fields = [
    ["Patient", "Alex Morgan", "#3FA47A"],
    ["Lab", "Meridian Diagnostics", "#8B7FD6"],
    ["Collected", "26 Jun 2023", "#E2BC68"],
    ["Glucose (fasting)", "98 mg/dL", "#3FA47A"],
    ["Total cholesterol", "212 mg/dL", "#E0A23B"],
    ["Vitamin D", "18 ng/mL", "#D8625E"],
  ];
  return (
    <div className="pf-scr">
      <div className="pf-scrbar"><span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" /><span className="pf-scrname">Health screening report.pdf</span><span className="pf-pill pf-em">READY · 92%</span></div>
      <div className="pf-extract">
        <div className="pf-doc">
          <div className="pf-docline" style={{ width: "82%" }} /><div className="pf-docline" style={{ width: "64%" }} />
          <div className="pf-docband"><span>GLUCOSE</span><span>98</span></div>
          <div className="pf-docline" style={{ width: "70%" }} /><div className="pf-docline" style={{ width: "48%" }} />
          <div className="pf-docband v"><span>CHOLESTEROL</span><span>212</span></div>
          <div className="pf-docline" style={{ width: "58%" }} />
        </div>
        <div className="pf-fields">
          <div className="pf-fh">Extracted fields</div>
          {fields.map(([k, v, c]) => (
            <div className="pf-frow" key={k}>
              <span className="pf-fk">{k}</span>
              <span className="pf-fv" style={{ color: c }}>{v}</span>
              <span className="pf-ftick" style={{ color: c }}>✓</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---- Screen 2 · Data → Intelligence --------------------------------------- */
function ScreenIntel() {
  const items = [
    ["Car insurance", "renews", "22 Jul", "#E0A23B"],
    ["Apartment lease", "expires", "03 Aug", "#D8625E"],
    ["Passport", "valid to", "2031", "#3FA47A"],
    ["Credit card", "payment due", "15 Jul", "#8B7FD6"],
  ];
  return (
    <div className="pf-scr">
      <div className="pf-scrbar"><span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" /><span className="pf-scrname">Intelligence · what needs you</span></div>
      <div className="pf-intel">
        <div className="pf-chips">
          {["Alex Morgan", "Meridian", "Northwind Bank", "$4,200", "Jul 2026"].map((c, i) => (
            <span className="pf-chip" key={c} style={{ "--c": ["#3FA47A", "#8B7FD6", "#6A93C8", "#E2BC68", "#E0A23B"][i] }}>{c}</span>
          ))}
        </div>
        <div className="pf-watch">
          {items.map(([t, l, d, c]) => (
            <div className="pf-wrow" key={t}>
              <span className="pf-wdot" style={{ background: c }} />
              <span className="pf-wt">{t}</span>
              <span className="pf-wl">{l}</span>
              <span className="pf-wd" style={{ color: c }}>{d}</span>
            </div>
          ))}
        </div>
        <div className="pf-intelnote">Derived from your dates & entities — zero extra work.</div>
      </div>
    </div>
  );
}

/* ---- Screen 3 · Dashboard -------------------------------------------------- */
function ScreenDash() {
  const kpis = [["Income", "$8,240", "#3FA47A"], ["Spend", "$5,110", "#E0A23B"], ["Saved", "38%", "#6A93C8"]];
  const bars = [62, 40, 78, 34, 90, 52, 70];
  return (
    <div className="pf-scr">
      <div className="pf-scrbar"><span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" /><span className="pf-scrname">Financial dashboard</span></div>
      <div className="pf-dash">
        <div className="pf-kpis">
          {kpis.map(([l, v, c]) => (
            <div className="pf-kpi" key={l} style={{ "--c": c }}><span className="pf-kl">{l}</span><span className="pf-kv">{v}</span></div>
          ))}
        </div>
        <div className="pf-chart">
          {bars.map((h, i) => <span key={i} style={{ height: `${h}%`, background: i === 4 ? "#E2BC68" : "#2E3542" }} />)}
        </div>
        <div className="pf-ai"><span className="pf-aidot">✦</span> Spending fell 12% vs last month; dining is your top category.</div>
      </div>
    </div>
  );
}

/* ---- Screen 4 · Chatbot ---------------------------------------------------- */
function ScreenChat() {
  return (
    <div className="pf-scr">
      <div className="pf-scrbar"><span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" /><span className="pf-scrname">Ask your documents</span></div>
      <div className="pf-chat">
        <div className="pf-bub you">What deadlines are coming up?</div>
        <div className="pf-bub ai">
          Two in the next 30 days — <b>car insurance</b> renews <b>22 Jul</b> and your <b>lease</b> ends <b>3 Aug</b>.
          <div className="pf-cites"><span>car_insurance.pdf</span><span>lease_2024.pdf</span></div>
        </div>
        <div className="pf-bub you">Any lab result out of range?</div>
        <div className="pf-bub ai">Yes — <b style={{ color: "#D8625E" }}>Vitamin D is low (18 ng/mL)</b>. Everything else is normal. <span className="pf-cite">[health_report.pdf]</span></div>
        <div className="pf-composer"><span className="pf-cinput">Ask across all your documents…</span><span className="pf-csend">↑</span></div>
      </div>
    </div>
  );
}

const SCREENS = { extract: ScreenExtract, intel: ScreenIntel, dash: ScreenDash, chat: ScreenChat };

export default function ProductFlow() {
  const [active, setActive] = useState("extract");
  const stage = STAGES.find((s) => s.key === active);
  const Screen = SCREENS[active];
  return (
    <section id="flow" className="ld-sec pf">
      <style>{PF_CSS}</style>
      <div className="ld-wrap">
        <div className="ld-kicker">How it works</div>
        <h2 className="ld-h2">From a folder of documents<br /><span className="ld-gold">to answers you can act on.</span></h2>
        <div className="pf-tabs">
          {STAGES.map((s) => (
            <button key={s.key} className={`pf-tab${s.key === active ? " on" : ""}`} onClick={() => setActive(s.key)}>
              <span className="pf-tn">{s.n}</span>{s.tab}
            </button>
          ))}
        </div>
        <div className="pf-stage">
          <div className="pf-copy">
            <h3 className="pf-title">{stage.title}</h3>
            <p className="pf-blurb">{stage.blurb}</p>
            <div className="pf-steps">
              {STAGES.map((s) => (
                <button key={s.key} className={`pf-step${s.key === active ? " on" : ""}`} onClick={() => setActive(s.key)}>{s.tab}</button>
              ))}
            </div>
          </div>
          <div className="pf-screenwrap"><Screen /></div>
        </div>
      </div>
    </section>
  );
}

const PF_CSS = `
.pf .ld-wrap{max-width:1080px}
.pf-tabs{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin:26px 0 30px}
.pf-tab{display:inline-flex;align-items:center;gap:8px;padding:9px 16px;border-radius:999px;border:1px solid var(--line);
  background:var(--bg1);color:var(--ink2);font-size:13px;cursor:pointer;transition:all .18s}
.pf-tab:hover{border-color:var(--line2);color:var(--ink)}
.pf-tab.on{background:linear-gradient(145deg,#E2BC68,#B8902F);color:#1a1508;border-color:transparent;font-weight:600}
.pf-tn{font-family:'IBM Plex Mono',monospace;font-size:10px;opacity:.7}
.pf-stage{display:grid;grid-template-columns:0.85fr 1.15fr;gap:34px;align-items:center}
.pf-copy .pf-title{font-family:'Fraunces',serif;font-size:26px;line-height:1.15;margin:0 0 12px;letter-spacing:-.01em}
.pf-blurb{color:var(--ink2);font-size:15px;line-height:1.6;margin:0 0 20px}
.pf-steps{display:flex;flex-direction:column;gap:2px}
.pf-step{text-align:left;background:none;border:none;border-left:2px solid var(--line);padding:7px 14px;color:var(--ink3);
  font-size:13.5px;cursor:pointer;transition:all .15s}
.pf-step:hover{color:var(--ink2)}
.pf-step.on{border-left-color:var(--gold2);color:var(--ink);font-weight:600}
.pf-screenwrap{min-width:0}
/* generic screen chrome */
.pf-scr{background:#0E1014;border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 24px 60px rgba(0,0,0,.5)}
.pf-scrbar{display:flex;align-items:center;gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);background:#13161C}
.pf-dot{width:9px;height:9px;border-radius:999px;background:#2E3542}
.pf-scrname{margin-left:8px;font-size:12px;color:var(--ink3)}
.pf-pill{margin-left:auto;font-size:10px;padding:3px 9px;border-radius:999px;border:1px solid var(--line)}
.pf-em{color:#3FA47A;border-color:rgba(63,164,122,.4)}
/* screen 1 · extract */
.pf-extract{display:grid;grid-template-columns:1fr 1.15fr;gap:0}
.pf-doc{padding:16px;border-right:1px solid var(--line);display:flex;flex-direction:column;gap:9px;background:#0B0D11}
.pf-docline{height:7px;border-radius:3px;background:#20262F}
.pf-docband{display:flex;justify-content:space-between;font-size:11px;font-family:'IBM Plex Mono',monospace;color:#0E1014;
  background:rgba(63,164,122,.85);padding:3px 8px;border-radius:4px}
.pf-docband.v{background:rgba(224,162,59,.85)}
.pf-fields{padding:14px 16px}
.pf-fh{font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:var(--ink3);margin-bottom:10px}
.pf-frow{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid rgba(38,44,56,.6)}
.pf-fk{flex:1;font-size:12.5px;color:var(--ink3)}
.pf-fv{font-size:12.5px;font-weight:600}
.pf-ftick{font-size:11px}
/* screen 2 · intel */
.pf-intel{padding:18px}
.pf-chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:16px}
.pf-chip{font-size:11.5px;padding:4px 11px;border-radius:999px;border:1px solid var(--c);color:var(--c);
  background:color-mix(in srgb,var(--c) 12%,transparent)}
.pf-watch{display:flex;flex-direction:column;gap:2px}
.pf-wrow{display:flex;align-items:center;gap:10px;padding:9px 4px;border-bottom:1px solid rgba(38,44,56,.6)}
.pf-wdot{width:8px;height:8px;border-radius:999px;flex:0 0 auto}
.pf-wt{flex:1;font-size:13px;color:var(--ink)}
.pf-wl{font-size:11.5px;color:var(--ink3)}
.pf-wd{font-size:12.5px;font-weight:600;font-family:'IBM Plex Mono',monospace}
.pf-intelnote{margin-top:14px;font-size:11.5px;color:var(--ink3);font-style:italic}
/* screen 3 · dash */
.pf-dash{padding:18px}
.pf-kpis{display:grid;grid-template-columns:1fr 1fr 1fr;gap:9px;margin-bottom:16px}
.pf-kpi{border:1px solid var(--line);border-left:3px solid var(--c);border-radius:11px;padding:11px 12px;
  background:linear-gradient(180deg,color-mix(in srgb,var(--c) 8%,#13161C),#13161C)}
.pf-kl{display:block;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink3);margin-bottom:6px}
.pf-kv{font-family:'Fraunces',serif;font-size:21px;color:var(--c)}
.pf-chart{display:flex;align-items:flex-end;gap:7px;height:78px;padding:8px 4px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.pf-chart span{flex:1;border-radius:3px 3px 0 0;min-height:6px}
.pf-ai{display:flex;align-items:flex-start;gap:7px;margin-top:14px;font-size:12.5px;color:var(--ink2);line-height:1.5}
.pf-aidot{color:var(--gold2)}
/* screen 4 · chat */
.pf-chat{padding:16px;display:flex;flex-direction:column;gap:11px;background:#0B0D11}
.pf-bub{max-width:86%;font-size:13px;line-height:1.5;padding:9px 13px;border:1px solid var(--line);box-shadow:0 6px 18px rgba(0,0,0,.35)}
.pf-bub.you{align-self:flex-end;background:rgba(226,188,104,.15);border-color:rgba(226,188,104,.35);border-radius:15px 15px 4px 15px}
.pf-bub.ai{align-self:flex-start;background:#13161C;border-radius:15px 15px 15px 4px}
.pf-cites{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.pf-cites span,.pf-cite{font-size:10px;font-family:'IBM Plex Mono',monospace;color:var(--gold2);
  border:1px solid rgba(226,188,104,.3);border-radius:5px;padding:1px 6px}
.pf-cite{border:none;padding:0}
.pf-composer{display:flex;gap:8px;align-items:center;margin-top:4px}
.pf-cinput{flex:1;font-size:12.5px;color:var(--ink3);background:#13161C;border:1px solid var(--line);border-radius:999px;padding:10px 14px}
.pf-csend{width:36px;height:36px;flex:0 0 auto;border-radius:999px;display:grid;place-items:center;color:#1a1508;
  background:linear-gradient(145deg,#E2BC68,#B8902F);font-weight:700}
@media(max-width:820px){
  .pf-stage{grid-template-columns:1fr;gap:22px}
  .pf-copy .pf-title{font-size:22px}
  .pf-steps{display:none}
  .pf-extract{grid-template-columns:1fr}
  .pf-doc{border-right:none;border-bottom:1px solid var(--line)}
}
`;
