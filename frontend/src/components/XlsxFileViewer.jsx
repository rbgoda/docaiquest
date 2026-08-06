// M51 · spreadsheet viewer for .xlsx uploads. PDF.js can't render a workbook
// ("Invalid PDF structure"); the backend parses it (openpyxl) and returns JSON
// sheets, which we render as tables with a tab per sheet. Shared by the Review
// DocumentViewer and the doc ChatPanel.
import React, { useEffect, useState } from "react";
import { LoadingState, ErrorState } from "./Shell.jsx";
import { fetchDocumentSheets } from "../api";

export default function XlsxFileViewer({ doc, zoom = 100 }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    let cancel = false;
    fetchDocumentSheets(doc.id)
      .then((d) => { if (!cancel) setData(d); })
      .catch((e) => { if (!cancel) setErr(e.message); });
    return () => { cancel = true; };
  }, [doc.id]);

  if (err) return <ErrorState message={err}/>;
  if (!data) return <LoadingState label="Loading spreadsheet…"/>;
  const sheets = data.sheets || [];
  if (sheets.length === 0) return <ErrorState message="No sheets in this workbook."/>;
  const sheet = sheets[Math.min(active, sheets.length - 1)];
  const rows = sheet.rows || [];
  const scale = zoom / 100;

  return (
    <div style={{ padding: 16 }}>
      {sheets.length > 1 && (
        <div className="row gap-1 mb-2" style={{ flexWrap: "wrap" }}>
          {sheets.map((s, i) => (
            <button key={i} onClick={() => setActive(i)}
              className={i === active ? "btn-gold" : "border bg2 ink2"}
              style={{ fontSize: 11, padding: "3px 10px", borderRadius: 8, cursor: "pointer" }}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="bg2 border rounded-md" style={{ overflow: "auto", maxHeight: "calc(100vh - 250px)" }}>
        <table style={{ borderCollapse: "collapse", fontFamily: "var(--font-mono, monospace)", fontSize: 11 * scale }}>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} style={{ background: ri === 0 ? "var(--bg1)" : (ri % 2 ? "var(--bg2)" : "transparent") }}>
                {r.map((c, ci) => (
                  <td key={ci} style={{
                    padding: "4px 10px", border: "1px solid var(--line)", whiteSpace: "nowrap",
                    fontWeight: ri === 0 ? 600 : 400, color: ri === 0 ? "var(--ink)" : "var(--ink2)",
                  }}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sheet.truncated && (
        <div className="ink3 mt-2" style={{ fontSize: 11 }}>
          Large sheet — showing the first 500 rows × 30 columns.
        </div>
      )}
    </div>
  );
}
