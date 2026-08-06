// Extracted from DocumentChatPanel.jsx.
import { exportDocJson, fetchCoverage, fetchSchemaJson } from "../../api";
import { ErrorState, LoadingState, Pill } from "../Shell.jsx";
import { useEffect, useMemo, useState } from "react";

// Per-doc in-memory cache of the extracted JSON. NOTE: this const was orphaned in
// MiniMarkdown.jsx when this module was split out (00b9fb8) — JsonTab referenced an
// undefined JSON_CACHE and crashed the tab to a blank page. It lives with its only
// user now.
const JSON_CACHE = new Map();

export function invalidateJsonCache(docId) {
  if (docId) JSON_CACHE.delete(docId);
}

// Extracted values rendered in the approved schema's shape: every schema field in order, its
// value + provenance (extracted / derived-from-universal / missing), and a conformance summary.
// Render a schema field value readably — including NESTED records (a lab panel's
// test_results → each test → an `attributes` array of {label,value}). The old
// JSON.stringify dumped an unreadable blob, so nested medical parameters / line items
// looked "missing" even though they were extracted. Recurses, stacking records and
// unfolding {label,value} pairs. `kind` is an internal discriminator → skipped.
function readableValue(v, depth = 0) {
  if (v == null || v === "") return <span className="ink3">—</span>;
  if (typeof v !== "object") return String(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="ink3">—</span>;
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {v.map((item, i) => <div key={i}>{readableValue(item, depth + 1)}</div>)}
      </div>
    );
  }
  // an atomic {label, value} pair
  if (v.value != null && v.value !== "") {
    const lbl = v.label != null && v.label !== "" ? String(v.label).replace(/_/g, " ") : null;
    return lbl ? `${lbl}: ${v.value}` : String(v.value);
  }
  // a record: scalar fields on one line, nested arrays/objects (attributes) unfolded below
  const scalars = [];
  const nested = [];
  for (const [k, z] of Object.entries(v)) {
    if (z == null || z === "" || k === "kind" || k.startsWith("_")) continue;
    if (typeof z === "object") nested.push([k, z]);
    else scalars.push(`${k.replace(/_/g, " ")}: ${z}`);
  }
  if (scalars.length === 0 && nested.length === 0) return <span className="ink3">—</span>;
  return (
    <div>
      {scalars.length > 0 && <div style={{ fontWeight: nested.length ? 600 : 400 }}>{scalars.join(" · ")}</div>}
      {nested.map(([k, z], i) => (
        <div key={i} style={{ paddingLeft: 10, marginTop: 2 }}>{readableValue(z, depth + 1)}</div>
      ))}
    </div>
  );
}

