// DocumentDetailPanel — info sidebar shown when a document row is clicked.
// Extracted from views/AllDocuments.jsx (refactoring Phase 2b).

import React from "react";
import Icon from "./Icon.jsx";

export default function DocumentDetailPanel({ doc, matchedReqs, onClose }) {
  return (
    <div className="bg1 border rounded-xl" style={{ overflow: "auto", maxHeight: "calc(100vh - 160px)", position: "sticky", top: 16 }}>
      <div className="row between p-4 border-b" style={{ alignItems: "center" }}>
        <div>
          <div className="upper ink3" style={{ fontSize: 10, letterSpacing: ".1em" }}>Document</div>
          <div className="serif font-semibold text-lg mt-1 truncate" style={{ maxWidth: 360 }}>{doc.name}</div>
          <div className="mono ink3 text-xs mt-1">{doc.id}</div>
        </div>
        <button onClick={onClose} className="hover-bg ink3" style={{ padding: 6, borderRadius: 4 }}>
          <Icon name="x" size={14}/>
        </button>
      </div>

      <DetailSection title="Classification (M11.6)">
        {doc.docType ? (
          <>
            <Field label="Top type" value={`${doc.docType} (${((doc.docTypeConfidence || 0) * 100).toFixed(0)}%)`}/>
            {(doc.docTypeAlternatives || []).length > 0 && (
              <div className="mt-2">
                <div className="upper ink3" style={{ fontSize: 10 }}>Alternatives</div>
                {doc.docTypeAlternatives.map((a, i) => (
                  <div key={i} className="mono text-xs ink2" style={{ marginTop: 2 }}>
                    {a.doc_type} · {(a.confidence * 100).toFixed(0)}%
                    {a.evidence && <span className="ink3"> · "{a.evidence}"</span>}
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <span className="ink3 text-xs" style={{ fontStyle: "italic" }}>not yet classified</span>
        )}
      </DetailSection>

      <DetailSection title="Ingestion">
        <Field label="Status"   value={doc.ingestionStatus || "—"}/>
        <Field label="Pages"    value={doc.pages}/>
        <Field label="Size"     value={doc.size}/>
        <Field label="MIME"     value={doc.mimeType || "—"}/>
        <Field label="SHA256"   value={doc.sha256 ? `${doc.sha256.slice(0, 16)}…` : "—"}/>
        {doc.ingestionError && <Field label="Error" value={doc.ingestionError}/>}
      </DetailSection>

      <DetailSection title="Matched requirements">
        {matchedReqs.length === 0 ? (
          <span className="ink3 text-xs" style={{ fontStyle: "italic" }}>none — the matcher hasn't attached this doc to any requirement yet</span>
        ) : (
          <div className="flex col gap-1">
            {matchedReqs.map(rid => (
              <span key={rid} className="mono text-xs ink2">{rid}</span>
            ))}
          </div>
        )}
      </DetailSection>

      <DetailSection title="Extracted fields (KYC Phase 1)">
        {doc.extractedFields ? (
          <div>
            <Field label="doc_type"  value={doc.extractedFields.doc_type}/>
            <Field label="confidence" value={`${((doc.extractedFields.confidence || 0) * 100).toFixed(0)}%`}/>
            <Field label="model"     value={doc.extractedFields.model || "—"}/>
            <div className="mt-2">
              <div className="upper ink3" style={{ fontSize: 10 }}>Fields</div>
              <div className="bg2 border rounded mt-1 p-2" style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "4px 12px", fontSize: 11 }}>
                {Object.entries(doc.extractedFields.fields || {}).map(([k, v]) => (
                  <React.Fragment key={k}>
                    <span className="ink3 mono">{k}</span>
                    <span className="ink" style={{ wordBreak: "break-word" }}>
                      {v === "" ? <span className="ink3" style={{ fontStyle: "italic" }}>empty</span> : String(v)}
                    </span>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <span className="ink3 text-xs" style={{ fontStyle: "italic" }}>none — only KYC-tagged docs get field-level extraction</span>
        )}
      </DetailSection>

      <DetailSection title="Provenance">
        <Field label="Uploaded by" value={doc.uploadedBy || "—"}/>
        <Field label="Modified"    value={doc.modified || "—"}/>
        <Field label="Path"        value={doc.path || "—"}/>
      </DetailSection>
    </div>
  );
}

function DetailSection({ title, children }) {
  return (
    <div className="p-4" style={{ borderBottom: "1px solid var(--line)" }}>
      <div className="upper ink3 mb-2" style={{ fontSize: 10 }}>{title}</div>
      {children}
    </div>
  );
}

function Field({ label, value }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 12px", fontSize: 12 }}>
      <span className="ink3 mono text-xs">{label}</span>
      <span className="ink2" style={{ wordBreak: "break-word" }}>{value ?? "—"}</span>
    </div>
  );
}
