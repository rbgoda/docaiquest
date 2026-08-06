// Neutralize dangerous URL schemes before a document/model-derived markdown link
// is rendered. javascript:, data:, vbscript: etc. in an <a href> execute on click
// (and, in the innerHTML editor path, an unescaped quote lets the URL break out of
// the attribute). Documents can be shared to other users, so a crafted link in one
// user's doc is a cross-user (stored) XSS vector. Allow only http/https/mailto/tel
// and relative URLs; everything else -> "" (inert href).
export function safeUrl(url) {
  const s = String(url == null ? "" : url).trim();
  if (!s) return "";
  // No valid URL contains an unencoded " ' < > or backtick - their presence is an
  // attribute-breakout attempt (e.g. https://a" onmouseover="x). Reject outright.
  if (/["'<>`]/.test(s)) return "";
  // Relative / anchor / query - no scheme, cannot execute.
  if (/^[/#?.]/.test(s)) return s;
  // Strip whitespace + control chars browsers ignore inside a scheme (tab/newline
  // obfuscation like java<TAB>script:) before testing, so obfuscation cannot pass.
  const probe = s.replace(/[\s\u0000-\u001f]/g, "").toLowerCase();
  const m = probe.match(/^([a-z][a-z0-9+.-]*):/);
  if (!m) return s; // no scheme -> relative/plain link, safe
  return ["http", "https", "mailto", "tel"].includes(m[1]) ? s : "";
}
