// DocTypeEditor — inline edit the document's classifier type.
// Extracted from views/DocumentChatPanel.jsx (refactoring Phase 2a).

import { useEffect, useState } from "react";
import { Pill } from "../Shell.jsx";
import { useAuth } from "../../auth/AuthContext.jsx";
import { fetchDocTypes, setDocumentType } from "../../api";

export default function DocTypeEditor({ doc, onDocUpdated }) {
  const { hasRole } = useAuth();
  const canEdit = hasRole("admin", "reviewer");
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(doc.docType || "");
  const [saving, setSaving] = useState(false);
  const [types, setTypes] = useState([]);
  useEffect(() => {
    if (editing && types.length === 0) {
      fetchDocTypes().then(r => setTypes([...(r.docTypes || [])].sort(
        (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" })))).catch(() => {});
    }
  }, [editing]);  // types.length is read but not needed as a dep — the guard prevents re-fetch

  const save = async () => {
    const t = val.trim();
    if (!t || saving) return;
    setSaving(true);
    try {
      const fresh = await setDocumentType(doc.id, t);
      onDocUpdated?.(fresh);
      setEditing(false);
    } catch (_e) { /* surfaced via no state change */ }
    finally { setSaving(false); }
  };

  if (!editing) {
    return (
      <span className="row gap-1" style={{ alignItems: "center" }}>
        {doc.docType ? (
          <span title={`classifier confidence ${((doc.docTypeConfidence || 0) * 100).toFixed(0)}%`}>
            <Pill color="violet">{doc.docType}</Pill>
          </span>
        ) : (
          <Pill color="neutral">unclassified</Pill>
        )}
        {canEdit && (
          <button onClick={() => { setVal(doc.docType || ""); setEditing(true); }}
            title="Correct the document type" aria-label="Edit type"
            className="ink3 hover-bg" style={{ border: "none", background: "none", cursor: "pointer", fontSize: 12, padding: "2px 4px", borderRadius: 3 }}>
            ✎
          </button>
        )}
      </span>
    );
  }
  return (
    <span className="row gap-1" style={{ alignItems: "center" }}>
      <input list="docaiq-doc-types" value={val} onChange={e => setVal(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
        autoFocus placeholder="document type…" className="bg1 border"
        style={{ padding: "3px 8px", borderRadius: 4, fontSize: 12, width: 210, color: "var(--ink)", outline: "none" }}/>
      <datalist id="docaiq-doc-types">{types.map(t => <option key={t} value={t}/>)}</datalist>
      <button onClick={save} disabled={saving || !val.trim()} className="btn-gold"
        style={{ padding: "3px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>{saving ? "…" : "Save"}</button>
      <button onClick={() => setEditing(false)} className="ink3"
        style={{ border: "none", background: "none", cursor: "pointer", fontSize: 11 }}>Cancel</button>
    </span>
  );
}
