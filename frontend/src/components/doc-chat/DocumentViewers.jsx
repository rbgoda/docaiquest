// Extracted from DocumentChatPanel.jsx — per-type document viewers (markdown / csv / image).
import React, { useState, useEffect, useRef } from "react";
import Icon from "../Icon.jsx";
import RichMessage from "../RichMessage.jsx";
import { LoadingState, ErrorState } from "../Shell.jsx";
import { FieldBoxes } from "../FieldOverlay.jsx";
import { documentFileUrl, exportFullMarkdown } from "../../api";

function MarkdownDocViewer({ doc, zoom = 100 }) {
  const [md, setMd] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let cancel = false;
    setMd(null); setErr(null);
    exportFullMarkdown(doc.id)
      .then(res => { if (!cancel) setMd((res && (res.body ?? res.markdown)) || ""); })
      .catch(e => { if (!cancel) setErr(e?.message || "Couldn't load document content"); });
    return () => { cancel = true; };
  }, [doc.id]);

  if (err) return <ErrorState message={err} />;
  if (md == null) return <LoadingState label="Rendering document…" />;
  const scale = zoom / 100;
  return (
    <div className="bg overflow-auto flex col grow min0">
      <div className="bg1 border-b row between p-3" style={{ position: "sticky", top: 0, zIndex: 1 }}>
        <div className="row gap-2">
          <Icon name="file" size={14} />
          <span className="font-medium text-sm truncate">{doc.name}</span>
          <span className="mono ink3 text-xs">· {doc.size}</span>
        </div>
        <span className="upper ink3 text-xs" title="Rendered from extracted text — original is an office document">
          text view
        </span>
      </div>
      <div style={{ padding: 20, maxWidth: 820, margin: "0 auto", fontSize: 14 * scale, lineHeight: 1.55 }}>
        {md.trim()
          ? <RichMessage content={md} />
          : <div className="ink3">No extractable text in this document.</div>}
      </div>
    </div>
  );
}


// ── CSV viewer for bank/CC statement uploads ─────────────────────────────
// PDF.js fails on CSV the same way it failed on JPEG ("Invalid PDF
// structure"). We fetch the raw CSV text from the existing /file stream
// endpoint, do a tolerant comma/tab parse, and render as a real <table>.
// No external dep — handles double-quoted fields and embedded commas.
function CsvDocumentViewer({ doc, zoom = 100 }) {
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
    <div style={{ padding: 16, fontSize: 12 * scale, transformOrigin: "top left" }}>
      <div className="row gap-2 mb-2" style={{ alignItems: "center" }}>
        <span className="upper ink3" style={{ fontSize: 10 }}>CSV</span>
        <span className="ink3">{body.length} row{body.length === 1 ? "" : "s"} · {header.length} columns</span>
      </div>
      <div className="bg2 border rounded-md" style={{ overflow: "auto", maxHeight: "calc(100vh - 240px)" }}>
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

// Minimal CSV parser — handles double-quoted fields, embedded commas, and
// "" escape inside quotes. Detects tab-separated as a fallback. No external
// dep. Sufficient for typical bank/CC exports.
function parseCsv(text) {
  if (!text) return [];
  // Strip BOM if present.
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  // Detect delimiter — prefer tabs if the first line has more tabs than commas
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
  // Drop trailing all-empty rows
  while (rows.length && rows[rows.length - 1].every(c => !c)) rows.pop();
  return rows;
}


// ── Image viewer for image/* uploads ──────────────────────────────────────
// PdfDocumentViewer can't render images — it pipes everything through PDF.js
// which fails on a JPEG with "Invalid PDF structure". For image MIMEs we
// render the file via the same backend stream endpoint as an <img>, with the
// active citations (if any) drawn as a translucent yellow rect overlaid on
// top in normalized coords. Since OCR'd images don't carry per-field bboxes
// (no PDF coordinate system), `highlights` is usually empty here and we
// just show the raw image.
function ImageDocumentViewer({ doc, highlights, zoom = 100 }) {
  const src = documentFileUrl(doc.id);
  const focused = highlights && highlights[0];
  const scale = zoom / 100;
  // M40 · capture rendered image size so FieldBoxes can project bboxes back
  // onto pixel space. Re-measure on zoom change because the image scales
  // with its container.
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 });
  const imgRef = useRef(null);
  useEffect(() => {
    if (!imgRef.current) return;
    const el = imgRef.current;
    const measure = () => setImgSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // The focused citation comes from FactsCard click on a typed field —
  // shaped as { bbox: {page, x0, y0, x1, y1}, page }. When bbox is present,
  // draw a tight gold rect at the actual coords instead of covering the
  // whole image.
  const focusedRect = (() => {
    if (!focused || !focused.bbox || !imgSize.w) return null;
    const bb = focused.bbox;
    if (bb.page_w && bb.page_h) {
      const sx = imgSize.w / bb.page_w;
      const sy = imgSize.h / bb.page_h;
      return {
        left: bb.x0 * sx, top: bb.y0 * sy,
        width: (bb.x1 - bb.x0) * sx, height: (bb.y1 - bb.y0) * sy,
      };
    }
    // Normalized 0..1 bbox (rare on FactsCard path).
    if (Array.isArray(bb) && bb.length === 4) {
      return {
        left: bb[0] * imgSize.w, top: bb[1] * imgSize.h,
        width: (bb[2] - bb[0]) * imgSize.w, height: (bb[3] - bb[1]) * imgSize.h,
      };
    }
    return null;
  })();

  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
      <div style={{
        position: "relative",
        transform: `scale(${scale})`,
        transformOrigin: "top center",
        transition: "transform 80ms ease-out",
      }}>
        <img
          ref={imgRef}
          src={src}
          alt={doc.name}
          onLoad={(e) => setImgSize({ w: e.target.clientWidth, h: e.target.clientHeight })}
          style={{
            display: "block",
            maxWidth: "100%", height: "auto", borderRadius: 4,
            boxShadow: "0 2px 8px rgba(0,0,0,0.25)", background: "#1a1a1a",
          }}
        />
        {/* M40 · per-field typed boxes (always on, color = field family).
            Reads extractedFields.fields + .field_bboxes — populated by the
            fact extractor at ingest time. Empty when the extractor didn't
            run or didn't locate bbox coords (vision-OCR docs typically). */}
        {imgSize.w > 0 && (
          <FieldBoxes
            extractedFields={doc.extractedFields}
            page={1}
            pageWidth={imgSize.w}
            pageHeight={imgSize.h}
          />
        )}
        {/* Focused-field citation rect — pushed in by FactsCard click. */}
        {focusedRect && (
          <div
            style={{
              position: "absolute",
              left: focusedRect.left, top: focusedRect.top,
              width: focusedRect.width, height: focusedRect.height,
              border: "2px solid var(--gold)",
              borderRadius: 3,
              background: "rgba(226,188,104,0.18)",
              pointerEvents: "none",
              animation: "docaiq-cite-pulse 2.4s ease-in-out",
            }}
            title={focused.text || focused.quote || "Citation region"}
          />
        )}
      </div>
    </div>
  );
}


// ── Tiny in-process Markdown renderer ───────────────────────────────────────
// Covers the subset the export prompt actually produces: headings (#-####),
// bullet + numbered lists (with nesting), bold (**), italic (*), inline code
// (`code`), fenced code blocks (```), horizontal rules (---), and tables
// (| col | col |). No remote fetches, no html injection — every span is
// rendered as React children, so the user's content is automatically escaped.

export { MarkdownDocViewer, CsvDocumentViewer, ImageDocumentViewer };
