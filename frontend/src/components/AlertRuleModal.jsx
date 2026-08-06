// AlertRuleModal — create a user-defined alert rule from selected documents.
// Three rule types:
//   watch_docs  — track these specific docs for approaching date fields
//   watch_types — alert when new docs of these types are uploaded
//   field_date  — watch a specific date field across matching docs
import React, { useState } from "react";
import { createPortal } from "react-dom";
import { createAlertRule } from "../api/documents";

const RULE_TYPES = [
  { id: "watch_docs", icon: "📄", label: "Watch these documents",
    desc: "Alert me when date fields in these documents are approaching" },
  { id: "watch_types", icon: "📁", label: "Watch document types",
    desc: "Alert me when new documents of the same types are uploaded" },
  { id: "field_date", icon: "📅", label: "Watch date field",
    desc: "Alert me when a specific date field is approaching across documents" },
];

export default function AlertRuleModal({ onClose, selectedDocs }) {
  const [step, setStep] = useState("type"); // "type" | "configure"
  const [ruleType, setRuleType] = useState("watch_docs");
  const [name, setName] = useState("");
  const [fieldName, setFieldName] = useState("");
  const [daysBefore, setDaysBefore] = useState(30);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  // Derive context from selected docs
  const docTypes = [...new Set(selectedDocs.map(d => d.docType).filter(Boolean))];
  const docNames = selectedDocs.map(d => d.name).slice(0, 3);

  // Find common date fields across selected docs
  const dateFields = [...new Set(
    selectedDocs.flatMap(d => {
      const fields = d.extractedFields?.fields || {};
      return Object.keys(fields).filter(k => {
        const v = fields[k];
        return typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v);
      });
    })
  )].sort();

  const handleCreate = async () => {
    setSaving(true); setErr("");
    try {
      const config = ruleType === "watch_docs"
        ? { docIds: selectedDocs.map(d => d.id) }
        : ruleType === "watch_types"
        ? { docTypes }
        : { fieldName: fieldName || dateFields[0] || "", daysBefore, docTypes };

      await createAlertRule({
        name: name || `Watch: ${docNames.slice(0, 2).join(", ")}`,
        ruleType,
        config,
      });
      onClose(true); // true = created
    } catch (e) {
      setErr(e.message || "Failed to create rule");
    } finally {
      setSaving(false);
    }
  };

  return createPortal(
    <div onClick={() => onClose(false)} style={{
      position: "fixed", inset: 0, zIndex: 1100, background: "rgba(0,0,0,0.6)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 16,
    }}>
      <div className="bg1 border" onClick={e => e.stopPropagation()} style={{
        borderRadius: 16, width: "100%", maxWidth: 500, maxHeight: "88vh",
        display: "flex", flexDirection: "column", boxShadow: "0 24px 80px rgba(0,0,0,0.5)",
      }}>
        {/* Header */}
        <div className="row between p-3 border-b" style={{ alignItems: "center" }}>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span style={{ fontSize: 17 }}>🔔</span>
            <span className="serif" style={{ fontSize: 16 }}>Create alert rule</span>
          </div>
          <button onClick={() => onClose(false)} className="ink3"
            style={{ background: "none", border: "none", fontSize: 19, cursor: "pointer", lineHeight: 1 }}>✕</button>
        </div>

        <div style={{ padding: 18, overflowY: "auto" }}>
          {/* Context: selected documents */}
          <div className="bg2 border rounded-md" style={{ padding: "10px 14px", marginBottom: 16, fontSize: 12 }}>
            <span className="ink3">Based on </span>
            <span style={{ fontWeight: 600 }}>{selectedDocs.length} document{selectedDocs.length !== 1 ? "s" : ""}</span>
            <span className="ink3">: </span>
            <span className="ink2">{docNames.join(", ")}{selectedDocs.length > 3 ? ` +${selectedDocs.length - 3} more` : ""}</span>
            {docTypes.length > 0 && (
              <div style={{ marginTop: 4 }}>
                <span className="ink3">Types: </span>
                {docTypes.map(t => (
                  <span key={t} className="mono" style={{
                    fontSize: 10, padding: "2px 6px", borderRadius: 4,
                    background: "var(--bg3)", color: "var(--ink2)", marginRight: 4,
                  }}>{t}</span>
                ))}
              </div>
            )}
          </div>

          {/* Rule type picker */}
          <div className="ink3" style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, textTransform: "uppercase", letterSpacing: ".05em" }}>
            Rule type
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 18 }}>
            {RULE_TYPES.map(rt => (
              <button key={rt.id} onClick={() => setRuleType(rt.id)}
                className={ruleType === rt.id ? "" : "border bg2"}
                style={{
                  textAlign: "left", padding: "12px 14px", borderRadius: 10, cursor: "pointer",
                  border: ruleType === rt.id ? "2px solid var(--gold)" : undefined,
                  background: ruleType === rt.id ? "rgba(200,160,76,0.10)" : undefined,
                }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
                  {rt.icon} {rt.label}
                </div>
                <div style={{ fontSize: 11.5, color: "var(--ink3)", marginTop: 3 }}>{rt.desc}</div>
              </button>
            ))}
          </div>

          {/* Configuration */}
          {ruleType === "field_date" && (
            <div style={{ marginBottom: 16 }}>
              <div className="ink3" style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, textTransform: "uppercase", letterSpacing: ".05em" }}>
                Date field to watch
              </div>
              {dateFields.length > 0 ? (
                <select value={fieldName} onChange={e => setFieldName(e.target.value)}
                  className="border bg2" style={{ width: "100%", padding: "9px 10px", borderRadius: 8, fontSize: 13, color: "var(--ink)" }}>
                  <option value="">— Pick a field —</option>
                  {dateFields.map(f => (
                    <option key={f} value={f}>{f.replace(/_/g, " ")}</option>
                  ))}
                </select>
              ) : (
                <input value={fieldName} onChange={e => setFieldName(e.target.value)}
                  placeholder="e.g. expiry_date"
                  className="border bg2" style={{ width: "100%", padding: "9px 10px", borderRadius: 8, fontSize: 13, color: "var(--ink)", boxSizing: "border-box" }} />
              )}
              <div className="row gap-2" style={{ alignItems: "center", marginTop: 8 }}>
                <span className="ink3" style={{ fontSize: 12 }}>Alert within</span>
                <select value={daysBefore} onChange={e => setDaysBefore(Number(e.target.value))}
                  className="border bg2" style={{ padding: "6px 8px", borderRadius: 6, fontSize: 12, color: "var(--ink)" }}>
                  <option value={7}>7 days</option>
                  <option value={14}>14 days</option>
                  <option value={30}>30 days</option>
                  <option value={60}>60 days</option>
                  <option value={90}>90 days</option>
                </select>
              </div>
            </div>
          )}

          {/* Rule name */}
          <div style={{ marginBottom: 18 }}>
            <div className="ink3" style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, textTransform: "uppercase", letterSpacing: ".05em" }}>
              Rule name
            </div>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder={`Watch: ${docNames.slice(0, 2).join(", ")}`}
              className="border bg2" style={{ width: "100%", padding: "9px 10px", borderRadius: 8, fontSize: 13, color: "var(--ink)", boxSizing: "border-box" }} />
          </div>

          {err && (
            <div style={{ fontSize: 12, color: "var(--rose)", marginBottom: 12 }}>{err}</div>
          )}

          <button onClick={handleCreate} disabled={saving}
            className="btn-gold" style={{
              width: "100%", padding: "11px 14px", borderRadius: 10, fontSize: 14,
              fontWeight: 600, cursor: saving ? "default" : "pointer", opacity: saving ? 0.6 : 1,
            }}>
            {saving ? "Creating…" : "🔔 Create alert rule"}
          </button>
        </div>
      </div>
    </div>, document.body
  );
}
