import React, { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
// Vite serves the worker from node_modules with the ?url import, no copy step.
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import Icon from "./Icon.jsx";
import { FieldBoxes } from "./FieldOverlay.jsx";
import { documentFileUrl } from "../api";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

/**
 * Renders an uploaded PDF document from the backend's streaming /file endpoint.
 * Citation pins on the right rail call `onPageJump` → the viewer scrolls and
 * tries to highlight the cited text. Text-search is used because today's
 * highlight data has no bbox coordinates; M8 (entity extraction) will populate
 * `bbox` and this component will switch to coord-based overlays.
 */
export default function PdfDocumentViewer({ doc, highlights = [], focusedHl, setFocusedHl, zoom = 100, focusedField, onSelectField, annotateMode = false, annotations = [], onCreateAnnotation, selectedAnnId, onSelectAnn, hideHeader = false, onPageClick, activeBlockIds = [], fieldBlockMap = {} }) {
  const containerRef = useRef(null);
  const [pdf, setPdf] = useState(null);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);   // bump to retry a failed re-pull

  // M46 · the original may have been freed from the server but still live in the
  // user's Drive. The backend's /file endpoint re-pulls it on demand, so the
  // viewer should still try to load (and tell the user it's fetching).
  const isRepullable = doc?.source === "drive" && !!doc?.sourceRef;
  const fetchingFromDrive = !doc?.hasFile && isRepullable;

  // Load the PDF once per doc id.
  useEffect(() => {
    if (!doc?.id || (!doc?.hasFile && !isRepullable)) return;
    let cancelled = false;
    setPdf(null);
    setError(null);
    const task = pdfjs.getDocument({ url: documentFileUrl(doc.id), withCredentials: true });
    task.promise.then(
      (d) => { if (!cancelled) setPdf(d); },
      (err) => { if (!cancelled) setError(err.message || "Failed to load PDF"); },
    );
    return () => {
      cancelled = true;
      task.destroy?.();
    };
  }, [doc?.id, doc?.hasFile, isRepullable, attempt]);

  // When a citation pin is clicked, scroll the matching page into view.
  useEffect(() => {
    if (!focusedHl || !containerRef.current) return;
    const hl = highlights.find((h) => h.id === focusedHl);
    if (!hl) return;
    const target = containerRef.current.querySelector(`[data-page="${hl.page}"]`);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusedHl, highlights]);

  if (error) {
    return (
      <aside className="bg overflow-auto flex col grow min0">
        <div className="p-5 ink3 flex col gap-3" style={{ alignItems: "flex-start" }}>
          <div><Icon name="alert" size={18}/> {fetchingFromDrive ? "Couldn't fetch the original from Drive" : "Failed to load PDF"}: <span className="mono">{error}</span></div>
          {isRepullable && (
            <button onClick={() => { setError(null); setAttempt(a => a + 1); }} className="border bg2 row gap-2"
              style={{ alignItems: "center", padding: "6px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer" }}>
              <Icon name="download" size={13}/> Pull from Drive again
            </button>
          )}
        </div>
      </aside>
    );
  }

  if (!doc?.hasFile && !isRepullable) {
    return (
      <aside className="bg overflow-auto flex col grow min0">
        <div className="p-5 ink3">No file attached to this document.</div>
      </aside>
    );
  }

  return (
    <aside className="bg overflow-auto flex col grow min0" ref={containerRef}>
      {/* Title strip — hidden in the simple stacked view (filename is in the doc
          list; page count + size already surface in the merged top box). */}
      {!hideHeader && (
      <div className="bg1 border-b row between p-3" style={{ position: "sticky", top: 0, zIndex: 1 }}>
        <div className="row gap-2">
          <Icon name="file" size={14}/>
          <span className="font-medium text-sm truncate">{doc.name}</span>
          <span className="mono ink3 text-xs">· {doc.size}</span>
        </div>
        <div className="row gap-2 text-xs ink3">
          {pdf ? <span>{pdf.numPages} page{pdf.numPages === 1 ? "" : "s"}</span> : "Loading…"}
        </div>
      </div>
      )}

      {!pdf ? (
        <div className="p-5 ink3 row gap-2" style={{ alignItems: "center" }}>
          {fetchingFromDrive && <Icon name="cloud" size={14} style={{ color: "#8B7FD6" }}/>}
          {fetchingFromDrive ? "Fetching the original from your Google Drive…" : "Loading PDF…"}
        </div>
      ) : (
        Array.from({ length: pdf.numPages }, (_, i) => (
          <PdfPage
            key={i + 1}
            pdf={pdf}
            pageNumber={i + 1}
            zoom={zoom}
            highlights={highlights.filter((h) => h.page === i + 1)}
            focusedHl={focusedHl}
            onPinClick={setFocusedHl}
            extractedFields={doc?.extractedFields}
            focusedField={focusedField}
            onSelectField={onSelectField}
            annotateMode={annotateMode}
            annotations={annotations.filter((a) => a.page === i + 1)}
            onCreateAnnotation={onCreateAnnotation}
            selectedAnnId={selectedAnnId}
            onSelectAnn={onSelectAnn}
            activeBlockIds={activeBlockIds}
            fieldBlockMap={fieldBlockMap}
          />
        ))
      )}
    </aside>
  );
}