// Extraction-coverage audit: proves the lossless-chunk guarantee and grades how many
// salient page values (numbers/dates) the structured extraction captured, listing any
// it missed. Self-fetches; deterministic + cheap on the backend (no LLM).
export function CoverageBadge({ docId }) {
  const [c, setC] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    fetchCoverage(docId).then(r => { if (live) setC(r); }).catch(() => {});
    return () => { live = false; };
  }, [docId]);
  if (!c || c.grade === "na") return null;
  const tone = { green: "#3FA47A", amber: "#E0A23B", red: "#D8625E" }[c.grade] || "#8B8B8B";
  const { captured, considered, pct } = c.structured;
  const missed = c.unstructured?.length || 0;
  return (
    <div className="border rounded-md mt-2" style={{ borderColor: "var(--line)", overflow: "hidden" }}>
      <button onClick={() => setOpen(o => !o)} className="row between" style={{
        width: "100%", padding: "7px 12px", background: "transparent", border: "none",
        cursor: "pointer", alignItems: "center", gap: 8 }}>
        <span className="row" style={{ alignItems: "center", gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: 8, background: tone, flexShrink: 0 }} />
          <span style={{ fontSize: 12, fontWeight: 600 }}>Extraction coverage</span>
          <span className="mono" style={{ fontSize: 11, color: tone }}>{pct}%</span>
          <span className="ink3" style={{ fontSize: 11 }}>
            {captured}/{considered} values structured{missed ? ` · ${missed} unstructured` : ""}
          </span>
        </span>
        <span className="ink3" style={{ fontSize: 11 }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div style={{ padding: "0 12px 10px", fontSize: 11 }}>
          <div className="ink3" style={{ marginBottom: 6 }}>
            All page values are captured verbatim in the indexed chunks (searchable in chat).
            {c.referenceExcluded ? ` ${c.referenceExcluded} reference threshold(s) excluded.` : ""}
          </div>
          {missed > 0 ? (
            <>
              <div style={{ fontWeight: 600, marginBottom: 3 }}>Salient values not mapped to a field:</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {c.unstructured.map((u, i) => (
                  <div key={i} className="row" style={{ gap: 8, alignItems: "baseline" }}>
                    <span className="mono" style={{ color: tone, minWidth: 70 }}>{u.value}</span>
                    <span className="ink3" style={{ fontSize: 10 }}>{u.kind} · “{u.context}”</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div style={{ color: "#3FA47A" }}>Every salient number/date on the page is in the structured extraction.</div>
          )}
        </div>
      )}
    </div>
  );
}

function SchemaShapedView({ data, docId }) {
  const { schemaSource, schemaLabel, record = {}, fieldSources = {}, conformance = {} } = data;
  const srcColor = { extracted: "#3FA47A", derived: "#E0A23B", missing: "#8B8B8B" };
  const fmt = (v) => (v == null ? "—" : readableValue(v));
  const entries = Object.entries(record);
  return (
    <div style={{ maxHeight: "calc(100vh - 300px)", overflow: "auto" }}>
      <div className="bg2 border rounded-md p-3 mb-3">
        <div className="row between" style={{ alignItems: "center" }}>
          <div className="serif font-semibold" style={{ fontSize: 14 }}>{schemaLabel}</div>
          {schemaSource === "library" && (
            <Pill color={conformance.populated >= conformance.total ? "emerald" : "amber"}>
              {conformance.populated}/{conformance.total} fields
            </Pill>
          )}
        </div>
        {schemaSource === "universal" ? (
          <div className="ink3 mt-2" style={{ fontSize: 11 }}>
            No approved schema for this type yet — showing the universal extraction. Approve this
            type's schema in the admin console to get a typed, conformant record here.
          </div>
        ) : (
          <div className="ink3 mt-2" style={{ fontSize: 11 }}>
            {conformance.missing?.length || 0} missing
            {conformance.missingRequired?.length ? ` (${conformance.missingRequired.length} required)` : ""}.
            <span style={{ color: srcColor.extracted }}> ● extracted</span>
            <span style={{ color: srcColor.derived }}> ● derived</span>
            <span style={{ color: srcColor.missing }}> ● missing</span>
          </div>
        )}
        {docId && <CoverageBadge docId={docId} />}
      </div>
      <div className="bg2 border rounded-md" style={{ overflow: "hidden" }}>
        {entries.map(([k, v], i) => {
          const src = fieldSources[k] || (schemaSource === "universal" ? "extracted" : "missing");
          return (
            <div key={k} className="row" style={{ padding: "7px 12px", gap: 10,
                 borderTop: i ? "1px solid var(--line)" : "none", alignItems: "flex-start" }}>
              <div className="mono ink3" style={{ fontSize: 11, minWidth: 150, flexShrink: 0 }}>{k}</div>
              <div style={{ fontSize: 12, flex: 1, wordBreak: "break-word",
                   color: v == null ? "#8B8B8B" : "var(--ink)", fontStyle: v == null ? "italic" : "normal" }}>{fmt(v)}</div>
              <span style={{ fontSize: 9, color: srcColor[src] || "#8B8B8B", flexShrink: 0, whiteSpace: "nowrap" }}>● {src}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function JsonTab({ docId, extractedFields, revealed }) {  // revealed: re-render trigger; prefill comes from (detokenized) extractedFields
  // If the doc was already classified + fact-extracted at ingest (layer 1
  // structured-facts path, or the KYC vision extractor), show that
  // synchronously — no extra LLM call needed. Only docs whose type didn't
  // map to a schema fall through to the on-demand extractor.
  const prefilled = useMemo(() => {
    if (extractedFields && extractedFields.fields) {
      return JSON.stringify(extractedFields, null, 2);
    }
    return null;
  }, [extractedFields]);

  const [body, setBody] = useState(() => {
    const cached = JSON_CACHE.get(docId);
    return prefilled || (typeof cached === "string" ? cached : null);
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Sync `body` when `prefilled` changes. useState's initializer only runs
  // once, so without this, a Re-extract that updates the parent's
  // extractedFields prop wouldn't refresh the displayed JSON — the user
  // would see stale data with old / missing field_bboxes.
  useEffect(() => {
    if (prefilled !== null) {
      setBody(prefilled);
      // Also refresh the cache so a later tab-switch picks up the new value.
      JSON_CACHE.set(docId, prefilled);
    }
  }, [prefilled, docId]);

  useEffect(() => {
    // Skip the on-demand extractor when: (a) prefilled from ingestion
    // already populated body, (b) we already have a cached extract for
    // this doc, (c) a request is already in flight.
    if (prefilled || body !== null || loading) return;
    if (JSON_CACHE.has(docId)) {
      const cached = JSON_CACHE.get(docId);
      if (typeof cached === "string") {
        setBody(cached);
        return;
      }
    }
    setLoading(true);
    exportDocJson(docId)
      .then(r => {
        JSON_CACHE.set(docId, r.body);
        setBody(r.body);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  const [view, setView] = useState("schema"); // "schema" | "tree" | "raw"
  const [schemaData, setSchemaData] = useState(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  useEffect(() => {
    if (view !== "schema" || schemaData || schemaLoading) return;
    setSchemaLoading(true);
    fetchSchemaJson(docId).then(setSchemaData).catch(() => setSchemaData({ error: true }))
      .finally(() => setSchemaLoading(false));
  }, [view, docId, schemaData, schemaLoading]);
  const _download = (fname, text, mime) => {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const a = document.createElement("a");
    a.href = url; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  };

  if (loading) return <LoadingState label="Extracting to JSON…"/>;
  if (error)   return <ErrorState message={error}/>;
  if (body == null) return null;

  // Parse once for the tree view. If parsing fails (model returned something
  // non-JSON), fall back to raw display only.
  let parsed = null;
  let parseErr = null;
  try { parsed = JSON.parse(body); } catch (e) { parseErr = e.message; }

  return (
    <div className="p-4">
      <div className="row gap-2 mb-3" style={{ alignItems: "center" }}>
        <button onClick={() => setView("schema")}
                className={view === "schema" ? "btn-gold" : "border bg2"}
                style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          Schema
        </button>
        {parsed != null && (
          <>
            <button onClick={() => setView("tree")}
                    className={view === "tree" ? "btn-gold" : "border bg2"}
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              Tree
            </button>
            <button onClick={() => setView("raw")}
                    className={view === "raw" ? "btn-gold" : "border bg2"}
                    style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              Raw
            </button>
          </>
        )}
        <div style={{ flex: 1 }} />
        {view === "schema" && schemaData?.record && (
          <>
            <button onClick={() => _download(`${docId}.json`, JSON.stringify(schemaData.record, null, 2), "application/json")}
                    className="border bg2" style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              ↓ JSON
            </button>
            <button onClick={() => {
                      const rows = [["field", "value", "source"]];
                      Object.entries(schemaData.record).forEach(([k, v]) => rows.push(
                        [k, (v && typeof v === "object") ? JSON.stringify(v) : (v ?? ""), schemaData.fieldSources?.[k] || ""]));
                      const csv = rows.map(r => r.map(c => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
                      _download(`${docId}.csv`, csv, "text/csv");
                    }}
                    className="border bg2" style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
              ↓ CSV
            </button>
          </>
        )}
        <button onClick={() => navigator.clipboard.writeText(view === "schema" && schemaData?.record ? JSON.stringify(schemaData.record, null, 2) : body)}
                className="border bg2" style={{ padding: "5px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
          Copy
        </button>
      </div>
      {prefilled && (
        <div className="ink3 text-xs mb-2" style={{ fontStyle: "italic" }}>
          Showing the structured facts extracted at ingest time — no extra LLM call needed.
        </div>
      )}
      {view === "schema" ? (
        schemaLoading ? <LoadingState label="Shaping to schema…"/> :
        (!schemaData || schemaData.error) ?
          <div className="ink3 p-3" style={{ fontSize: 12, fontStyle: "italic" }}>Couldn't load the schema view.</div> :
          <SchemaShapedView data={schemaData} docId={docId} />
      ) : parsed != null && view === "tree" ? (
        <div className="bg2 border p-3" style={{
          borderRadius: 4,
          maxHeight: "calc(100vh - 280px)",
          overflow: "auto",
          fontFamily: "var(--mono)",
          fontSize: 12,
        }}>
          <JsonNode value={parsed} initiallyOpen depth={0} />
        </div>
      ) : (
        <pre className="bg2 border p-3" style={{
          whiteSpace: "pre-wrap",
          fontFamily: "var(--mono)",
          fontSize: 12,
          borderRadius: 4,
          maxHeight: "calc(100vh - 280px)",
          overflow: "auto",
        }}>{body}</pre>
      )}
    </div>
  );
}


// ── Collapsible JSON tree ────────────────────────────────────────────────────
// One reusable recursive node. Objects + arrays render a clickable summary
// with a child count; toggling expands the keys/items inline. Scalars render
// inline with type-appropriate colours. Top two levels open by default so
// reviewers don't have to click to see the structure.
function JsonNode({ name, value, initiallyOpen, depth }) {
  const [open, setOpen] = useState(initiallyOpen || depth < 2);

  const renderScalar = (v) => {
    if (v === null) return <span style={{ color: "#8B7FD6" }}>null</span>;
    if (typeof v === "boolean") return <span style={{ color: "#E0A23B" }}>{String(v)}</span>;
    if (typeof v === "number") return <span style={{ color: "#3FA47A" }}>{v}</span>;
    return <span style={{ color: "#E2BC68" }}>"{String(v)}"</span>;
  };

  if (value === null || typeof value !== "object") {
    return (
      <div style={{ paddingLeft: depth * 12 }}>
        {name !== undefined && <span className="ink3">"{name}"</span>}
        {name !== undefined && <span className="ink3">: </span>}
        {renderScalar(value)}
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries = isArray ? value.map((v, i) => [i, v]) : Object.entries(value);
  const count = entries.length;
  const openChar = isArray ? "[" : "{";
  const closeChar = isArray ? "]" : "}";

  return (
    <div style={{ paddingLeft: depth * 12 }}>
      <span style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(o => !o)}>
        <span className="ink3" style={{ width: 12, display: "inline-block", textAlign: "center" }}>
          {open ? "▾" : "▸"}
        </span>
        {name !== undefined && <span className="ink3">"{name}"</span>}
        {name !== undefined && <span className="ink3">: </span>}
        <span>{openChar}</span>
        {!open && (
          <span className="ink3" style={{ marginLeft: 4 }}>
            {count} {isArray ? "item" : "key"}{count === 1 ? "" : "s"}{closeChar}
          </span>
        )}
      </span>
      {open && (
        <>
          <div>
            {entries.map(([k, v], i) => (
              <JsonNode key={i} name={isArray ? undefined : k} value={v} depth={depth + 1} />
            ))}
          </div>
          <div style={{ paddingLeft: 0 }}>{closeChar}</div>
        </>
      )}
    </div>
  );
}

export { JsonTab };
