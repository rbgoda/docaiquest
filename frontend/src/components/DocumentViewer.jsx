import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { LoadingState, ErrorState } from "./Shell.jsx";
import PdfDocumentViewer from "./PdfDocumentViewer.jsx";
import TextFileViewer from "./TextFileViewer.jsx";
import XlsxFileViewer from "./XlsxFileViewer.jsx";
import { FieldBoxes, FieldLegend, fieldCount } from "./FieldOverlay.jsx";
import DragDivider from "./DragDivider.jsx";
import { documentFileUrl } from "../api";


// DocumentViewer · multi-format evidence renderer for the Review screen.
//
// Routes the doc to the right inline viewer based on mimeType + filename:
//   * image/*           → ImageViewer (with overlay support for the focused citation)
//   * text/csv / *.csv  → CsvViewer (parses + renders as a table)
//   * everything else   → PdfDocumentViewer
//
// Mirrors the routing logic in DocumentChatPanel so the Review tab + doc
// panel handle the same uploads identically. Previously this component
// fell through to PdfDocumentViewer for everything, which made image-only
// passports + CSV statements render as a "failed to load PDF" error.

const DocumentViewer = ({ doc, highlights, focusedHl, setFocusedHl, focusField, focusKey }) => {
  const [zoom, setZoom] = useState(100);        // in-pane zoom (± controls)
  const [fieldsW, setFieldsW] = useState(300);  // draggable width of the fields panel
  const [fieldsOpen, setFieldsOpen] = useState(false);  // fields collapsed by default (document-first)
  // M40 Phase E · per-field overlay state.
  //
  //   focusedField   — name of the typed extracted field the reviewer just
  //                    clicked in the right-rail legend; pulses the matching
  //                    colored box on the doc for ~2.4s, then clears.
  //
  // The legend mounts as a sibling aside to the doc, only when the document
  // has any extracted fields (avoids empty rail padding on docs the
  // extractor hasn't processed). Layout: doc grows, legend is fixed 260px.
  const [focusedField, setFocusedField] = useState(null);
  const focusFieldTimerRef = useRef(null);
  const selectField = (name) => {
    setFocusedField(name);
    if (focusFieldTimerRef.current) clearTimeout(focusFieldTimerRef.current);
    focusFieldTimerRef.current = setTimeout(() => setFocusedField(null), 2400);
  };
  useEffect(() => () => focusFieldTimerRef.current && clearTimeout(focusFieldTimerRef.current), []);
  // A chat citation was clicked → pulse the named field's box (scrolls into view via the overlay).
  useEffect(() => {
    if (focusField) selectField(focusField);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusField, focusKey]);

  if (!doc) {
    return (
      <section className="flex col min0" style={{ background: "var(--bg)" }}>
        <div className="grow flex col" style={{ alignItems: "center", justifyContent: "center", padding: 40, textAlign: "center" }}>
          <Icon name="folder" size={48}/>
          <div className="serif font-semibold mt-3" style={{ fontSize: 18 }}>No document linked</div>
          <div className="ink3 mt-2 text-base" style={{ maxWidth: "40ch" }}>
            This requirement has no matched document yet. Click "Replace" to choose one manually.
          </div>
        </div>
      </section>
    );
  }

  const mt = (doc.mimeType || "").toLowerCase();
  const name = (doc.name || "").toLowerCase();
  const isImage = mt.startsWith("image/")
                || /\.(jpe?g|png|gif|webp|bmp|tiff?)$/.test(name);
  const isXlsx = mt === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              || name.endsWith(".xlsx");
  const isCsv = !isXlsx && (mt === "text/csv" || mt === "application/csv"
             || mt === "application/vnd.ms-excel"
             || name.endsWith(".csv"));
  // Plain-text uploads aren't PDFs — render as text, not via PDF.js.
  const isText = (mt.startsWith("text/") && !isCsv)
              || mt === "message/rfc822"
              || /\.(txt|md|markdown|log|eml|text)$/.test(name);

  // Mount the legend only when the doc has at least one extracted field to
  // surface. Keeps the layout clean for not-yet-processed uploads and for
  // simple text docs the extractor doesn't touch (EML, TXT, MD).
  const hasFields = fieldCount(doc) > 0;

  const zBtn = { width: 26, height: 24, borderRadius: 6, cursor: "pointer", fontSize: 14, lineHeight: 1 };
  return (
    <div className="flex col grow min0" style={{ overflow: "hidden" }}>
      {/* zoom toolbar */}
      <div className="row" style={{ gap: 6, alignItems: "center", padding: "6px 10px", borderBottom: "1px solid var(--line)", flex: "0 0 auto" }}>
        <button className="border bg2 hover-bg" style={zBtn} title="Zoom out"
          onClick={() => setZoom((z) => Math.max(50, z - 15))}>−</button>
        <span className="mono ink3" style={{ fontSize: 11, minWidth: 40, textAlign: "center" }}>{zoom}%</span>
        <button className="border bg2 hover-bg" style={zBtn} title="Zoom in"
          onClick={() => setZoom((z) => Math.min(300, z + 15))}>+</button>
        <button className="border bg2 hover-bg" style={{ ...zBtn, width: "auto", padding: "0 9px", fontSize: 11 }}
          title="Reset zoom" onClick={() => setZoom(100)}>Reset</button>
        {hasFields && (
          <button className="border bg2 hover-bg" style={{ ...zBtn, marginLeft: "auto", width: "auto", padding: "0 11px", fontSize: 11,
            background: fieldsOpen ? "rgba(200,160,76,0.16)" : undefined, color: fieldsOpen ? "var(--gold2)" : "var(--ink2)" }}
            title={fieldsOpen ? "Hide extracted fields" : "Show extracted fields"}
            onClick={() => setFieldsOpen((o) => !o)}>
            {fieldsOpen ? "Fields ✕" : `⌗ Fields (${fieldCount(doc)})`}
          </button>
        )}
      </div>
      <div className="flex grow min0" style={{ overflow: "hidden" }}>
        <section className="flex col grow min0" style={{ background: "var(--bg)", overflow: "auto" }}>
          {isImage ? (
            <ImageViewer doc={doc} highlights={highlights} focusedHl={focusedHl} setFocusedHl={setFocusedHl}
              zoom={zoom} focusedField={focusedField} onSelectField={selectField} />
          ) : isXlsx ? (
            <XlsxFileViewer doc={doc} zoom={zoom}/>
          ) : isCsv ? (
            <CsvViewer doc={doc} zoom={zoom}/>
          ) : isText ? (
            <TextFileViewer doc={doc} zoom={zoom}/>
          ) : (
            <PdfDocumentViewer doc={doc} highlights={highlights} focusedHl={focusedHl} setFocusedHl={setFocusedHl}
              zoom={zoom} focusedField={focusedField} onSelectField={selectField} />
          )}
        </section>
        {hasFields && fieldsOpen && (<>
          <DragDivider getWidth={() => fieldsW} setWidth={setFieldsW} min={190} max={520} invert />
          <div style={{ width: fieldsW, flex: `0 0 ${fieldsW}px`, minWidth: 0, overflow: "auto" }}>
            <FieldLegend doc={doc} focusedField={focusedField} onSelectField={selectField} />
          </div>
        </>)}
      </div>
    </div>
  );
};

