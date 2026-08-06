// M46 · Documents · Groups (management). Create a group, add/remove members by
// gmail, delete a group. The group's DOCUMENTS live in the Documents tab under
// that group's scope tab (members co-manage them there).
import React, { useEffect, useState } from "react";
import Icon from "../components/Icon.jsx";
import { fetchGroups, createGroup, addGroupMember, removeGroupMember, renameGroup, deleteGroup, fetchGroupActivity, fetchGroupChat, postGroupChat } from "../api/documents";
import { useConfirm } from "../components/ConfirmDialog.jsx";

// A1 · compact "ask across this group's documents" chat panel.
function GroupChat({ groupId }) {
  const [msgs, setMsgs] = useState(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  useEffect(() => { fetchGroupChat(groupId).then(r => setMsgs(r.messages || [])).catch(() => setMsgs([])); }, [groupId]);
  const send = async () => {
    const t = input.trim();
    if (!t || sending) return;
    setInput(""); setSending(true);
    setMsgs(m => [...(m || []), { id: "tmp", role: "user", text: t }]);
    try { const ai = await postGroupChat(groupId, t); setMsgs(m => [...(m || []), ai]); }
    catch (e) { setMsgs(m => [...(m || []), { id: "err", role: "ai", text: "Error: " + (e.message || "") }]); }
    finally { setSending(false); }
  };
  return (
    <div style={{ marginTop: 8 }}>
      <div className="bg2 border rounded-md" style={{ maxHeight: 220, overflowY: "auto", padding: 8, marginBottom: 6 }}>
        {msgs === null ? <div className="ink4" style={{ fontSize: 11 }}>Loading…</div>
          : msgs.length === 0 ? <div className="ink4" style={{ fontSize: 11, fontStyle: "italic" }}>Ask a question across this group's documents.</div>
          : msgs.map((m, i) => (
            <div key={m.id || i} style={{ marginBottom: 6, fontSize: 12 }}>
              <span className="mono ink4" style={{ fontSize: 10 }}>{m.role === "user" ? "you" : "ai"}</span>
              <div className={m.role === "user" ? "ink" : "ink2"} style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
            </div>
          ))}
      </div>
      <div className="row gap-2">
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter") send(); }}
          placeholder="Ask across this group's docs…" className="bg1 border grow"
          style={{ padding: "7px 10px", borderRadius: 6, fontSize: 12, color: "var(--ink)", outline: "none" }} />
        <button onClick={send} disabled={sending || !input.trim()} className="btn-gold" style={{ padding: "7px 14px", borderRadius: 6, fontSize: 12 }}>
          {sending ? "…" : "Ask"}
        </button>
      </div>
    </div>
  );
}

// §1 · render a group activity row as readable text.
const ACTION_LABEL = {
  created: "created the group", renamed: "renamed", added_member: "added",
  invited_member: "invited", removed_member: "removed", shared_doc: "shared",
  unshared_doc: "unshared",
};
function eventLine(e) {
  const who = (e.actor || "someone").split("@")[0];
  const verb = ACTION_LABEL[e.action] || e.action;
  return `${who} ${verb}${e.detail ? " " + e.detail : ""}`;
}

export default function DocumentsGroups({ onOpenDocuments }) {
  const [groups, setGroups] = useState(null);
  const [error, setError] = useState(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState(null);          // group id being mutated
  const [memberDraft, setMemberDraft] = useState({});  // {groupId: email}
  const [editId, setEditId] = useState(null);          // group id being renamed
  const [editName, setEditName] = useState("");
  const [activity, setActivity] = useState({});        // {groupId: events[] | "loading"}
  const [chatOpen, setChatOpen] = useState(null);      // group id whose chat is open
  const confirmDialog = useConfirm();

  const toggleActivity = async (gid) => {
    if (activity[gid] !== undefined) { setActivity(a => { const n = { ...a }; delete n[gid]; return n; }); return; }
    setActivity(a => ({ ...a, [gid]: "loading" }));
    try { const r = await fetchGroupActivity(gid); setActivity(a => ({ ...a, [gid]: r.events || [] })); }
    catch { setActivity(a => ({ ...a, [gid]: [] })); }
  };

  const onRename = async (g) => {
    const name = editName.trim();
    if (!name || name === g.name) { setEditId(null); return; }
    setBusyId(g.id); setError(null);
    try { await renameGroup(g.id, name); setEditId(null); await load(); }
    catch (e) { setError(e.message || "Couldn't rename group"); }
    finally { setBusyId(null); }
  };

  const load = async () => {
    try { setGroups((await fetchGroups()).groups); }
    catch (e) { setError(e.message || "Couldn't load groups"); }
  };
  useEffect(() => { load(); }, []);

  const onCreate = async (e) => {
    e?.preventDefault();
    if (!newName.trim() || creating) return;
    setCreating(true); setError(null);
    try { await createGroup(newName.trim()); setNewName(""); await load(); }
    catch (e) { setError(e.message || "Create failed"); }
    finally { setCreating(false); }
  };

  const onAddMember = async (gid) => {
    const email = (memberDraft[gid] || "").trim();
    if (!email) return;
    setBusyId(gid); setError(null);
    try {
      await addGroupMember(gid, email);
      setMemberDraft((d) => ({ ...d, [gid]: "" }));
      await load();
    } catch (e) { setError(e.message || "Couldn't add member"); }
    finally { setBusyId(null); }
  };

  const onRemove = async (gid, email) => {
    setBusyId(gid); setError(null);
    try { await removeGroupMember(gid, email); await load(); }
    catch (e) { setError(e.message || "Couldn't remove member"); }
    finally { setBusyId(null); }
  };

  const onDelete = async (g) => {
    const ok = await confirmDialog({
      title: `Delete group "${g.name}"?`,
      body: `Its ${g.docCount} document${g.docCount === 1 ? "" : "s"} will move back to each owner's Personal documents. Members lose access to them. This can't be undone.`,
      confirmLabel: "Delete group",
      destructive: true,
    });
    if (!ok) return;
    setBusyId(g.id); setError(null);
    try { await deleteGroup(g.id); await load(); }
    catch (e) { setError(e.message || "Couldn't delete group"); }
    finally { setBusyId(null); }
  };

  return (
    <div style={{ maxWidth: 760 }}>
      <h2 className="serif" style={{ fontSize: 22, marginBottom: 4 }}>Groups</h2>
      <p className="ink2" style={{ fontSize: 13, marginBottom: 20 }}>
        Create a group and add people by their Google email. A group's documents live
        in the <button onClick={() => onOpenDocuments?.()} style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--gold2)", fontSize: 13 }}>Documents</button> tab
        under that group's tab — every member can co-manage them. Shared docs are stored in a shared Google Drive folder.
      </p>

      {error && (
        <div className="border rounded-md" style={{ padding: "8px 12px", fontSize: 12, marginBottom: 16, background: "rgba(216,98,94,0.08)", borderColor: "rgba(216,98,94,.30)", color: "#D8625E" }}>
          <span className="mono">{error}</span>
        </div>
      )}

      {/* Create */}
      <form onSubmit={onCreate} className="row gap-2 bg1 border rounded-xl" style={{ padding: 14, alignItems: "center", marginBottom: 18 }}>
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New group name (e.g. Family, Acme Audit)"
          className="bg2 border grow" style={{ padding: "9px 12px", borderRadius: 6, fontSize: 13, color: "var(--ink)", outline: "none" }} />
        <button type="submit" disabled={creating || !newName.trim()} className="btn-gold"
          style={{ padding: "9px 16px", borderRadius: 6, fontSize: 13, opacity: creating || !newName.trim() ? 0.6 : 1 }}>
          {creating ? "Creating…" : "Create group"}
        </button>
      </form>

      {/* List */}
      {groups == null ? (
        <div className="ink3" style={{ fontSize: 13 }}>Loading…</div>
      ) : groups.length === 0 ? (
        <div className="ink3" style={{ fontSize: 13, fontStyle: "italic" }}>No groups yet. Create one above to start sharing documents.</div>
      ) : (
        <div className="flex col gap-3">
          {groups.map((g) => (
            <div key={g.id} className="bg1 border rounded-xl" style={{ padding: 18 }}>
              <div className="row between" style={{ alignItems: "center", marginBottom: 12 }}>
                <div className="row gap-2" style={{ alignItems: "center" }}>
                  <Icon name="users" size={16} />
                  {editId === g.id ? (
                    <input autoFocus value={editName} onChange={(e) => setEditName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") onRename(g); if (e.key === "Escape") setEditId(null); }}
                      onBlur={() => onRename(g)} disabled={busyId === g.id}
                      className="bg2 border" style={{ padding: "3px 8px", borderRadius: 5, fontSize: 15, color: "var(--ink)", outline: "none", minWidth: 180 }} />
                  ) : (
                    <span className="font-semibold" style={{ fontSize: 15 }}>{g.name}</span>
                  )}
                  {g.isOwner && editId !== g.id && (
                    <button onClick={() => { setEditId(g.id); setEditName(g.name); }} title="Rename group"
                      className="ink3" style={{ background: "none", border: "none", cursor: "pointer", display: "flex", padding: 2 }}>
                      <Icon name="pen" size={12} />
                    </button>
                  )}
                  {g.isOwner && <span className="mono ink4" style={{ fontSize: 10 }}>owner</span>}
                  {g.driveShared && <span title="Backed by a shared Google Drive folder" style={{ display: "flex" }}><Icon name="cloud" size={13} style={{ color: "#8B7FD6" }} /></span>}
                </div>
                <div className="row gap-3" style={{ alignItems: "center" }}>
                  <button onClick={() => onOpenDocuments?.()} className="mono" style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, color: "var(--gold2)" }}>
                    {g.docCount} shared doc{g.docCount === 1 ? "" : "s"} →
                  </button>
                  {g.isOwner && (
                    <button onClick={() => onDelete(g)} disabled={busyId === g.id}
                      title="Delete group (docs return to Personal)"
                      style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, color: "var(--rose)" }}>
                      Delete
                    </button>
                  )}
                </div>
              </div>

              {/* Members */}
              <div className="flex col gap-1" style={{ marginBottom: 12 }}>
                {g.members.map((m) => (
                  <div key={m.email} className="row between" style={{ alignItems: "center", padding: "5px 0", borderTop: "1px solid var(--line)" }}>
                    <div className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
                      <Icon name="user" size={12} />
                      <span className="truncate" style={{ fontSize: 13 }}>{m.email}</span>
                      {m.role === "owner" && <span className="mono ink4" style={{ fontSize: 9 }}>owner</span>}
                      {m.pending && <span className="mono" style={{ fontSize: 9, color: "var(--amber)" }}>pending</span>}
                    </div>
                    {m.role !== "owner" && g.isOwner && (
                      <button onClick={() => onRemove(g.id, m.email)} disabled={busyId === g.id}
                        className="ink3" style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11 }} title="Remove member">✕</button>
                    )}
                  </div>
                ))}
              </div>

              {/* Add member */}
              {g.isOwner && (
                <div className="row gap-2">
                  <input value={memberDraft[g.id] || ""} onChange={(e) => setMemberDraft((d) => ({ ...d, [g.id]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === "Enter") onAddMember(g.id); }}
                    placeholder="Add member by Google email…" type="email"
                    className="bg2 border grow" style={{ padding: "7px 11px", borderRadius: 6, fontSize: 12, color: "var(--ink)", outline: "none" }} />
                  <button onClick={() => onAddMember(g.id)} disabled={busyId === g.id || !(memberDraft[g.id] || "").trim()}
                    className="border bg2" style={{ padding: "7px 14px", borderRadius: 6, fontSize: 12 }}>
                    {busyId === g.id ? "…" : "Add"}
                  </button>
                </div>
              )}

              {/* A1 · ask across this group's documents */}
              <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--line)" }}>
                <button onClick={() => setChatOpen(chatOpen === g.id ? null : g.id)} className="row gap-1"
                  style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, alignItems: "center", color: "var(--gold2)" }}>
                  <Icon name="search" size={11} />
                  {chatOpen === g.id ? "Close chat" : "Ask across this group's docs"}
                </button>
                {chatOpen === g.id && <GroupChat groupId={g.id} />}
              </div>

              {/* §1 · Activity log */}
              <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--line)" }}>
                <button onClick={() => toggleActivity(g.id)} className="ink3 row gap-1"
                  style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, alignItems: "center" }}>
                  <Icon name="clock" size={11} />
                  {activity[g.id] !== undefined ? "Hide activity" : "Activity"}
                </button>
                {activity[g.id] === "loading" && <div className="ink4" style={{ fontSize: 11, padding: "4px 0" }}>Loading…</div>}
                {Array.isArray(activity[g.id]) && (
                  activity[g.id].length === 0
                    ? <div className="ink4" style={{ fontSize: 11, padding: "4px 0", fontStyle: "italic" }}>No activity yet.</div>
                    : <ul style={{ listStyle: "none", padding: 0, margin: "6px 0 0" }}>
                        {activity[g.id].map((e) => (
                          <li key={e.id} className="row between" style={{ fontSize: 11, padding: "2px 0", gap: 8 }}>
                            <span className="ink2 truncate">{eventLine(e)}</span>
                            <span className="ink4 mono" style={{ flexShrink: 0 }}>{e.at ? new Date(e.at).toLocaleDateString() : ""}</span>
                          </li>
                        ))}
                      </ul>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
