import React, { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon.jsx";
import RichMessage from "../components/RichMessage.jsx";
import ChatFeedback from "../components/ChatFeedback.jsx";
import { ArtifactBar, StepTrace, Citations } from "../components/AgentExtras.jsx";
import SmartVisuals from "../components/doc-chat/SmartVisuals.jsx";
import { LoadingState, ErrorState } from "../components/Shell.jsx";
import { fetchWorkspaceChat, postWorkspaceChatMessage, clearWorkspaceChat, listWorkspaceThreads } from "../api";
import { useApiResource } from "../api/useApi.js";

// M44.P12 · "Ask across all documents" — cross-document chat scoped to the
// active vendor's document set (the Documents tab). Mirrors the per-document
// ChatTab, but every citation is attributed to its source document since an
// answer can draw on several docs at once.
//
// `vendorPk` scopes the thread (one shared thread per vendor); `docCount` is
// rendered so the reviewer knows how many documents the answer ranged over.
export default function WorkspaceChat({ vendorPk = null, onOpenDocument, docs = [], fill = false }) {
  // Which saved conversation is open (null = the base/original thread). Persisted
  // per vendor so it sticks across reloads.
  const convKey = `docaiq.wschat.conv.${vendorPk ?? "u"}`;
  const [convId, setConvId] = useState(() => {
    try { return localStorage.getItem(convKey) || null; } catch { return null; }
  });
  const setConvPersist = (id) => {
    setConvId(id);
    try { id ? localStorage.setItem(convKey, id) : localStorage.removeItem(convKey); } catch { /* ignore */ }
  };
  const { data: thread, loading, error, setData } = useApiResource(
    () => fetchWorkspaceChat(vendorPk, convId), [vendorPk, convId]
  );
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [threads, setThreads] = useState([]);
  const [histOpen, setHistOpen] = useState(false);
  const scrollRef = useRef(null);

  const refreshThreads = async () => {
    try { setThreads(await listWorkspaceThreads(vendorPk)); } catch { /* ignore */ }
  };
  useEffect(() => { refreshThreads(); }, [vendorPk]); // eslint-disable-line react-hooks/exhaustive-deps

  // Start a NEW conversation. The current one is KEPT (saved server-side) and stays
  // in the history picker — nothing is deleted. A fresh conv id means the next
  // question carries no prior context (never treated as a follow-up).
  const newChat = () => {
    if (sending) return;
    setSendError(null);
    setHistOpen(false);
    setConvPersist(String(Date.now()));
    setData(prev => ({ ...(prev || {}), messages: [] }));
    refreshThreads();
  };

  const openConv = (id) => { setHistOpen(false); setConvPersist(id || null); };

  const deleteConv = async (id, e) => {
    e?.stopPropagation();
    try {
      await clearWorkspaceChat(vendorPk, id || null);
      if ((id || null) === (convId || null)) setConvPersist(null);
      refreshThreads();
    } catch (err) { setSendError(err.message); }
  };

  // Subset mode · ready docs the reviewer can narrow the question to. Empty
  // set = ask across ALL (the default). Picking some restricts retrieval.
  const readyDocs = (docs || []).filter(d => d.ingestionStatus === "ready");
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [scopeOpen, setScopeOpen] = useState(false);
  const toggleId = (id) => setSelectedIds(prev => {
    const n = new Set(prev);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [thread?.messages?.length, sending]);

  const submit = async (e) => {
    e?.preventDefault();
    if (!draft.trim() || sending) return;
    setSending(true);
    setSendError(null);
    const text = draft.trim();
    setDraft("");
    try {
      // Optimistic user bubble before the AI replies.
      setData(prev => prev ? {
        ...prev,
        messages: [
          ...prev.messages,
          { id: -Date.now(), role: "user", text, citations: [], createdAt: new Date().toISOString() },
        ],
      } : prev);
      await postWorkspaceChatMessage(vendorPk, text, Array.from(selectedIds), convId);
      // Refetch so the optimistic user-msg gets its real PK + the AI reply.
      const fresh = await fetchWorkspaceChat(vendorPk, convId);
      setData(fresh);
      refreshThreads();   // update the history titles / ordering
    } catch (err) {
      setSendError(err.message);
    } finally {
      setSending(false);
    }
  };

  if (loading) return <LoadingState label="Loading documents chat…"/>;
  if (error)   return <ErrorState message={error}/>;

  const docCount = thread?.docCount ?? 0;
  const msgs = thread?.messages || [];
  // Saved conversations other than a brand-new empty one — what the picker offers.
  const savedThreads = threads.filter(t => t.count > 0);

  return (
    <div className="flex col bg1 border rounded-xl"
         style={{ minHeight: 0, height: fill ? "100%" : 460, overflow: "hidden" }}>
      <div className="row between p-3 border-b" style={{ alignItems: "center", flex: "0 0 auto" }}>
        <div className="row gap-2" style={{ alignItems: "center" }}>
          <Icon name="search" size={13}/>
          <span className="font-semibold text-sm">Ask across all documents</span>
        </div>
        <div className="row gap-2" style={{ alignItems: "center", position: "relative" }}>
          {savedThreads.length > 0 && (
            <div style={{ position: "relative" }}>
              <button onClick={() => setHistOpen(o => !o)}
                className={histOpen ? "btn-gold row gap-1" : "border bg2 row gap-1"}
                style={{ fontSize: 10, padding: "3px 10px", borderRadius: 10, cursor: "pointer", alignItems: "center" }}
                title="Reopen a past conversation">
                <Icon name="clock" size={10}/>History ({savedThreads.length}) ▾
              </button>
              {histOpen && (
                <div className="bg1 border rounded-md" style={{ position: "absolute", right: 0, top: "125%", zIndex: 30, width: 280, maxHeight: 260, overflow: "auto", boxShadow: "0 8px 24px rgba(0,0,0,.4)", padding: 4 }}>
                  {savedThreads.map(t => {
                    const active = (t.conv || null) === (convId || null);
                    return (
                      <div key={t.conv || "base"} onClick={() => openConv(t.conv)}
                        className="hover-bg row between"
                        style={{ alignItems: "center", gap: 6, padding: "6px 8px", borderRadius: 5, cursor: "pointer",
                                 background: active ? "rgba(224,162,59,0.14)" : undefined }}>
                        <div style={{ minWidth: 0 }}>
                          <div className="truncate" style={{ fontSize: 12, color: "var(--ink)" }}>{t.title}</div>
                          <div className="ink4" style={{ fontSize: 10 }}>{t.count} message{t.count === 1 ? "" : "s"}{t.updatedAt ? ` · ${new Date(t.updatedAt).toLocaleDateString()}` : ""}</div>
                        </div>
                        <button onClick={(e) => deleteConv(t.conv, e)} title="Delete this conversation"
                          className="ink3 hover-bg" style={{ flex: "0 0 auto", border: "none", background: "none", cursor: "pointer", fontSize: 13, lineHeight: 1 }}>×</button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
          {(msgs.length > 0 || convId) && (
            <button onClick={newChat} disabled={sending}
              className="border bg2 row gap-1"
              style={{ fontSize: 10, padding: "3px 10px", borderRadius: 10, cursor: sending ? "default" : "pointer", opacity: sending ? 0.6 : 1, alignItems: "center" }}
              title="Start a new conversation — the current one is saved to History; your next question carries no prior context (documents are untouched)">
              <Icon name="plus" size={10}/>New chat
            </button>
          )}
          {readyDocs.length > 1 && (
            <button onClick={() => setScopeOpen(o => !o)}
              className={selectedIds.size ? "btn-gold" : "border bg2"}
              style={{ fontSize: 10, padding: "3px 10px", borderRadius: 10, cursor: "pointer" }}
              title="Choose which documents to ask across">
              Scope: {selectedIds.size ? `${selectedIds.size} of ${readyDocs.length}` : `all ${readyDocs.length}`} ▾
            </button>
          )}
          <span className="ink3 text-xs mono">
            {(selectedIds.size || docCount)} document{(selectedIds.size || docCount) === 1 ? "" : "s"} in scope
          </span>
        </div>
      </div>

      {scopeOpen && readyDocs.length > 1 && (
        <div className="bg2 border-b p-2" style={{ flex: "0 0 auto", maxHeight: 150, overflow: "auto" }}>
          <div className="row between mb-1" style={{ alignItems: "center" }}>
            <span className="ink3 text-xs">{selectedIds.size ? "Asking across the ticked documents" : "Asking across all documents"}</span>
            {selectedIds.size > 0 && (
              <button onClick={() => setSelectedIds(new Set())} className="ink2"
                style={{ background: "none", border: "none", fontSize: 10, cursor: "pointer", textDecoration: "underline" }}>
                Clear (use all)
              </button>
            )}
          </div>
          {readyDocs.map(d => (
            <label key={d.id} className="row gap-2" style={{ alignItems: "center", padding: "2px 0", fontSize: 12, cursor: "pointer" }}>
              <input type="checkbox" checked={selectedIds.has(d.id)} onChange={() => toggleId(d.id)}/>
              <span className="truncate">{d.name}</span>
            </label>
          ))}
        </div>
      )}

      <div ref={scrollRef} style={{ flex: "1 1 0", minHeight: 0, overflow: "auto", padding: 16 }}>
        {msgs.length === 0 && (
          <div className="ink3 text-sm" style={{ fontStyle: "italic" }}>
            {docCount === 0
              ? "No documents are ready yet. Upload and let processing finish, then ask a question."
              : "Ask a question spanning all the documents — \"which policies expire this year?\", \"compare the two insurance certs\", \"what's the total across the invoices?\""}
          </div>
        )}
        {msgs.map(m => <WorkspaceMessageRow key={m.id} m={m} onOpenDocument={onOpenDocument}/>)}
        {sending && <ThinkingBubble/>}
      </div>

      {sendError && (
        <div className="text-sm p-2 mx-4 mb-2" style={{ background: "rgba(216,98,94,0.08)", color: "var(--rose)", borderRadius: 4 }}>
          {sendError}
        </div>
      )}

      <form onSubmit={submit} className="p-3 row gap-2" style={{ borderTop: "1px solid var(--line)", flex: "0 0 auto" }}>
        <input
          type="text"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={docCount === 0 ? "No documents ready yet…" : "Ask across all documents…"}
          disabled={sending || docCount === 0}
          className="bg1 border grow"
          style={{ padding: "8px 12px", borderRadius: 6, fontSize: 13, color: "var(--ink)", outline: "none" }}
        />
        <button type="submit" disabled={sending || !draft.trim()} className="btn-gold"
                style={{ padding: "8px 16px", borderRadius: 6, fontSize: 12 }}>
          {sending ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}

function WorkspaceMessageRow({ m, onOpenDocument }) {
  const isUser = m.role === "user";
  const cites = m.citations || [];
  const artifacts = m.artifacts || [];
  const trace = m.trace || [];
  return (
    <div className="mb-3" style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div style={{ maxWidth: "85%" }}>
        <div
          className={isUser ? "btn-gold" : "bg2 border"}
          style={{
            padding: "8px 12px", borderRadius: 10, fontSize: 13,
            color: isUser ? undefined : "var(--ink)",
          }}
        >
          {isUser ? <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span> : <RichMessage content={m.text} />}
        </div>
        {!isUser && <SmartVisuals content={m.text} />}
        {!isUser && <Citations items={cites} onOpen={onOpenDocument} />}
        {!isUser && <ArtifactBar artifacts={artifacts} />}
        {!isUser && <StepTrace steps={trace} />}
        {!isUser && <ChatFeedback messageId={m.id} />}
      </div>
    </div>
  );
}

// An animated placeholder while the agent runs (single POST, so we can't stream
// real steps yet — this gives the "working" feel until the reply lands).
function ThinkingBubble() {
  const [dots, setDots] = useState(1);
  useEffect(() => {
    const t = setInterval(() => setDots(d => (d % 3) + 1), 420);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="mb-3" style={{ display: "flex", justifyContent: "flex-start" }}>
      <div className="bg2 border ink3"
        style={{ padding: "8px 12px", borderRadius: 10, fontSize: 12, fontStyle: "italic" }}>
        Working through your documents{".".repeat(dots)}
      </div>
    </div>
  );
}
