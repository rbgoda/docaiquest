// Developer · self-serve API keys. A logged-in user mints keys scoped to THEIR OWN documents and
// uses them from their own app (or an MCP client). The raw key is shown exactly once on creation.
import { useEffect, useState } from "react";
import { listApiKeys, createApiKey, revokeApiKey } from "../api/documents";

const BASE = typeof window !== "undefined" ? window.location.origin : "https://docaiq.jicama.tech";

// Ready-to-import OpenAPI schema for a ChatGPT Custom GPT Action (askDocuments +
// listDocuments over /api/v1). Copied verbatim from
// docs/integrations/chatgpt-custom-gpt.openapi.yaml — keep the two in sync.
const CGPT_SCHEMA = `openapi: 3.1.0
info:
  title: DocAIQ — Your Documents
  description: Ask questions across the API-key owner's own documents and list them, with source citations.
  version: "1.0.0"
servers:
  - url: ${BASE}
paths:
  /api/v1/ask:
    post:
      operationId: askDocuments
      summary: Ask a question across the user's documents
      description: RAG answer over the key owner's documents — counts, dates, amounts, people, summaries, "list all documents of <person>". Returns a grounded answer with citations.
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/AskRequest' }
      responses:
        '200':
          description: A grounded answer with source citations.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/AskResponse' }
  /api/v1/documents:
    get:
      operationId: listDocuments
      summary: List the user's processed documents
      parameters:
        - name: limit
          in: query
          schema: { type: integer, default: 100, minimum: 1, maximum: 500 }
      responses:
        '200':
          description: The list of ready documents.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/DocumentsResponse' }
components:
  securitySchemes:
    BearerAuth: { type: http, scheme: bearer }
  schemas:
    AskRequest:
      type: object
      required: [question]
      properties:
        question: { type: string, maxLength: 4000 }
        topK: { type: integer, default: 8, minimum: 1, maximum: 20 }
        history:
          type: array
          items:
            type: object
            properties:
              role: { type: string, enum: [user, ai] }
              text: { type: string }
    AskResponse:
      type: object
      properties:
        answer: { type: string }
        grounded: { type: boolean }
        confidence: { type: string, enum: [high, low, none] }
        citations:
          type: array
          items:
            type: object
            properties:
              docId: { type: string }
              name: { type: string }
              page: { type: integer }
              quote: { type: string }
    DocumentsResponse:
      type: object
      properties:
        documents:
          type: array
          items:
            type: object
            properties:
              id: { type: string }
              name: { type: string }
              type: { type: string }
              createdAt: { type: string, format: date-time }
        count: { type: integer }
security:
  - BearerAuth: []
`;

function Row({ k, onRevoke }) {
  return (
    <tr style={{ borderBottom: "1px solid var(--line)", opacity: k.revoked ? 0.45 : 1 }}>
      <td style={{ padding: "9px 8px", fontSize: 13 }}>{k.name}</td>
      <td style={{ padding: "9px 8px", fontFamily: "var(--mono, monospace)", fontSize: 12, color: "var(--ink2)" }}>{k.keyPrefix}</td>
      <td style={{ padding: "9px 8px", fontSize: 11, color: "var(--ink3)" }}>{(k.scopes || []).join(", ")}</td>
      <td style={{ padding: "9px 8px", fontSize: 11, color: "var(--ink3)" }}>{k.lastUsedAt ? new Date(k.lastUsedAt).toLocaleDateString() : "never"}</td>
      <td style={{ padding: "9px 8px", textAlign: "right" }}>
        {k.revoked
          ? <span style={{ fontSize: 11, color: "var(--ink3)" }}>revoked</span>
          : <button onClick={() => onRevoke(k)} className="border bg2 hover-bg"
              style={{ padding: "4px 10px", borderRadius: 7, fontSize: 11, cursor: "pointer", color: "var(--rose, #d8625e)" }}>Revoke</button>}
      </td>
    </tr>
  );
}

