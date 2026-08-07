import { useCallback, useEffect, useRef, useState } from "react";
import {
  whoami, login, signup,
  listDocuments, uploadDocument, deleteDocument, documentFileUrl,
  fetchDocChat, sendDocMessage, fetchWorkspaceChat, sendWorkspaceMessage,
  setConsent,
  getLlmSettings, setLlmSettings, probeProvider,
} from "./api";

// ---- Auth forms -----------------------------------------------------------

function LoginForm({ onLogin, onSwitch }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      const user = await whoami();
      onLogin(user);
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  };

  return (
    <form className="auth-form" onSubmit={submit}>
      <h1>DocAIQuest</h1>
      <p className="auth-sub">Sign in to your documents</p>
      {error && <div className="auth-error">{error}</div>}
      <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      <button type="button" className="demo-quick"
        onClick={() => { setEmail("demo@docaiquest.dev"); setPassword("docaiquest"); setError(""); }}>
        🧪 Quick Demo Login
      </button>
      <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      <p className="auth-switch">
        No account?{" "}
        <button type="button" className="link" onClick={onSwitch}>Create one</button>
      </p>
    </form>
  );
}

function SignupForm({ onLogin, onSwitch }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsentChecked] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await signup(email, password, name, consent);
      const user = await whoami();
      onLogin(user);
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  };

  return (
    <form className="auth-form" onSubmit={submit}>
      <h1>DocAIQuest</h1>
      <p className="auth-sub">Create your account</p>
      {error && <div className="auth-error">{error}</div>}
      <input type="text" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required />
      <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      <label className="consent-row">
        <input type="checkbox" checked={consent} onChange={(e) => setConsentChecked(e.target.checked)} />
        <span>I consent to processing — documents are sent to third-party AI providers for extraction and chat. My data stays private to my account.</span>
      </label>
      <button type="submit" disabled={busy || !consent}>{busy ? "Creating…" : "Create account"}</button>
      <p className="auth-switch">
        Already have one?{" "}
        <button type="button" className="link" onClick={onSwitch}>Sign in</button>
      </p>
    </form>
  );
}

// ---- Document list panel ---------------------------------------------------

