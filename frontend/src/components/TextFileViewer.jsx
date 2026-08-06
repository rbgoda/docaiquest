// M51 · shared plain-text file viewer for .txt / .md / .eml / text/* uploads.
// PDF.js can't render plain text ("Invalid PDF structure"); stream the raw text
// from the backend /file endpoint and show it monospaced on cream paper. Used by
// both the Review DocumentViewer and the doc ChatPanel so they can't drift.
import React, { useEffect, useState } from "react";
import { LoadingState, ErrorState } from "./Shell.jsx";
import { documentFileUrl } from "../api";

const MAX = 200000;  // cap the rendered <pre> so a huge log doesn't lock the tab

export default function TextFileViewer({ doc, zoom = 100, showHeader = false }) {
  const src = documentFileUrl(doc.id);
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancel = false;
    fetch(src, { credentials: "same-origin" })
      .then(r => r.text())
      .then(t => { if (!cancel) setText(t); })
      .catch(e => { if (!cancel) setErr(e.message); });
    return () => { cancel = true; };
  }, [src]);

  if (err) return <ErrorState message={err}/>;
  if (text == null) return <LoadingState label="Loading document…"/>;

  const scale = zoom / 100;
  const truncated = text.length > MAX;
  const shown = truncated ? text.slice(0, MAX) : text;
  return (
    <div style={{ padding: 16 }}>
      {(showHeader || truncated) && (
        <div className="row gap-2 mb-2" style={{ alignItems: "center" }}>
          {showHeader && <span className="upper ink3" style={{ fontSize: 10 }}>TEXT</span>}
          {showHeader && <span className="ink3" style={{ fontSize: 11 }}>{doc.name}</span>}
          {truncated && <span className="ink3" style={{ fontSize: 10 }}>· first {Math.round(MAX / 1000)} KB</span>}
        </div>
      )}
      <pre style={{
        margin: 0, padding: "16px 18px", background: "#F4EFE6", color: "#2a2a2a",
        borderRadius: 6, border: "1px solid var(--line)", overflow: "auto",
        maxHeight: "calc(100vh - 240px)", whiteSpace: "pre-wrap", wordBreak: "break-word",
        fontFamily: "var(--mono, monospace)", fontSize: 12.5 * scale, lineHeight: 1.6,
      }}>{shown}</pre>
    </div>
  );
}