function PdfPage({ pdf, pageNumber, zoom, highlights, focusedHl, onPinClick, extractedFields, focusedField, onSelectField, annotateMode = false, annotations = [], onCreateAnnotation, selectedAnnId, onSelectAnn, activeBlockIds = [], fieldBlockMap = {} }) {
  const canvasRef = useRef(null);
  const textLayerRef = useRef(null);
  const [textItems, setTextItems] = useState([]);
  const [pageSize, setPageSize] = useState({ w: 0, h: 0 });
  // M53 · drag-to-draw highlight. `drag` holds the in-progress rect in px.
  const [drag, setDrag] = useState(null);
  const _pos = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const _down = (e) => { const p = _pos(e); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }); };
  const _move = (e) => { if (!drag) return; const p = _pos(e); setDrag((d) => ({ ...d, x1: p.x, y1: p.y })); };
  const _up = () => {
    if (!drag) return;
    const { x0, y0, x1, y1 } = drag;
    setDrag(null);
    if (Math.abs(x1 - x0) < 6 || Math.abs(y1 - y0) < 6 || !pageSize.w) return;  // ignore stray clicks
    onCreateAnnotation?.(pageNumber, [
      Math.min(x0, x1) / pageSize.w, Math.min(y0, y1) / pageSize.h,
      Math.max(x0, x1) / pageSize.w, Math.max(y0, y1) / pageSize.h,
    ]);
  };

  useEffect(() => {
    let cancelled = false;
    pdf.getPage(pageNumber).then(async (page) => {
      if (cancelled) return;
      const scale = (zoom / 100) * 1.4;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const native = page.getViewport({ scale: 1 });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setPageSize({ w: viewport.width, h: viewport.height, pw: native.width, ph: native.height });

      await page.render({ canvasContext: ctx, viewport }).promise;
      if (cancelled) return;

      const content = await page.getTextContent();
      // Stash for text-search highlights. Coords are in viewport space.
      const items = content.items.map((it) => {
        const tx = pdfjs.Util.transform(viewport.transform, it.transform);
        return {
          str: it.str,
          x: tx[4],
          y: tx[5] - it.height * scale,
          w: it.width * scale,
          h: it.height * scale,
        };
      });
      if (!cancelled) setTextItems(items);
    });
    return () => { cancelled = true; };
  }, [pdf, pageNumber, zoom]);

  // M40 Phase E · citation rendering policy. When the typed field overlay
  // is also rendering on this page, chat-citation gold boxes become noise.
  // Hide them by default; render only when the reviewer clicks a [chunk-N]
  // pin in the chat (focusedHl === hl.id), with the cite-pulse animation
  // applied via the existing setFocusedHl(null) timer in Review.jsx.
  // Seeded / non-citation highlights stay always-on (back-compat).
  // Only render highlights that belong to THIS page (bbox page or hl.page).
  const visibleHighlights = highlights.filter(h => {
    if (h.page && h.page !== pageNumber) return false;  // wrong page — skip
    return !h.isCitation || focusedHl === h.id;
  });

  // Compute overlay rects per highlight. Two paths:
  //
  //  1. Coord path (preferred) — when `hl.bbox` is present, project the
  //     stored coords into viewport space directly. Stable on scanned /
  //     rotated PDFs where text-search misses or guesses badly.
  //     Accepts two shapes:
  //       * list  [x0, y0, x1, y1]                   — normalized 0..1
  //       * dict  {x0, y0, x1, y1, page_w, page_h}   — page coordinate space
  //
  //  2. Text-search fallback — original M2 heuristic, used when bbox is
  //     NULL (seeded JSX demo docs, vision-OCR vision path that can't
  //     ground exact coords). Unions text-item boxes containing any word
  //     from `hl.text`.
  const overlays = visibleHighlights.map((hl) => {
    // ── 1. coord path ───────────────────────────────────────────────────
    const bb = hl.bbox;
    if (Array.isArray(bb) && bb.length === 4 && pageSize.w && pageSize.h) {
      const [nx0, ny0, nx1, ny1] = bb;
      return {
        id: hl.id, pin: hl.pin, color: hl.color,
        x: nx0 * pageSize.w, y: ny0 * pageSize.h,
        w: (nx1 - nx0) * pageSize.w, h: (ny1 - ny0) * pageSize.h,
        precise: true,
        isCitation: !!hl.isCitation,
      };
    }
    if (bb && typeof bb === "object" && bb.page_w && bb.page_h && pageSize.w && pageSize.h) {
      // Percentage-based shape from the line-map pipeline
      // PyMuPDF coords are top-left origin — no flip needed
      if (bb.y0_pct !== undefined) {
        return {
          id: hl.id, pin: hl.pin, color: hl.color,
          x: (bb.x0_pct || 0) * pageSize.w,
          y: bb.y0_pct * pageSize.h,
          w: ((bb.x1_pct || 1) - (bb.x0_pct || 0)) * pageSize.w,
          h: (bb.y1_pct - bb.y0_pct) * pageSize.h,
          precise: true,
          isCitation: !!hl.isCitation,
        };
      }
      // Legacy shape from PyMuPDF search_for / _locate_text_span
      // PyMuPDF uses top-left origin — scale directly, no flip
      const sx = pageSize.w / bb.page_w;
      const sy = pageSize.h / bb.page_h;
      return {
        id: hl.id, pin: hl.pin, color: hl.color,
        x: bb.x0 * sx,
        y: bb.y0 * sy,
        w: (bb.x1 - bb.x0) * sx,
        h: (bb.y1 - bb.y0) * sy,
        precise: true,
        isCitation: !!hl.isCitation,
      };
    }
    // ── 2. text-search fallback ────────────────────────────────────────
    const needles = (hl.text || "").toLowerCase().split(/\s+/).filter((w) => w.length > 3);
    if (!needles.length || !textItems.length) return null;
    const matches = textItems.filter((it) => {
      const s = it.str.toLowerCase();
      return needles.some((n) => s.includes(n));
    });
    if (!matches.length) return null;
    console.warn(
      "[DocAIQ bbox] text-search fallback used",
      { hlId: hl.id, pin: hl.pin, page: pageNumber, needles: needles.slice(0, 5), matchCount: matches.length },
      "— bbox not in line_map/block_map; highlight may be imprecise",
    );
    const x0 = Math.min(...matches.map((m) => m.x));
    const y0 = Math.min(...matches.map((m) => m.y));
    const x1 = Math.max(...matches.map((m) => m.x + m.w));
    const y1 = Math.max(...matches.map((m) => m.y + m.h));
    return { id: hl.id, pin: hl.pin, color: hl.color, x: x0, y: y0, w: x1 - x0, h: y1 - y0, precise: false, isCitation: !!hl.isCitation };
  }).filter(Boolean);

  return (
    <div data-page={pageNumber} style={{ padding: 16, display: "flex", justifyContent: "center" }}>
      <div style={{ position: "relative", width: pageSize.w, boxShadow: "0 4px 18px rgba(0,0,0,.25)" }}
        onClick={(e) => {
          if (!onPageClick || !pageSize.w) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const cx = e.clientX - rect.left;  // click pos in canvas pixels
          const cy = e.clientY - rect.top;
          const scale = pageSize.w / (pageSize.pw || pageSize.w);  // canvas / PDF coords
          const px = cx / scale;  // convert to PDF page coords
          const py = cy / scale;
          onPageClick(pageNumber, px, py);
        }}>
        <canvas ref={canvasRef}/>
        {/* M40 · per-field typed boxes (always on, color = field family).
            Render BEFORE the text layer so citations land on top when the
            reviewer pulses them.  Must live OUTSIDE the pointerEvents:none
            text-layer container so field boxes remain clickable. */}
        {pageSize.w > 0 && extractedFields && (
          <FieldBoxes
            extractedFields={extractedFields}
            page={pageNumber}
            pageWidth={pageSize.w}
            pageHeight={pageSize.h}
            focusedField={focusedField}
            onSelectField={onSelectField}
            activeBlockIds={activeBlockIds}
            fieldBlockMap={fieldBlockMap}
          />
        )}
        <div ref={textLayerRef} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
          {overlays.map((o) => (
            <div key={o.id}
                 onClick={() => onPinClick(o.id)}
                 title={o.precise ? undefined : "Approximate citation (text-search)"}
                 style={{
                   position: "absolute",
                   left: o.x, top: o.y, width: o.w, height: o.h,
                   background: o.color === "rose" ? "rgba(216,98,94,0.22)" : "rgba(226,188,104,0.35)",
                   border: o.precise
                     ? "2px solid rgba(200,160,76,.90)"
                     : "1.5px dashed rgba(200,160,76,.70)",
                   boxShadow: o.precise ? "0 0 0 1px rgba(200,160,76,0.25)" : "none",
                   outline: focusedHl === o.id ? "2px solid var(--gold2)" : "none",
                   outlineOffset: 1,
                   cursor: "pointer",
                   pointerEvents: "auto",
                   // Click-to-pulse for chat citations · plays during the
                   // 2.4s window the parent keeps focusedHl set.
                   animation: o.isCitation ? "docaiq-cite-pulse 2.4s ease-in-out" : undefined,
                 }}/>
          ))}
          {overlays.map((o) => (
            <div key={`pin-${o.id}`}
                 onClick={() => onPinClick(o.id)}
                 style={{
                   position: "absolute",
                   left: o.x - 22, top: o.y - 4,
                   width: 20, height: 20, borderRadius: "50%",
                   background: "var(--gold)", color: "#1a1c22",
                   fontSize: 11, fontWeight: 700,
                   display: "grid", placeItems: "center",
                   border: "1px solid var(--gold2)",
                   cursor: "pointer",
                   pointerEvents: "auto",
                   boxShadow: focusedHl === o.id ? "0 0 0 3px rgba(200,160,76,.4)" : "none",
                 }}>{o.pin}</div>
          ))}
          {/* M53 · user highlights (yellow). Click to select (when not drawing). */}
          {pageSize.w > 0 && annotations.map((a) => {
            const [nx0, ny0, nx1, ny1] = a.bbox || [0, 0, 0, 0];
            return (
              <div key={`ann-${a.id}`}
                   onClick={() => !annotateMode && onSelectAnn?.(a.id)}
                   title={a.note || a.text || "highlight"}
                   style={{
                     position: "absolute",
                     left: nx0 * pageSize.w, top: ny0 * pageSize.h,
                     width: (nx1 - nx0) * pageSize.w, height: (ny1 - ny0) * pageSize.h,
                     background: "rgba(245,210,70,0.28)",
                     border: selectedAnnId === a.id ? "2px solid #e0a23b" : "2px solid rgba(224,162,59,0.7)",
                     borderRadius: 3,
                     cursor: annotateMode ? "crosshair" : "pointer",
                     pointerEvents: annotateMode ? "none" : "auto",
                   }}/>
            );
          })}
          {/* M53 · drag-to-draw capture layer — only in annotate mode. */}
          {annotateMode && (
            <div onMouseDown={_down} onMouseMove={_move} onMouseUp={_up} onMouseLeave={_up}
                 style={{ position: "absolute", inset: 0, cursor: "crosshair", zIndex: 5 }}>
              {drag && (
                <div style={{
                  position: "absolute",
                  left: Math.min(drag.x0, drag.x1), top: Math.min(drag.y0, drag.y1),
                  width: Math.abs(drag.x1 - drag.x0), height: Math.abs(drag.y1 - drag.y0),
                  background: "rgba(245,210,70,0.25)", border: "2px dashed #e0a23b",
                }}/>
              )}
            </div>
          )}
        </div>
        <div style={{ position: "absolute", left: 6, bottom: 4, fontSize: 10, color: "rgba(0,0,0,.4)", fontFamily: "Georgia, serif" }}>
          page {pageNumber}
        </div>
      </div>
    </div>
  );
}