export default DocumentViewer;


// ── ImageViewer · scans of passports / receipts / IDs ─────────────────────
// Streams the file through the backend (same /file endpoint PDF.js uses) so
// CORS + per-tenant routing stays consistent.
//
// Highlight rendering (M40 · full-bbox rollout):
//   * If a highlight carries `bbox` — normalized [x0,y0,x1,y1] in 0..1 page
//     space, OR an object {x0,y0,x1,y1,page_w,page_h} from the fact extractor —
//     render a real rectangle at the actual citation region with a numbered pin.
//   * If bbox is missing (back-compat: seeded JSX demo data without coords),
//     fall back to the previous full-image gold tint so the citation is at
//     least visible — but mark it as approximate via dashed border.
//
// We use the natural image size from `<img>` onLoad to compute pixel offsets
// even though the highlight coords are normalized — the image element is
// itself responsive (maxWidth 100%), so the overlay container shares the same
// width and percentages map cleanly.

function ImageViewer({ doc, highlights, focusedHl, setFocusedHl, zoom = 100, focusedField, onSelectField }) {
  const src = documentFileUrl(doc.id);
  const scale = zoom / 100;
  // M40 · capture natural dimensions on load so FieldBoxes can project
  // normalized / pixel-space bboxes back onto the rendered image.
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 });

  // M40 Phase E · citation rendering policy: when fields are extracted, we
  // give the doc surface to the field overlays. Citations (from chat
  // messages — `isCitation: true`) become click-only pulses: they're hidden
  // until `focusedHl` matches their id, then they fade in / out via the
  // docaiq-cite-pulse keyframe and auto-clear via the parent's existing
  // setFocusedHl(null) timer.
  const renderCitation = (hl) => focusedHl === hl.id;
  const visibleHighlights = (highlights || []).filter(hl => !hl.isCitation || renderCitation(hl));

  // Normalize every highlight into a {pct, label, color, id, isApprox} shape.
  // `pct` is {left, top, width, height} in % for absolute positioning inside
  // the relative-positioned wrapper, which matches the rendered image's box.
  const rects = visibleHighlights.map(hl => {
    const bb = hl.bbox;
    if (Array.isArray(bb) && bb.length === 4) {
      const [x0, y0, x1, y1] = bb;
      return {
        id: hl.id, pin: hl.pin, color: hl.color,
        pct: { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` },
        isApprox: false, text: hl.text, isCitation: !!hl.isCitation,
      };
    }
    if (bb && typeof bb === "object" && bb.page_w && bb.page_h) {
      const { x0, y0, x1, y1, page_w, page_h } = bb;
      return {
        id: hl.id, pin: hl.pin, color: hl.color,
        pct: {
          left: `${(x0 / page_w) * 100}%`, top: `${(y0 / page_h) * 100}%`,
          width: `${((x1 - x0) / page_w) * 100}%`, height: `${((y1 - y0) / page_h) * 100}%`,
        },
        isApprox: false, text: hl.text, isCitation: !!hl.isCitation,
      };
    }
    // Fallback: no coords — soft full-image tint, dashed to read as "approximate".
    return {
      id: hl.id, pin: hl.pin, color: hl.color,
      pct: { left: 0, top: 0, width: "100%", height: "100%" },
      isApprox: true, text: hl.text, isCitation: !!hl.isCitation,
    };
  });

  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
      <div style={{
        position: "relative",
        transform: `scale(${scale})`,
        transformOrigin: "top center",
        transition: "transform 80ms ease-out",
      }}>
        <img
          src={src}
          alt={doc.name}
          onLoad={(e) => setImgSize({ w: e.target.clientWidth, h: e.target.clientHeight })}
          style={{
            display: "block",
            maxWidth: "100%", height: "auto", borderRadius: 4,
            boxShadow: "0 2px 8px rgba(0,0,0,0.25)", background: "#1a1a1a",
          }}
        />
        {/* M40 · per-field typed boxes (always on, color = field family). */}
        {imgSize.w > 0 && (
          <FieldBoxes
            extractedFields={doc.extractedFields}
            page={1}
            pageWidth={imgSize.w}
            pageHeight={imgSize.h}
            focusedField={focusedField}
            onSelectField={onSelectField}
          />
        )}
        {rects.map(r => {
          const isFocused = focusedHl && focusedHl === r.id;
          const tintAlpha = r.isApprox ? 0.10 : 0.22;
          const fillRgb = r.color === "rose" ? "216,98,94" : "226,188,104";
          return (
            <React.Fragment key={r.id}>
              <div
                onClick={setFocusedHl ? () => setFocusedHl(r.id) : undefined}
                style={{
                  position: "absolute",
                  left: r.pct.left, top: r.pct.top, width: r.pct.width, height: r.pct.height,
                  background: `rgba(${fillRgb},${tintAlpha})`,
                  border: r.isApprox
                    ? `2px dashed var(--gold)`
                    : `2px solid var(--gold)`,
                  outline: isFocused ? "2px solid var(--gold2)" : "none",
                  outlineOffset: 2,
                  borderRadius: 3,
                  cursor: setFocusedHl ? "pointer" : "default",
                  mixBlendMode: r.isApprox ? "multiply" : undefined,
                  // Click-to-pulse for chat citations · plays once over the
                  // 2.4s the parent keeps focusedHl set, then auto-clears.
                  animation: r.isCitation ? "docaiq-cite-pulse 2.4s ease-in-out" : undefined,
                  pointerEvents: setFocusedHl ? "auto" : "none",
                }}
                title={r.text || (r.isApprox ? "Citation region (approximate)" : "Citation region")}
              />
              {!r.isApprox && r.pin != null && (
                <div
                  onClick={setFocusedHl ? () => setFocusedHl(r.id) : undefined}
                  style={{
                    position: "absolute",
                    left: `calc(${r.pct.left} - 22px)`, top: r.pct.top,
                    width: 20, height: 20, borderRadius: "50%",
                    background: "var(--gold)", color: "#1a1c22",
                    fontSize: 11, fontWeight: 700,
                    display: "grid", placeItems: "center",
                    border: "1px solid var(--gold2)",
                    cursor: setFocusedHl ? "pointer" : "default",
                    boxShadow: isFocused ? "0 0 0 3px rgba(200,160,76,.4)" : "none",
                  }}
                >{r.pin}</div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}


// ── CsvViewer · bank / CC / expense statement uploads ────────────────────
// PDF.js fails on CSV. We fetch raw CSV via the backend stream endpoint and
// render as a sticky-header table. Handles quoted fields with embedded
// commas, double-quote escape, and tab-separated as a fallback.

function CsvViewer({ doc, zoom = 100 }) {
  const src = documentFileUrl(doc.id);
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancel = false;
    fetch(src, { credentials: "same-origin" })
      .then(r => r.text())
      .then(text => { if (!cancel) setRows(parseCsv(text)); })
      .catch(e => { if (!cancel) setErr(e.message); });
    return () => { cancel = true; };
  }, [src]);

  if (err) return <ErrorState message={err}/>;
  if (rows == null) return <LoadingState label="Loading CSV…"/>;

  const header = rows[0] || [];
  const body = rows.slice(1);
  const scale = zoom / 100;

  return (
    <div style={{ padding: 16, fontSize: 12 * scale }}>
      <div className="row gap-2 mb-2" style={{ alignItems: "center" }}>
        <span className="upper ink3" style={{ fontSize: 10 }}>CSV</span>
        <span className="ink3">{body.length} row{body.length === 1 ? "" : "s"} · {header.length} columns</span>
      </div>
      <div className="bg2 border rounded-md" style={{ overflow: "auto", maxHeight: "calc(100vh - 260px)" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontFamily: "var(--mono)", fontSize: 11 }}>
          <thead style={{ position: "sticky", top: 0, background: "var(--bg1)", zIndex: 1 }}>
            <tr>
              {header.map((h, i) => (
                <th key={i} style={{
                  padding: "6px 10px", textAlign: "left",
                  fontSize: 10, textTransform: "uppercase", letterSpacing: 0.06,
                  color: "var(--ink3)", fontWeight: 700, whiteSpace: "nowrap",
                  borderBottom: "1px solid var(--line)",
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, r) => (
              <tr key={r} style={{ borderTop: r ? "1px solid var(--line)" : "none" }}>
                {header.map((_h, c) => (
                  <td key={c} style={{ padding: "5px 10px", whiteSpace: "nowrap", verticalAlign: "top" }}>
                    {row[c] ?? <span className="ink3">—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function parseCsv(text) {
  if (!text) return [];
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);  // strip BOM
  const firstLine = text.split(/\r?\n/, 1)[0] || "";
  const sep = (firstLine.split("\t").length > firstLine.split(",").length) ? "\t" : ",";

  const rows = [];
  let cur = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (ch === '"') { inQuotes = false; }
      else { field += ch; }
    } else {
      if (ch === '"') inQuotes = true;
      else if (ch === sep) { cur.push(field); field = ""; }
      else if (ch === "\n") { cur.push(field); rows.push(cur); cur = []; field = ""; }
      else if (ch === "\r") { /* swallow */ }
      else field += ch;
    }
  }
  if (field.length || cur.length) { cur.push(field); rows.push(cur); }
  while (rows.length && rows[rows.length - 1].every(c => !c)) rows.pop();
  return rows;
}
