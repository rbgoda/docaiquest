// M46 · Documents System · Connectors view (Google Drive).
//
// Connect a Drive account → browse folders → sync a folder into your private
// workspace. Retention default is download→process→purge→keep a re-pull link
// (the synced docs stay fully searchable; the original blob is purged and
// re-fetched on demand). "Keep original copy" is an opt-in toggle.
//
// Documents-product only; the backend 404s these endpoints elsewhere.
import React, { useEffect, useState } from "react";
import Icon from "../components/Icon.jsx";
import {
  fetchDriveStatus, connectDrive, disconnectDrive,
  fetchDriveFolders, syncDriveFolder,
  fetchDriveInbox, syncDriveInbox, backupUploadsToDrive,
  setBackupEncryption,
} from "../api/documents";

export default function DocumentsConnectors({ onSynced }) {
  const [status, setStatus] = useState(null);     // {backend, connected, accountEmail, requiresOauth}
  const [folders, setFolders] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [keepOriginal, setKeepOriginal] = useState(true);
  const [syncing, setSyncing] = useState(null);   // folderId currently syncing
  const [result, setResult] = useState(null);     // last sync summary
  const [inbox, setInbox] = useState(null);       // {folderName, folderId, count, files}
  const [inboxBusy, setInboxBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupResult, setBackupResult] = useState(null);
  const [encPw, setEncPw] = useState("");
  const [encBusy, setEncBusy] = useState(false);
  const [encErr, setEncErr] = useState(null);

  const onToggleBackupEncryption = async (enable) => {
    setEncErr(null);
    if (enable && encPw.length < 8) { setEncErr("Choose a password of at least 8 characters."); return; }
    setEncBusy(true);
    try {
      await setBackupEncryption(enable, enable ? encPw : null);
      setStatus(s => ({ ...s, backupEncryption: enable }));
      setEncPw("");
    } catch (e) { setEncErr(e.message || "Couldn't update encryption"); }
    finally { setEncBusy(false); }
  };

  const loadStatus = async () => {
    try { setStatus(await fetchDriveStatus()); }
    catch (e) { setError(e.message || "Failed to load connector"); }
  };
  useEffect(() => { loadStatus(); }, []);

  const refreshInbox = async () => {
    try { setInbox(await fetchDriveInbox()); }
    catch (e) { setError(e.message || "Couldn't open the docaiq_docs folder"); }
  };

  // Once connected, open the docaiq_docs inbox (and lazily the full folder list).
  useEffect(() => {
    if (status?.connected) {
      refreshInbox();
      fetchDriveFolders().then(r => setFolders(r.folders)).catch(() => {});
    } else {
      setInbox(null); setFolders(null);
    }
  }, [status?.connected]);

  const onBackupUploads = async () => {
    setBackupBusy(true); setBackupResult(null); setError(null);
    try {
      // Stay on this page and show the result here (don't navigate to Documents
      // like sync does — backup is a settings action, not a content import).
      setBackupResult(await backupUploadsToDrive());
    } catch (e) { setError(e.message || "Backup failed"); }
    finally { setBackupBusy(false); }
  };

  const onSyncInbox = async () => {
    setInboxBusy(true); setError(null); setResult(null);
    try {
      const r = await syncDriveInbox({ keepOriginal });
      setResult(r);
      onSynced?.();
      await refreshInbox();
    } catch (e) { setError(e.message || "Sync failed"); }
    finally { setInboxBusy(false); }
  };

  const onConnect = async () => {
    setBusy(true); setError(null);
    try {
      const r = await connectDrive();
      if (r.authUrl) { window.location.href = r.authUrl; return; }  // google → consent
      await loadStatus();                                            // stub → instant
    } catch (e) { setError(e.message || "Connect failed"); }
    finally { setBusy(false); }
  };

  const onDisconnect = async () => {
    setBusy(true); setError(null);
    try { await disconnectDrive(); setResult(null); await loadStatus(); }
    catch (e) { setError(e.message || "Disconnect failed"); }
    finally { setBusy(false); }
  };

  const onSync = async (folderId) => {
    setSyncing(folderId); setError(null); setResult(null);
    try {
      const r = await syncDriveFolder({ folderId, keepOriginal });
      setResult(r);
      onSynced?.();
    } catch (e) { setError(e.message || "Sync failed"); }
    finally { setSyncing(null); }
  };

  return (
    <div style={{ maxWidth: 760 }}>
      <h2 className="serif" style={{ fontSize: 22, marginBottom: 4 }}>Connectors</h2>
      <p className="ink2" style={{ fontSize: 13, marginBottom: 20 }}>
        Bring documents in from an external source. Files sync into your private
        workspace — only you can see them.
      </p>

      {error && (
        <div className="border rounded-md" style={{ padding: "8px 12px", fontSize: 12, marginBottom: 16, background: "rgba(216,98,94,0.08)", borderColor: "rgba(216,98,94,.30)", color: "#D8625E" }}>
          <span className="mono">{error}</span>
        </div>
      )}

      {/* Drive connector card */}
      <div className="bg1 border rounded-xl" style={{ padding: 22 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div className="row gap-3" style={{ alignItems: "center" }}>
            <div className="bg2 border" style={{ width: 38, height: 38, borderRadius: 8, display: "grid", placeItems: "center" }}>
              <Icon name="link" size={18} />
            </div>
            <div>
              <div className="font-semibold" style={{ fontSize: 15 }}>Google Drive</div>
              <div className="ink3" style={{ fontSize: 12 }}>
                {status?.connected
                  ? <>Connected{status.accountEmail ? ` · ${status.accountEmail}` : ""}</>
                  : "Not connected"}
                {status?.backend === "stub" && (
                  <span className="mono" style={{ marginLeft: 8, fontSize: 10, padding: "1px 5px", borderRadius: 3, background: "var(--bg3)", color: "var(--ink3)" }}>
                    demo backend
                  </span>
                )}
              </div>
            </div>
          </div>
          {status == null ? (
            <span className="ink3" style={{ fontSize: 12 }}>Loading…</span>
          ) : status.connected ? (
            <button className="border bg2" onClick={onDisconnect} disabled={busy}
                    style={{ padding: "7px 14px", borderRadius: 7, fontSize: 13 }}>
              Disconnect
            </button>
          ) : (
            <button className="btn-gold" onClick={onConnect} disabled={busy}
                    style={{ padding: "8px 16px", borderRadius: 7, fontSize: 13, opacity: busy ? 0.6 : 1 }}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          )}
        </div>

        {/* docaiq_docs inbox (primary) + retention + advanced browser, when connected */}
        {status?.connected && (
          <div style={{ marginTop: 20, borderTop: "1px solid var(--line)", paddingTop: 18 }}>
            <label className="row gap-2" style={{ alignItems: "center", fontSize: 13, cursor: "pointer", marginBottom: 16 }}>
              <input type="checkbox" checked={keepOriginal} onChange={e => setKeepOriginal(e.target.checked)} />
              <span>
                Keep a copy of the original file
                <span className="ink3" style={{ marginLeft: 6, fontSize: 12 }}>
                  — kept by default so you can view it any time. The file always stays in your Drive too.
                </span>
              </span>
            </label>

            {/* The dedicated docaiq_docs inbox */}
            <div className="bg2 border rounded-md" style={{ padding: 16 }}>
              <div className="row between" style={{ alignItems: "center" }}>
                <div className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
                  <Icon name="folder" size={16} />
                  <div>
                    <div className="font-semibold mono" style={{ fontSize: 13 }}>{inbox?.folderName || "docaiq_docs"}</div>
                    <div className="ink3" style={{ fontSize: 11 }}>
                      {inbox == null ? "Opening…"
                        : inbox.count === 0 ? "No files waiting"
                        : `${inbox.count} file${inbox.count === 1 ? "" : "s"} ready to process`}
                    </div>
                  </div>
                </div>
                <div className="row gap-2" style={{ flexShrink: 0 }}>
                  <button onClick={refreshInbox} disabled={inboxBusy} className="border bg1"
                          style={{ padding: "6px 12px", borderRadius: 6, fontSize: 12 }}>Refresh</button>
                  <button onClick={onSyncInbox} disabled={inboxBusy || (inbox?.count || 0) === 0} className="btn-gold"
                          style={{ padding: "6px 14px", borderRadius: 6, fontSize: 12, opacity: inboxBusy || (inbox?.count || 0) === 0 ? 0.5 : 1 }}>
                    {inboxBusy ? "Syncing…" : "Sync now"}
                  </button>
                </div>
              </div>
              <div className="ink3" style={{ fontSize: 11, marginTop: 10, lineHeight: 1.5 }}>
                Drop files into the <b className="mono" style={{ color: "var(--ink2)" }}>docaiq_docs</b> folder in your Google Drive, then Sync. DocAIQuest only ever looks at this one folder.
              </div>
              {inbox?.files?.length > 0 && (
                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
                  {inbox.files.slice(0, 8).map(f => (
                    <div key={f.id} className="row gap-2" style={{ alignItems: "center", fontSize: 12 }}>
                      <Icon name="file" size={12} /><span className="truncate">{f.name}</span>
                    </div>
                  ))}
                  {inbox.files.length > 8 && <span className="ink3" style={{ fontSize: 11 }}>+{inbox.files.length - 8} more</span>}
                </div>
              )}
            </div>

            {/* Optional: password-encrypt the Drive backup (user-owned key) */}
            <div className="bg2 border rounded-md" style={{ padding: 14, marginTop: 14 }}>
              <div className="row between" style={{ alignItems: "flex-start", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div className="font-semibold" style={{ fontSize: 13 }}>🔒 Encrypt my backup with a password</div>
                  <div className="ink3" style={{ fontSize: 11, marginTop: 2, lineHeight: 1.5 }}>
                    Optional. Your backup already lives only in your own private Drive — turn this on to also lock it
                    with a password only you know.{" "}
                    <b style={{ color: "var(--amber)" }}>If you forget the password, no one (including us) can recover the backup</b>
                    {" "}— but your original files in Drive can always be re-imported.
                  </div>
                </div>
                <span className="mono" style={{ fontSize: 11, flexShrink: 0,
                  color: status?.backupEncryption ? "var(--emerald)" : "var(--ink3)" }}>
                  {status?.backupEncryption ? "On" : "Off"}
                </span>
              </div>
              {status?.backupEncryption ? (
                <button onClick={() => onToggleBackupEncryption(false)} disabled={encBusy}
                  className="border bg1" style={{ marginTop: 10, padding: "6px 12px", borderRadius: 6, fontSize: 12 }}>
                  {encBusy ? "Updating…" : "Turn off encryption"}
                </button>
              ) : (
                <div className="row gap-2" style={{ marginTop: 10, alignItems: "center" }}>
                  <input type="password" value={encPw} onChange={e => setEncPw(e.target.value)}
                    placeholder="Set a backup password (8+ chars)" autoComplete="new-password"
                    style={{ flex: 1, padding: "7px 10px", borderRadius: 6, fontSize: 12,
                             background: "var(--bg1)", border: "1px solid var(--line)", color: "var(--ink)" }} />
                  <button onClick={() => onToggleBackupEncryption(true)} disabled={encBusy}
                    className="btn-gold" style={{ padding: "7px 14px", borderRadius: 6, fontSize: 12 }}>
                    {encBusy ? "Enabling…" : "Enable"}
                  </button>
                </div>
              )}
              {encErr && <div style={{ color: "var(--rose)", fontSize: 11, marginTop: 6 }}>{encErr}</div>}
            </div>

            {/* Free up server space — back up direct uploads to Drive */}
            <div className="bg2 border rounded-md" style={{ padding: 14, marginTop: 14 }}>
              <div className="row between" style={{ alignItems: "center" }}>
                <div style={{ minWidth: 0 }}>
                  <div className="font-semibold" style={{ fontSize: 13 }}>Free up space</div>
                  <div className="ink3" style={{ fontSize: 11, marginTop: 2 }}>
                    Push your uploaded files into <span className="mono">docaiq_docs</span> and remove <b>all</b> server copies (uploads + already-synced). Everything stays searchable and re-opens from your Drive on demand.
                  </div>
                </div>
                <button onClick={onBackupUploads} disabled={backupBusy} className="border bg1"
                        style={{ padding: "6px 14px", borderRadius: 6, fontSize: 12, flexShrink: 0, marginLeft: 12 }}>
                  {backupBusy ? "Freeing…" : "Free server space"}
                </button>
              </div>
              {backupResult && (
                <div className="ink2" style={{ fontSize: 12, marginTop: 8, color: "#3FA47A" }}>
                  Freed <b>{backupResult.backedUp}</b> server cop{backupResult.backedUp === 1 ? "y" : "ies"} — kept in your Drive.
                  {backupResult.remaining > 0 && <span className="ink3" style={{ color: "var(--ink2)" }}> {backupResult.remaining} left.</span>}
                  {backupResult.errors?.length > 0 && <span style={{ color: "#D8625E" }}> {backupResult.errors.length} error(s).</span>}
                </div>
              )}
            </div>

            {result && (
              <div className="border rounded-md" style={{ marginTop: 14, padding: "10px 14px", fontSize: 12, background: "rgba(63,164,122,0.08)", borderColor: "rgba(63,164,122,.30)", color: "#3FA47A" }}>
                Synced <b>{result.created.length}</b> file{result.created.length === 1 ? "" : "s"}
                {result.skipped.length > 0 && <> · {result.skipped.length} already present</>}
                {result.errors.length > 0 && <> · <span style={{ color: "#D8625E" }}>{result.errors.length} error(s)</span></>}
                {" · "}{result.retained ? "originals kept" : "originals purged (re-pull on demand)"}.
                <span className="ink3" style={{ color: "var(--ink2)" }}> Processing in the background — check the Documents tab.</span>
              </div>
            )}

            {/* Advanced: sync any other folder */}
            <button onClick={() => setShowAdvanced(s => !s)} className="ink3"
                    style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, textDecoration: "underline", marginTop: 14 }}>
              {showAdvanced ? "Hide other folders" : "Sync another folder (advanced)"}
            </button>
            {showAdvanced && (
              <div style={{ marginTop: 12 }}>
                {folders == null ? (
                  <div className="ink3" style={{ fontSize: 13 }}>Loading folders…</div>
                ) : folders.length === 0 ? (
                  <div className="ink3" style={{ fontSize: 13 }}>No folders found in this Drive.</div>
                ) : (
                  <div className="flex col gap-2">
                    {folders.map(f => (
                      <div key={f.id} className="row border bg2" style={{ justifyContent: "space-between", alignItems: "center", padding: "10px 14px", borderRadius: 8 }}>
                        <div className="row gap-2" style={{ alignItems: "center" }}>
                          <Icon name="folder" size={15} />
                          <span style={{ fontSize: 13 }}>{f.name}</span>
                        </div>
                        <button className="border bg1" onClick={() => onSync(f.id)} disabled={syncing != null}
                                style={{ padding: "5px 12px", borderRadius: 6, fontSize: 12, opacity: syncing && syncing !== f.id ? 0.5 : 1 }}>
                          {syncing === f.id ? "Syncing…" : "Sync"}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