export default function DeveloperKeys() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState(null);   // the just-created raw key (shown once)
  const [copied, setCopied] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [schemaCopied, setSchemaCopied] = useState(false);

  const load = async () => {
    setLoading(true);
    try { setKeys((await listApiKeys()).keys || []); setErr(""); }
    catch (e) { setErr(e?.detail || "Couldn't load your keys."); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    setCreating(true); setErr("");
    try {
      const r = await createApiKey(name.trim() || "API key");
      setFresh(r.key); setName(""); setCopied(false);
      await load();
    } catch (e) { setErr(e?.detail || "Couldn't create a key."); }
    finally { setCreating(false); }
  };
  const revoke = async (k) => {
    if (!window.confirm(`Revoke "${k.name}"? Any app using it stops working immediately.`)) return;
    try { await revokeApiKey(k.id); await load(); }
    catch (e) { setErr(e?.detail || "Couldn't revoke that key."); }
  };
  const copy = () => { navigator.clipboard?.writeText(fresh); setCopied(true); };
  const copySchema = () => {
    navigator.clipboard?.writeText(CGPT_SCHEMA);
    setSchemaCopied(true);
    setTimeout(() => setSchemaCopied(false), 1600);
  };

  const active = keys.filter((k) => !k.revoked).length;

  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, margin: "0 0 4px" }}>API keys</h1>
      <p style={{ color: "var(--ink2)", fontSize: 14, margin: "0 0 20px" }}>
        Build on your own documents. A key is scoped to <b>your</b> data only — it can answer questions,
        list your documents, and run extraction from your own app or an AI assistant.
      </p>

      {fresh && (
        <div style={{ background: "rgba(79,178,134,.08)", border: "1px solid #2f5c48", borderRadius: 12, padding: 16, marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>✅ Key created — copy it now, it won’t be shown again</div>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <code style={{ flex: 1, fontFamily: "var(--mono, monospace)", fontSize: 13, background: "var(--bg1, #0f141c)", padding: "9px 12px", borderRadius: 8, overflowX: "auto", whiteSpace: "nowrap" }}>{fresh}</code>
            <button onClick={copy} className="btn-gold" style={{ padding: "8px 14px", borderRadius: 8, fontSize: 12 }}>{copied ? "Copied ✓" : "Copy"}</button>
            <button onClick={() => setFresh(null)} className="border bg2" style={{ padding: "8px 12px", borderRadius: 8, fontSize: 12 }}>Done</button>
          </div>
        </div>
      )}

      <div className="row gap-2" style={{ alignItems: "center", marginBottom: 16 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Key name (e.g. My app · prod)"
          onKeyDown={(e) => e.key === "Enter" && create()}
          style={{ flex: 1, maxWidth: 320, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--line)", background: "var(--bg1)", color: "inherit", font: "inherit" }} />
        <button onClick={create} disabled={creating || active >= 10} className="btn-gold"
          style={{ padding: "8px 16px", borderRadius: 8, fontSize: 13, opacity: creating || active >= 10 ? 0.6 : 1 }}>
          {creating ? "Creating…" : "+ Create key"}
        </button>
        {active >= 10 && <span style={{ fontSize: 11, color: "var(--ink3)" }}>10-key limit reached</span>}
      </div>

      {err && <div style={{ color: "var(--rose, #d8625e)", fontSize: 13, marginBottom: 12 }}>{err}</div>}

      {loading ? <div style={{ color: "var(--ink3)", padding: 20 }}>Loading…</div> : (
        keys.length === 0
          ? <div style={{ color: "var(--ink3)", padding: "26px 0", fontSize: 14 }}>No keys yet. Create one above to start building.</div>
          : (
            <div style={{ border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ background: "var(--bg2, #171c24)" }}>
                    {["Name", "Key", "Scopes", "Last used", ""].map((h, i) => (
                      <th key={i} style={{ padding: "9px 8px", textAlign: i === 4 ? "right" : "left", fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--ink3)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>{keys.map((k) => <Row key={k.id} k={k} onRevoke={revoke} />)}</tbody>
              </table>
            </div>
          )
      )}

      <div style={{ marginTop: 26, borderTop: "1px solid var(--line)", paddingTop: 20 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>Use your key</h2>
        <pre style={{ background: "var(--bg1, #0f141c)", border: "1px solid var(--line)", borderRadius: 10, padding: 14, overflowX: "auto", fontSize: 12, lineHeight: 1.55, margin: 0 }}>
{`# Ask a question about YOUR documents
curl ${BASE}/api/v1/ask \\
  -H "X-API-Key: dq_live_…" \\
  -H "Content-Type: application/json" \\
  -d '{"question": "Which invoices are due this month?"}'

# List your documents
curl ${BASE}/api/v1/documents -H "X-API-Key: dq_live_…"

# Extract structured fields from a file (stateless)
curl ${BASE}/api/extraction/extract \\
  -H "X-API-Key: dq_live_…" -F file=@invoice.pdf`}
        </pre>
        <p style={{ color: "var(--ink3)", fontSize: 12, marginTop: 10 }}>
          Same key works with the DocAIQ Python/JS SDKs and the MCP server. Full reference at <code>{BASE}/api/docs</code>.
        </p>
      </div>

      {/* Connect to a third-party AI assistant (ChatGPT / Claude / Gemini) */}
      <div style={{ marginTop: 26, borderTop: "1px solid var(--line)", paddingTop: 20 }}>
        <button onClick={() => setConnectOpen(o => !o)}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "inherit", display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>Connect to ChatGPT, Claude or Gemini</span>
          <span style={{ fontSize: 12, color: "var(--ink3)" }}>{connectOpen ? "▾" : "▸"}</span>
        </button>
        <p style={{ color: "var(--ink3)", fontSize: 12, margin: "6px 0 0" }}>
          Let an AI assistant answer questions grounded in your documents, with citations.
        </p>

        {connectOpen && (
          <div style={{ marginTop: 14 }}>
            {/* ChatGPT */}
            <div style={{ background: "var(--bg2, #171c24)", border: "1px solid var(--line)", borderRadius: 12, padding: 16, marginBottom: 14 }}>
              <div className="row between" style={{ alignItems: "center", marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>🤖 ChatGPT — Custom GPT</span>
                <button onClick={copySchema} className="btn-gold" style={{ padding: "6px 12px", borderRadius: 8, fontSize: 12, cursor: "pointer" }}>
                  {schemaCopied ? "Schema copied ✓" : "Copy OpenAPI schema"}
                </button>
              </div>
              <ol style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 13, lineHeight: 1.65, color: "var(--ink2)" }}>
                <li><b>Create a key</b> above (<code>dq_live_…</code>) and copy it.</li>
                <li>In ChatGPT: <b>Explore GPTs → Create → Configure → Actions → Create new action</b>.</li>
                <li>Click <b>Copy OpenAPI schema</b> here and paste it into the Action’s <b>Schema</b> box.</li>
                <li>Under <b>Authentication</b>: type <b>API Key</b>, auth type <b>Bearer</b>, paste your <code>dq_live_…</code> key.</li>
                <li>Test it: <i>“How many invoices do I have?”</i> — it answers from your documents with sources.</li>
              </ol>
              <p style={{ color: "var(--ink3)", fontSize: 11.5, margin: "10px 0 0" }}>
                Requires ChatGPT Plus/Team/Enterprise (Custom GPTs). The schema targets <code>{BASE}/api/v1</code>.
              </p>
            </div>

            {/* Claude */}
            <div style={{ background: "var(--bg2, #171c24)", border: "1px solid var(--line)", borderRadius: 12, padding: 16, marginBottom: 14 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>✳️ Claude — MCP connector</div>
              <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.65, color: "var(--ink2)" }}>
                <li>In Claude (Desktop or claude.ai Pro/Team): <b>Settings → Connectors → Add custom connector</b>.</li>
                <li>URL: <code>{BASE}/api/mcp</code></li>
                <li>Authentication: <b>Bearer</b> — your <code>dq_live_…</code> key.</li>
                <li>Ask away — Claude calls the <code>ask_documents</code> tool automatically.</li>
              </ol>
            </div>

            {/* Gemini / other */}
            <div style={{ background: "var(--bg2, #171c24)", border: "1px solid var(--line)", borderRadius: 12, padding: 16 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>♦ Gemini &amp; other apps — REST</div>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--ink2)" }}>
                Register <code>POST {BASE}/api/v1/ask</code> as a function/tool (Bearer <code>dq_live_…</code>), or call it
                directly from any app. Interactive reference: <code>{BASE}/api/docs</code>.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
