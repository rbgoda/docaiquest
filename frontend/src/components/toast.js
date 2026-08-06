// Lightweight toast/snackbar — no provider or root mount needed. Appends a fixed, auto-dismissing,
// aria-live element to <body> and styles it with the app's theme tokens. Call toast("Saved").
export function toast(message, { type = "ok", ms = 2800 } = {}) {
  if (typeof document === "undefined") return;
  let host = document.getElementById("docaiq-toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "docaiq-toasts";
    host.setAttribute("aria-live", "polite");
    host.style.cssText =
      "position:fixed;bottom:22px;left:50%;transform:translateX(-50%);z-index:9999;" +
      "display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none";
    document.body.appendChild(host);
  }
  const accent = type === "err" ? "var(--rose,#D8625E)" : type === "info" ? "var(--gold2,#E2BC68)" : "var(--emerald,#3FA47A)";
  const el = document.createElement("div");
  el.setAttribute("role", "status");
  el.textContent = message;
  el.style.cssText =
    "pointer-events:auto;background:var(--bg2,#181C24);color:var(--ink,#E7E9EE);" +
    `border:1px solid var(--line,#262C38);border-left:3px solid ${accent};border-radius:10px;` +
    "padding:10px 16px;font-size:13px;line-height:1.4;max-width:90vw;" +
    "box-shadow:0 12px 34px rgba(0,0,0,.35);opacity:0;transform:translateY(8px);" +
    "transition:opacity .18s ease,transform .18s ease";
  host.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = "1"; el.style.transform = "translateY(0)"; });
  const kill = () => { el.style.opacity = "0"; el.style.transform = "translateY(8px)"; setTimeout(() => el.remove(), 220); };
  const timer = setTimeout(kill, ms);
  el.addEventListener("click", () => { clearTimeout(timer); kill(); });
}

export default toast;
