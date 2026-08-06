// MiniFieldsList — compact editable field list with inline editing, quality bar,
// bbox citation, expandable arrays-of-objects, and per-field review diagnostics
// (anomalies, 6-dimension confidence breakdown, quality flags, confidence reasons).
// Used by AdvancedSidebar and FieldsTab.
// Extracted from AdvancedSidebar.jsx; enhanced 2026-07-24 to merge Review-tab diagnostics.
import React, { useEffect, useState } from "react";
import { editDocumentField, fetchDocument } from "../../api";

// Compact 6-dimension bars for one field
function DimBars({ dimensions }) {
  if (!dimensions || !Object.keys(dimensions).length) return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: "4px 8px", marginTop: 4 }}>
      {Object.entries(dimensions).map(([dim, score]) => (
        <div key={dim} style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ fontSize: 9, color: "var(--ink3)", minWidth: 60, textTransform: "capitalize" }}>{dim.replace(/_/g, " ")}</span>
          <div style={{ flex: 1, height: 3, borderRadius: 1, background: "var(--bg1)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: Math.round((score || 0) * 100) + "%",
              background: (score || 0) >= 0.7 ? "var(--emerald)" : (score || 0) >= 0.4 ? "#E0A23B" : "var(--rose)",
              borderRadius: 1 }} />
          </div>
          <span style={{ fontSize: 8, fontWeight: 600, color: "var(--ink2)", minWidth: 22, textAlign: "right" }}>{Math.round((score || 0) * 100)}%</span>
        </div>
      ))}
    </div>
  );
}

