// Thin fetch wrapper. All views go through src/api/index.js, which calls these.
// Keeping this minimal on purpose — when we add auth/tenant headers, retries,
// or swap to React Query, this is the single seam to change.

const BASE = "/api";

export class ApiError extends Error {
  // `message` is the user-facing text — `detail.message` (when structured)
  // or `detail` string from the API, else a friendly fallback. `body` is
  // the full parsed JSON when present (lets call-sites read structured
  // detail.code / detail.closedAudits etc for 4xx with structured payloads,
  // e.g. M29 doc-delete 409). Technical fields (method, url, status) for
  // devs reading devtools / Sentry.
  constructor(message, { status, url, method, body } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
    this.method = method;
    this.body = body;
  }
}

// Map HTTP statuses to a user-friendly fallback when the API didn't provide
// a `detail` string. Keeps call-site logic dumb: just show err.message.
function friendlyFallback(status) {
  switch (status) {
    case 400: return "Request was invalid.";
    case 401: return "You're not signed in.";
    case 403: return "You don't have permission for that.";
    case 404: return "Not found.";
    case 409: return "That conflicts with existing data.";
    case 413: return "That file is too large.";
    case 422: return "Some inputs were invalid.";
    case 429: return "Too many requests — slow down.";
    default:
      if (status >= 500) return "The server hit an error. Try again shortly.";
      return "Request failed.";
  }
}

// Listeners for global auth events. Set via setUnauthorizedHandler so that
// AuthContext can drop the user back to the login screen on a 401 from any
// endpoint without each call site having to handle it.
let unauthorizedHandler = null;
export function setUnauthorizedHandler(fn) { unauthorizedHandler = fn; }

async function request(method, path, { signal, body } = {}) {
  const url = `${BASE}${path}`;
  const init = {
    method,
    signal,
    credentials: "same-origin",  // send the session cookie
    headers: { Accept: "application/json" },
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn't reach the server.", { url, method });
  }
  if (!res.ok) {
    let body = null;
    let detail = "";
    try {
      body = await res.json();
      const d = body?.detail;
      // FastAPI allows detail to be a plain string OR a structured object.
      // Surface .message when structured so the UI gets human-readable text
      // by default; structured fields are still on err.body.detail.
      detail = (typeof d === "string" ? d : d?.message) || "";
    } catch { /* not JSON */ }
    const userMessage = detail || friendlyFallback(res.status);
    const err = new ApiError(userMessage, { status: res.status, url, method, body });
    // A 401 means "drop to anon" ONLY when it's the identity check itself
    // (/me, /auth/*). A 401 from a FEATURE endpoint (e.g. a stale Drive token on
    // /connectors/drive/restore/status) must NOT nuke a valid session — that bug
    // bounced freshly-logged-in users straight back to the landing page. Such a
    // 401 just fails its own request; the caller handles it.
    const isAuthIdentityPath = path === "/me" || path.startsWith("/auth/");
    if (res.status === 401 && unauthorizedHandler && isAuthIdentityPath) {
      unauthorizedHandler();
    }
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const get = (path, opts) => request("GET", path, opts);
export const patch = (path, body, opts) => request("PATCH", path, { ...opts, body });
export const post = (path, body, opts) => request("POST", path, { ...opts, body });
export const put = (path, body, opts) => request("PUT", path, { ...opts, body });
export const del = (path, opts) => request("DELETE", path, opts);
