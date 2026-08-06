// Official TypeScript/JavaScript SDK for the DocAIQ document-intelligence API.
//
// Zero runtime dependencies: uses the global `fetch` and `FormData` (Node 18+
// and all modern browsers). Every request is authenticated with an owner-scoped
// API key sent in the `X-API-Key` header (a key looks like `dq_live_...`).

export class DocaiqClient {
  /**
   * @param {{ apiKey: string, baseUrl?: string }} opts
   */
  constructor({ apiKey, baseUrl = "https://docaiq.jicama.tech" }) {
    if (!apiKey) throw new Error("apiKey is required");
    this.apiKey = apiKey;
    // Strip trailing slash so path joins are predictable.
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  /**
   * Perform an authenticated request and throw on non-2xx.
   * @private
   */
  async _request(method, path, { body, headers = {} } = {}) {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { "X-API-Key": this.apiKey, ...headers },
      body,
    });

    if (!resp.ok) {
      // Prefer the API's `detail` message when the body is JSON.
      let detail;
      try {
        const data = await resp.json();
        detail = data && data.detail;
      } catch {
        try {
          detail = await resp.text();
        } catch {
          detail = null;
        }
      }
      throw new Error(detail || `HTTP ${resp.status}`);
    }

    return resp.json();
  }

  /**
   * Ask a grounded question across the owner's documents.
   * @param {string} question
   * @param {{ topK?: number }} [opts]
   * @returns {Promise<{ answer: string, grounded: boolean, confidence?: number, citations: any[] }>}
   */
  async ask(question, { topK = 8 } = {}) {
    return this._request("POST", "/api/v1/ask", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, topK }),
    });
  }

  /**
   * List the owner's documents, returning the `documents` array.
   * @param {{ limit?: number }} [opts]
   * @returns {Promise<Array<{ id: string, name: string, type: string, createdAt: string }>>}
   */
  async documents({ limit = 100 } = {}) {
    const data = await this._request("GET", `/api/v1/documents?limit=${limit}`);
    return data.documents || [];
  }

  /**
   * Extract structured fields from a single document file.
   * @param {Blob|File} file
   * @param {string} [filename="file"]
   * @returns {Promise<{ status: string, docType: string, fields: object, citations: any[], confidence?: number }>}
   */
  async extract(file, filename = "file") {
    const form = new FormData();
    form.append("file", file, filename);
    // Do NOT set Content-Type: fetch sets the multipart boundary automatically.
    return this._request("POST", "/api/extraction/extract", { body: form });
  }
}

export default DocaiqClient;
