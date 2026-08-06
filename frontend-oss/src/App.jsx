import { useCallback, useEffect, useRef, useState } from "react";
import {
  whoami, login, signup,
  listDocuments, uploadDocument, deleteDocument, documentFileUrl,
  fetchChat, sendMessage,
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await signup(email, password, name);
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
      <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      <p className="auth-switch">
        Already have one?{" "}
        <button type="button" className="link" onClick={onSwitch}>Sign in</button>
      </p>
    </form>
  );
}

// ---- Document list panel ---------------------------------------------------

function DocList({ docs, selectedId, onSelect, onUpload, onDelete, uploading }) {
  const fileRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);

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
    <div className="doc-list">
      <div className="doc-list-header">
        <h2>Documents</h2>
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
            <span className="doc-icon">{doc.type === "application/pdf" ? "📄" : "📃"}</span>
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
  );
}

// ---- Chat panel ------------------------------------------------------------

function ChatPanel({ doc, docId, onClose }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [fileUrl, setFileUrl] = useState("");
  const bottomRef = useRef(null);

  // Load chat history when doc changes
  useEffect(() => {
    if (!docId) { setMessages([]); return; }
    setFileUrl(documentFileUrl(docId));
    fetchChat(docId)
      .then((data) => setMessages(data.messages || []))
      .catch(() => setMessages([]));
  }, [docId]);

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading || !docId) return;
    setInput("");
    setLoading(true);

    // Optimistically add user message
    const userMsg = { role: "user", text, id: Date.now().toString() };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const data = await sendMessage(docId, text);
      // Response comes back with the updated message list or the AI reply
      if (data.messages) {
        setMessages(data.messages);
      } else if (data.message) {
        setMessages((prev) => [...prev, { role: "ai", text: data.message, id: (Date.now() + 1).toString() }]);
      }
    } catch (err) {
      setMessages((prev) => [...prev, { role: "ai", text: `Error: ${err.message}`, id: (Date.now() + 2).toString(), error: true }]);
    }
    setLoading(false);
  };

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <h2>{doc ? doc.name : "Chat"}</h2>
        {onClose && <button className="chat-close" onClick={onClose}>×</button>}
      </div>

      {/* Document embed */}
      {fileUrl && (
        <div className="doc-preview">
          <iframe src={fileUrl} title="Document preview" />
        </div>
      )}

      {/* Messages */}
      <div className="chat-messages">
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
          placeholder={docId ? "Ask about this document…" : "Select a document to chat"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={!docId || loading}
        />
        <button type="submit" disabled={!docId || loading || !input.trim()}>Send</button>
      </form>
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
      setDocs(data.documents || []);
    } catch {}
  };

  const handleUpload = async (file) => {
    setUploading(true);
    try {
      await uploadDocument(file);
      await refreshDocs();
    } catch (err) {
      alert(err.message);
    }
    setUploading(false);
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
    // Clear session cookie and reset
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
          {user.email}
          <button className="link logout" onClick={handleLogout}>Sign out</button>
        </span>
      </header>
      <div className="app-body">
        <DocList
          docs={docs}
          selectedId={selectedDoc?.id}
          onSelect={handleSelect}
          onUpload={handleUpload}
          onDelete={handleDelete}
          uploading={uploading}
        />
        <ChatPanel
          doc={selectedDoc}
          docId={selectedDoc?.id}
          onClose={() => setSelectedDoc(null)}
        />
      </div>
    </div>
  );
}
