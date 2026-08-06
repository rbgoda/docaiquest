// Minimal API layer. Same patterns as the main frontend — session cookie auth,
// same-origin credentials, JSON error handling.

const BASE = "/api";

class ApiError extends Error {
  constructor(message, { status } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(method, path, { body, signal, raw } = {}) {
  const url = `${BASE}${path}`;
  const init = { method, signal, credentials: "same-origin" };

  if (body !== undefined && !(body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  } else if (body instanceof FormData) {
    init.body = body;
  }

  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn't reach the server.");
  }

  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      const d = data?.detail;
      detail = (typeof d === "string" ? d : d?.message) || "";
    } catch {}
    throw new ApiError(detail || `Request failed (${res.status})`, { status: res.status });
  }

  if (raw) return res;
  if (res.status === 204) return null;
  return res.json();
}

// -- Auth --
export const signup = (email, password, name) =>
  request("POST", "/auth/register", { body: { email, password, name } });

export const login = (email, password) =>
  request("POST", "/auth/login", { body: { email, password } });

export const whoami = () => request("GET", "/me");

// -- Documents --
export const listDocuments = () => request("GET", "/documents");

export const getDocument = (id) => request("GET", `/documents/${id}`);

export const uploadDocument = (file, { signal } = {}) => {
  const form = new FormData();
  form.append("file", file);
  return request("POST", "/documents", { body: form, signal });
};

export const deleteDocument = (id) => request("DELETE", `/documents/${id}`);

export const documentFileUrl = (id) => `${BASE}/documents/${id}/file`;

// -- Chat --
export const fetchChat = (docId) => request("GET", `/documents/${docId}/chat`);

export const sendMessage = (docId, text) =>
  request("POST", `/documents/${docId}/chat/messages`, { body: { text } });