function DocList({ docs, selectedId, onSelect, onUpload, onDelete, uploading, collapsed, onToggleCollapse, sidebarWidth, onSidebarResize }) {
  const fileRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);

  const onResizeStart = useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = sidebarWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const onMove = (ev) => {
      onSidebarResize(Math.max(180, Math.min(500, startW + (ev.clientX - startX))));
    };
    const onUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sidebarWidth, onSidebarResize]);

  const handleFile = useCallback((file) => {
    if (file) onUpload(file);
    if (fileRef.current) fileRef.current.value = "";
  }, [onUpload]);

  const onDragOver = (e) => { e.preventDefault(); setDragOver(true); };
  const onDragLeave = () => setDragOver(false);
  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    handleFile(file);
  };

  return (
    <>
      {/* Expand handle when collapsed */}
      {collapsed && (
        <div className="sidebar-collapsed-strip" onClick={onToggleCollapse} title="Show documents">
          <span className="sidebar-expand-arrow">▸</span>
        </div>
      )}
      <div className={`doc-list${collapsed ? " collapsed" : ""}`} style={{ width: collapsed ? 0 : sidebarWidth }}>
        <div className="doc-list-header">
          <h2>Documents</h2>
          <button
            className="sidebar-toggle"
            onClick={onToggleCollapse}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? "▸" : "◂"}
          </button>
        </div>

        {/* Upload zone */}
        <div
          className={`upload-zone${dragOver ? " drag-over" : ""}`}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          {uploading ? (
            <span className="spinner" />
          ) : (
            <>
              <span className="upload-icon">+</span>
              <span>Drop a file or click to upload</span>
            </>
          )}
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.xlsx,.csv,.tsv,.pptx,.eml,.html,.txt,.md,.png,.jpg,.jpeg,.heic,.avif"
            style={{ display: "none" }}
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
        </div>

        {/* Document list */}
        <div className="doc-items">
          {docs.length === 0 && !uploading && (
            <p className="empty-hint">Upload your first document.</p>
          )}
          {docs.map((doc) => (
            <div
              key={doc.id}
              className={`doc-item${doc.id === selectedId ? " selected" : ""}`}
              onClick={() => onSelect(doc)}
            >
              <span className="doc-icon">{doc.mimeType === "application/pdf" ? "📄" : "📃"}</span>
              <span className="doc-name" title={doc.name}>{doc.name}</span>
              <button
                className="doc-delete"
                title="Delete"
                onClick={(e) => { e.stopPropagation(); onDelete(doc); }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>
      {/* Resize handle — only when not collapsed */}
      {!collapsed && (
        <div className="sidebar-resize-handle" onMouseDown={onResizeStart}>
          <span className="resize-grip-vert" />
        </div>
      )}
    </>
  );
}

// ---- Chat panel ------------------------------------------------------------

function ChatPanel({ doc, docId, docs, onClose }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [fileUrl, setFileUrl] = useState("");
  const [previewOpen, setPreviewOpen] = useState(true);
  const [previewHeight, setPreviewHeight] = useState(260);
  const [zoom, setZoom] = useState(1.0);
  const previewRef = useRef(null);
  const bottomRef = useRef(null);

  // Load chat history when doc changes (or switch to workspace chat)
  useEffect(() => {
    if (docId) {
      setFileUrl(documentFileUrl(docId));
      fetchDocChat(docId)
        .then((data) => setMessages(data.messages || []))
        .catch(() => setMessages([]));
    } else {
      setFileUrl("");
      fetchWorkspaceChat()
        .then((data) => setMessages(data.messages || []))
        .catch(() => setMessages([]));
    }
  }, [docId]);

  // Reset zoom and height when doc changes
  useEffect(() => {
    setZoom(1.0);
    setPreviewHeight(260);
  }, [docId]);

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Drag resize handler for document preview
  const onResizeStart = useCallback((e) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = previewRef.current?.offsetHeight || previewHeight;
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";

    const onMove = (ev) => {
      const delta = ev.clientY - startY;
      setPreviewHeight(Math.max(100, Math.min(600, startH + delta)));
    };
    const onUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [previewHeight]);

  const send = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setLoading(true);

    // Optimistically add user message
    const userMsg = { role: "user", text, id: Date.now().toString() };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const data = docId
        ? await sendDocMessage(docId, text)
        : await sendWorkspaceMessage(text);
      setMessages((prev) => [...prev, { ...data, role: data.role || "ai" }]);
    } catch (err) {
      setMessages((prev) => [...prev, { role: "ai", text: `Error: ${err.message}`, id: (Date.now() + 2).toString(), error: true }]);
    }
    setLoading(false);
  };

  // Compute aggregate workspace stats from docs array
  const totalDocs = (docs || []).length;
  const totalPages = (docs || []).reduce((sum, d) => sum + (d.pages || 0), 0);
  const readyDocs = (docs || []).filter((d) => d.ingestionStatus === "ready").length;
  const mimeTypes = new Set((docs || []).map((d) => d.mimeType).filter(Boolean));
  const typeLabel = mimeTypes.size === 1 ? [...mimeTypes][0].replace("application/", "") : `${mimeTypes.size} types`;

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <h2>{doc ? doc.name : "All Documents"}</h2>
        <div className="chat-header-actions">
          {fileUrl && previewOpen && (
            <>
              <button className="zoom-btn" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} title="Zoom out">−</button>
              <span className="zoom-label">{Math.round(zoom * 100)}%</span>
              <button className="zoom-btn" onClick={() => setZoom((z) => Math.min(2.0, z + 0.25))} title="Zoom in">+</button>
            </>
          )}
          {fileUrl && (
            <button
              className="preview-toggle"
              onClick={() => setPreviewOpen((v) => !v)}
              title={previewOpen ? "Hide document preview" : "Show document preview"}
            >
              {previewOpen ? "▼" : "▲"} Preview
            </button>
          )}
          {doc && onClose && <button className="chat-close" onClick={onClose}>×</button>}
        </div>
      </div>

      {/* Document embed */}
      {fileUrl && (
        <>
          <div
            className={`doc-preview${previewOpen ? "" : " collapsed"}`}
            ref={previewRef}
            style={{ height: previewOpen ? previewHeight : 0 }}
          >
            <div className="doc-preview-viewport">
              <iframe
                src={fileUrl}
                title="Document preview"
                style={{
                  transform: `scale(${zoom})`,
                  transformOrigin: "top left",
                  width: `${100 / zoom}%`,
                  height: `${100 / zoom}%`,
                }}
              />
            </div>
          </div>
          {previewOpen && (
            <div className="preview-resize-handle" onMouseDown={onResizeStart}>
              <span className="resize-grip" />
            </div>
          )}
        </>
      )}

      {/* Messages */}
      <div className="chat-messages">
        {/* Aggregate stats bar */}
        {totalDocs > 0 && (
          <div className="stats-bar">
            <div className="stat-capsule">
              <span className="stat-icon">📄</span>
              <span className="stat-value">{totalDocs}</span>
              <span className="stat-label">{totalDocs === 1 ? "Doc" : "Docs"}</span>
            </div>
            <div className="stat-capsule">
              <span className="stat-icon">📑</span>
              <span className="stat-value">{totalPages}</span>
              <span className="stat-label">{totalPages === 1 ? "Page" : "Pages"}</span>
            </div>
            <div className="stat-capsule">
              <span className="stat-icon">✅</span>
              <span className="stat-value">{readyDocs}</span>
              <span className="stat-label">Ready</span>
            </div>
            <div className="stat-capsule">
              <span className="stat-icon">📁</span>
              <span className="stat-value">{typeLabel}</span>
              <span className="stat-label">Format</span>
            </div>
          </div>
        )}
        {messages.length === 0 && (
          <p className="empty-hint">Ask a question about this document.</p>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-msg ${msg.role}${msg.error ? " error" : ""}`}>
            <div className="chat-msg-text">{msg.text}</div>
            {msg.citations?.map((c, i) => (
              <div key={i} className="citation">
                📍 {c.name || "Document"}, page {c.page || "—"}
                {c.quote && <span className="quote"> — "{c.quote.slice(0, 120)}{c.quote.length > 120 ? "…" : ""}"</span>}
              </div>
            ))}
          </div>
        ))}
        {loading && <div className="chat-msg ai"><div className="chat-msg-text"><span className="spinner" /></div></div>}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form className="chat-input" onSubmit={send}>
        <input
          type="text"
          placeholder={docId ? "Ask about this document…" : "Ask about all documents…"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>Send</button>
      </form>
    </div>
  );
}

// ---- LLM Settings modal ----------------------------------------------------

const PROVIDER_OPTIONS = [
  { slug: "dashscope", label: "DashScope (Alibaba)", model: "qwen-max", embeds: true },
  { slug: "openai", label: "OpenAI", model: "gpt-4o", embeds: true },
  { slug: "anthropic", label: "Anthropic", model: "claude-sonnet-4-20250514", embeds: false },
  { slug: "google", label: "Google Gemini", model: "gemini-2.5-flash", embeds: true },
  { slug: "deepseek", label: "DeepSeek", model: "deepseek-chat", embeds: false },
  { slug: "openrouter", label: "OpenRouter", model: "openai/gpt-4o", embeds: true },
];

function SettingsModal({ onClose }) {
  const [provider, setProvider] = useState("dashscope");
  const [model, setModel] = useState("qwen-max");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState(null);
  const [loadedKey, setLoadedKey] = useState(false);

  // Load existing config on mount
  useEffect(() => {
    getLlmSettings().then((data) => {
      const provs = data.providers || [];
      const configured = provs.find((p) => p.configured);
      if (configured) {
        setProvider(configured.provider);
        setModel(configured.defaultModel || configured.recommendedModel || "");
        setEnabled(configured.enabled);
        setLoadedKey(true);
      }
    }).catch(() => {});
  }, []);

  const handleProviderChange = (slug) => {
    setProvider(slug);
    const opt = PROVIDER_OPTIONS.find((o) => o.slug === slug);
    if (opt) setModel(opt.model);
    setProbeResult(null);
  };

  const save = async () => {
    setSaving(true);
    try {
      await setLlmSettings(provider, { apiKey: apiKey || undefined, enabled, defaultModel: model });
      setLoadedKey(true);
      setApiKey("");
    } catch (err) {
      alert(err.message);
    }
    setSaving(false);
  };

  const probe = async () => {
    setProbing(true);
    try {
      const result = await probeProvider(provider);
      setProbeResult(result);
    } catch (err) {
      setProbeResult({ ok: false, error: err.message });
    }
    setProbing(false);
  };

  const selected = PROVIDER_OPTIONS.find((o) => o.slug === provider);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>⚙️ LLM Settings</h2>
          <button className="chat-close" onClick={onClose}>×</button>
        </div>
        <p className="modal-sub">
          Add an API key for chat and extraction. Embeddings run locally — no key needed.
        </p>

        <div className="settings-form">
          <label className="field-label">Provider</label>
          <select className="field-select" value={provider} onChange={(e) => handleProviderChange(e.target.value)}>
            {PROVIDER_OPTIONS.map((o) => (
              <option key={o.slug} value={o.slug}>
                {o.label}{o.embeds ? "" : " (chat only)"}
              </option>
            ))}
          </select>

          <label className="field-label">Model</label>
          <input
            type="text" className="field-input"
            value={model} onChange={(e) => setModel(e.target.value)}
            placeholder="e.g. qwen-max"
          />

          <label className="field-label">API Key</label>
          <input
            type="password" className="field-input"
            value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={loadedKey ? "Already set (enter new to replace)" : "sk-..."}
          />

          <label className="field-check">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <span>Enabled</span>
          </label>

          <div className="settings-actions">
            <button className="btn-sm btn-outline" onClick={probe} disabled={probing}>
              {probing ? "Testing…" : "Test connection"}
            </button>
            <button className="btn-sm btn-primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>

          {probeResult && (
            <div className={`probe-result ${probeResult.ok ? "ok" : "fail"}`}>
              {probeResult.ok
                ? `✓ Connected (${probeResult.latencyMs}ms)`
                : `✗ ${probeResult.error || "Connection failed"}`}
            </div>
          )}
        </div>

        {selected && (
          <p className="settings-footnote">
            {selected.embeds
              ? `${selected.label} supports both chat and API embeddings.`
              : `${selected.label} is chat-only. Embeddings run locally on your machine.`}
          </p>
        )}
      </div>
    </div>
  );
}

// ---- Main app --------------------------------------------------------------

export default function App() {
  const [user, setUser] = useState(null);
  const [authPage, setAuthPage] = useState("login"); // "login" | "signup"
  const [docs, setDocs] = useState([]);
  const [selectedDoc, setSelectedDoc] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [needConsent, setNeedConsent] = useState(false); // upload blocked on consent
  const [showSettings, setShowSettings] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(300);

  // Check existing session on mount
  useEffect(() => {
    whoami()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, []);

  // Load documents when user is set
  useEffect(() => {
    if (!user) return;
    refreshDocs();
  }, [user]);

  const refreshDocs = async () => {
    try {
      const data = await listDocuments();
      // API returns a map keyed by doc ID; convert to sorted array.
      const list = Object.values(data || {}).sort((a, b) => {
        const da = a.uploadedAt || ""; const db = b.uploadedAt || "";
        return db.localeCompare(da); // newest first
      });
      setDocs(list);
    } catch {}
  };

  const handleUpload = async (file) => {
    setUploading(true);
    try {
      await uploadDocument(file);
      await refreshDocs();
      setNeedConsent(false);
    } catch (err) {
      if (err.status === 400 && err.message?.includes?.("acknowledge")) {
        setNeedConsent(true);
      } else {
        alert(err.message);
      }
    }
    setUploading(false);
  };

  const handleConsent = async () => {
    try {
      await setConsent("personal_data");
      setNeedConsent(false);
    } catch (err) {
      alert(err.message);
    }
  };

  const handleDelete = async (doc) => {
    if (!confirm(`Delete "${doc.name}"?`)) return;
    try {
      await deleteDocument(doc.id);
      if (selectedDoc?.id === doc.id) setSelectedDoc(null);
      await refreshDocs();
    } catch (err) {
      alert(err.message);
    }
  };

  const handleSelect = (doc) => {
    setSelectedDoc((prev) => prev?.id === doc.id ? null : doc);
  };

  const handleLogout = () => {
    document.cookie = "session=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
    setUser(null);
    setDocs([]);
    setSelectedDoc(null);
  };

  // -- Checking session ----------------------------------------------------
  if (checking) {
    return <div className="loading-screen"><span className="spinner" /></div>;
  }

  // -- Not logged in -------------------------------------------------------
  if (!user) {
    return (
      <div className="auth-screen">
        {authPage === "login" ? (
          <LoginForm onLogin={setUser} onSwitch={() => setAuthPage("signup")} />
        ) : (
          <SignupForm onLogin={setUser} onSwitch={() => setAuthPage("login")} />
        )}
      </div>
    );
  }

  // -- Logged in — two-panel layout ----------------------------------------
  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-logo">DocAIQuest</span>
        <span className="app-user">
          <button className="link settings-btn" onClick={() => setShowSettings(true)} title="LLM Settings">⚙️</button>
          {user.email}
          <button className="link logout" onClick={handleLogout}>Sign out</button>
        </span>
      </header>
      {needConsent && (
        <div className="consent-banner">
          <span>Your documents may contain personal or health data. By uploading, you acknowledge that processing happens through third-party AI providers.</span>
          <button className="consent-ack-btn" onClick={handleConsent}>Acknowledge &amp; Continue</button>
        </div>
      )}
      <div className="app-body">
        <DocList
          docs={docs}
          selectedId={selectedDoc?.id}
          onSelect={handleSelect}
          onUpload={handleUpload}
          onDelete={handleDelete}
          uploading={uploading}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
          sidebarWidth={sidebarWidth}
          onSidebarResize={setSidebarWidth}
        />
        <ChatPanel
          doc={selectedDoc}
          docId={selectedDoc?.id}
          docs={docs}
          onClose={() => setSelectedDoc(null)}
        />
      </div>
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}