export default function MiniFieldsList({ doc, onCite, onDocUpdated, locatedField, activeBlockIds = [], fieldBlockMap = {} }) {
  const ef = doc?.extractedFields || {};
  const fields = ef.fields || {};
  const fieldBboxes = ef?.field_bboxes || {};
  const fieldConf = ef?.field_confidence || {};
  const [editPath, setEditPath] = useState(null); // "field_name" or "field_name.0.key"
  const [editVal, setEditVal] = useState("");
  const [saving, setSaving] = useState(false);
  const [quality, setQuality] = useState(null);
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    if (!doc?.id) return;
    fetch("/api/documents/" + encodeURIComponent(doc.id) + "/review")
      .then(r => r.json()).then(d => setQuality(d)).catch(() => {});
  }, [doc?.id]);

  const keys = Object.keys(fields).filter(k => !k.startsWith("_"));
  if (!keys.length) return <div className="ink3" style={{fontSize:12,padding:24,textAlign:"center",fontStyle:"italic"}}>No fields extracted yet.</div>;

  const cite = (fname) => {
    const bb = fieldBboxes[fname];
    if (!bb || !onCite) return;
    const bbox = { page: bb.page, x0: bb.x0, y0: bb.y0, x1: bb.x1, y1: bb.y1 };
    if (bb.page_w && bb.page_h) { bbox.page_w = bb.page_w; bbox.page_h = bb.page_h; }
    onCite({ page: bb.page, bbox, chunkPk: bb.chunk_pk || 0, quote: fname + ": " + fields[fname] }, 0);
  };

  const save = async (path) => {
    if (!doc || saving) return;
    setSaving(true);
    try {
      await editDocumentField(doc.id, { field_path: "fields." + path, value: editVal.trim() });
      setEditPath(null);
      if (onDocUpdated) { try { const f = await fetchDocument(doc.id); onDocUpdated(f); } catch {} }
    } catch {}
    setSaving(false);
  };

  const qScores = quality?.field_scores || {};
  const scoreColor = (s) => s >= 0.8 ? "var(--emerald)" : s >= 0.5 ? "#E0A23B" : "var(--rose)";
  const renderValue = (v) => {
    if (v === null || v === undefined || v === "") return <span style={{color:"var(--ink3)",fontStyle:"italic"}}>—</span>;
    if (typeof v === "string") return <span style={{wordBreak:"break-word"}}>{v.length > 120 ? v.slice(0,120)+"…" : v}</span>;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return null;
  };

  return (
    <div>
      {/* Quality summary bar */}
      {quality && (
        <div style={{marginBottom:10, padding:"8px 10px", borderRadius:8, background:"var(--bg2)", border:"1px solid var(--line)"}}>
          <div className="row between" style={{alignItems:"center"}}>
            <span style={{fontSize:12,fontWeight:700,color:"var(--ink)"}}>
              {quality.quality_level === "good" ? "✅" : quality.quality_level === "fair" ? "⚠️" : "❌"} {quality.quality_level?.toUpperCase()}
            </span>
            <span style={{fontSize:11,fontWeight:600,color:scoreColor(quality.overall_quality)}}>
              {Math.round(quality.overall_quality*100)}%
            </span>
          </div>
          <div style={{height:4,borderRadius:2,background:"var(--bg1)",overflow:"hidden",marginTop:4}}>
            <div style={{height:"100%",width: Math.round(quality.overall_quality*100) + "%",background:scoreColor(quality.overall_quality),borderRadius:2}}/>
          </div>
        </div>
      )}

      {keys.map(k => {
        const v = fields[k];
        const q = qScores[k];
        const score = q?.confidence || fieldConf[k] || 0;
        const hasBbox = !!fieldBboxes[k];
        const isArray = Array.isArray(v) && v.length > 0 && typeof v[0] === "object";
        const isExpanded = expanded[k];
        const isEditing = editPath === k;
        const linkedBlockIds = fieldBlockMap[k] || [];
        const isActive = linkedBlockIds.some(bid => activeBlockIds.includes(bid));

        return (
          <div key={k} id={"field-" + k}
            style={{ padding: "8px 0", borderBottom: "1px solid var(--line)",
              background: isActive ? "rgba(124,111,214,0.08)" : (locatedField === k ? "rgba(226,188,104,0.06)" : "transparent"),
              borderLeft: isActive ? "3px solid #7C6FD6" : undefined,
              paddingLeft: isActive ? 6 : undefined,
            }}>
            {/* Header row */}
            <div className="row between" style={{alignItems:"flex-start"}}>
              <div style={{flex:1, minWidth:0}}>
                <div className="row gap-2" style={{alignItems:"center", marginBottom:2}}>
                  {/* Expand toggle — click to see diagnostics */}
                  <button onClick={() => setExpanded(e => ({...e, [k]: !e[k]}))}
                    style={{background:"none",border:"none",cursor:"pointer",fontSize:9,color:"var(--ink3)",padding:0,lineHeight:1}}
                    title={isExpanded ? "Collapse" : "Expand diagnostics"}>
                    {isExpanded ? "▼" : "▶"}
                  </button>
                  <span style={{fontSize:10.5, fontWeight:700, color:"var(--ink)"}}>{k.replace(/_/g," ")}</span>
                  {hasBbox && <button onClick={() => cite(k)} title="Locate in document" style={{background:"none",border:"none",cursor:"pointer",fontSize:10,padding:0,lineHeight:1,opacity:0.5}}>📍</button>}
                  {isArray && (
                    <span style={{fontSize:9, color:"var(--ink3)"}}>{v.length} items</span>
                  )}
                  {/* Edit button */}
                  {!isArray && (
                    <button onClick={() => { setEditPath(k); setEditVal(typeof v==="string"?v:JSON.stringify(v)); }}
                      title="Edit value" style={{background:"none",border:"none",cursor:"pointer",fontSize:10,padding:0,lineHeight:1,opacity:0.4}}>✏️</button>
                  )}
                </div>
                {/* Value row — edit mode or display */}
                {isEditing ? (
                  <div className="row gap-2" style={{alignItems:"center"}}>
                    <input value={editVal} onChange={e => setEditVal(e.target.value)} autoFocus
                      onKeyDown={e => { if(e.key==="Enter") save(k); if(e.key==="Escape") setEditPath(null); }}
                      className="bg1 border" style={{flex:1,padding:"2px 6px",borderRadius:3,fontSize:10,color:"var(--ink)",outline:"none"}} />
                    <button onClick={() => save(k)} disabled={saving} className="btn-gold" style={{padding:"1px 6px",borderRadius:3,fontSize:9,cursor:"pointer"}}>Save</button>
                    <button onClick={() => setEditPath(null)} className="border bg1" style={{padding:"1px 6px",borderRadius:3,fontSize:9,cursor:"pointer",color:"var(--ink3)"}}>Cancel</button>
                  </div>
                ) : (
                  <div style={{fontSize:10, color:"var(--ink2)", lineHeight:1.4}}>
                    {isArray ? v.length + " items" : renderValue(v)}
                  </div>
                )}
              </div>
              {/* Confidence mini-bar */}
              <div className="row gap-1" style={{alignItems:"center", flexShrink:0, marginLeft:8}}>
                <div style={{width:28,height:3,borderRadius:1,background:"var(--bg1)",overflow:"hidden"}}>
                  <div style={{height:"100%",width: Math.round(score*100) + "%",background:scoreColor(score),borderRadius:1}}/>
                </div>
                <span style={{fontSize:9,fontWeight:600,color:scoreColor(score),minWidth:26,textAlign:"right"}}>{Math.round(score*100)}%</span>
              </div>
            </div>

            {/* Expanded diagnostics */}
            {isExpanded && !isEditing && (
              <div style={{marginTop:6, padding:"8px 10px", borderRadius:6, background:"var(--bg2)", border:"1px solid var(--line)"}}>
                {/* Full value */}
                {!isArray && (
                  <div style={{marginBottom:8}}>
                    <div style={{fontSize:9, fontWeight:600, color:"var(--ink3)", textTransform:"uppercase", letterSpacing:".05em", marginBottom:2}}>Value</div>
                    <div style={{fontSize:10.5, color:"var(--ink)", whiteSpace:"pre-wrap", wordBreak:"break-word", maxHeight:80, overflowY:"auto",
                      fontFamily:"SF Mono, Menlo, monospace", background:"var(--bg1)", padding:"4px 6px", borderRadius:3}}>
                      {typeof v === "object" ? JSON.stringify(v, null, 2) : String(v)}
                    </div>
                  </div>
                )}

                {/* 6-dimension breakdown */}
                {q?.dimensions && Object.keys(q.dimensions).length > 0 && (
                  <div style={{marginBottom:6}}>
                    <div style={{fontSize:9, fontWeight:600, color:"var(--ink3)", textTransform:"uppercase", letterSpacing:".05em", marginBottom:3}}>Confidence Dimensions</div>
                    <DimBars dimensions={q.dimensions} />
                  </div>
                )}

                {/* Quality flags */}
                {q?.quality_flags?.length > 0 && (
                  <div style={{marginBottom:6}}>
                    <div style={{fontSize:9, fontWeight:600, color:"var(--ink3)", textTransform:"uppercase", letterSpacing:".05em", marginBottom:2}}>Quality Flags</div>
                    <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                      {q.quality_flags.map((flag, i) => (
                        <span key={i} style={{padding:"2px 7px",borderRadius:10,fontSize:9,background:"rgba(239,68,68,0.12)",color:"var(--rose)",border:"1px solid rgba(239,68,68,0.2)"}}>⚠️ {flag}</span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Confidence reasons */}
                {q?.confidence_reasons?.length > 0 && (
                  <div>
                    <div style={{fontSize:9, fontWeight:600, color:"var(--ink3)", textTransform:"uppercase", letterSpacing:".05em", marginBottom:2}}>Why This Score?</div>
                    <ul style={{margin:0, paddingLeft:16, fontSize:10, color:"var(--ink2)", lineHeight:1.5}}>
                      {q.confidence_reasons.map((reason, i) => <li key={i}>{reason}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* Expanded sub-fields for arrays of objects */}
            {isArray && isExpanded && v.map((item, idx) => {
              const itemKeys = Object.keys(item).filter(ik => !ik.startsWith("_"));
              return (
                <div key={idx} style={{marginTop:6, marginLeft:10, padding:"6px 8px", borderRadius:4, background:"var(--bg2)", border:"1px solid var(--line)"}}>
                  <span style={{fontSize:9,fontWeight:600,color:"var(--ink3)",textTransform:"uppercase",letterSpacing:".05em"}}>#{idx+1}</span>
                  {itemKeys.map(ik => {
                    const iv = item[ik];
                    const itemPath = k + "." + idx + "." + ik;
                    return (
                      <div key={ik} className="row between" style={{alignItems:"center", marginTop:3}}>
                        <span style={{fontSize:9.5,color:"var(--ink2)",fontWeight:500}}>{ik.replace(/_/g," ")}</span>
                        {editPath === itemPath ? (
                          <div className="row gap-1" style={{alignItems:"center",flex:1,marginLeft:8}}>
                            <input value={editVal} onChange={e => setEditVal(e.target.value)} autoFocus
                              onKeyDown={e => { if(e.key==="Enter") save(itemPath); if(e.key==="Escape") setEditPath(null); }}
                              className="bg1 border" style={{flex:1,padding:"1px 4px",borderRadius:2,fontSize:9,color:"var(--ink)",outline:"none"}} />
                            <button onClick={() => save(itemPath)} disabled={saving} className="btn-gold" style={{padding:"0 5px",borderRadius:2,fontSize:8,cursor:"pointer"}}>✓</button>
                          </div>
                        ) : (
                          <span onClick={() => { setEditPath(itemPath); setEditVal(String(iv)); }}
                            style={{fontSize:9,color:"var(--ink)",cursor:"pointer",marginLeft:8,textAlign:"right",flex:1}}
                            title="Click to edit">{String(iv).length>60 ? String(iv).slice(0,60)+"…" : String(iv)}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
