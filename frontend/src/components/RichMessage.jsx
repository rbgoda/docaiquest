// M46 · RichMessage — renders a chat answer's lightweight Markdown so the model's
// tables / lists / bold show as real tables, bullets, and emphasis instead of raw
// pipes and asterisks. (Benchmarked against xpenseaiq-v5's chat output.)
//
// Inline Markdown is HTML-escaped FIRST, then **bold** / `code` are applied, so
// dangerouslySetInnerHTML is safe for untrusted LLM text.
import React from "react";

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s) {
  let h = escapeHtml(s);
  h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/`([^`]+)`/g,
    '<code style="font-family:var(--font-mono,monospace);background:var(--bg3);padding:1px 4px;border-radius:3px;font-size:.92em">$1</code>');
  return h;
}

export default function RichMessage({ content }) {
  const lines = String(content ?? "").split("\n");
  const els = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // ── Fenced code block (```lang … ```) ───────────────────────────
    if (line.trim().startsWith("```")) {
      i++;
      const code = [];
      while (i < lines.length && !lines[i].trim().startsWith("```")) { code.push(lines[i]); i++; }
      if (i < lines.length) i++;  // consume closing fence
      els.push(
        <pre key={`pre${els.length}`} style={{
          margin: "6px 0", padding: "8px 10px", background: "var(--bg3,#1a1a1a)",
          borderRadius: 6, overflowX: "auto", fontSize: 11,
          fontFamily: "var(--font-mono,monospace)", whiteSpace: "pre",
        }}>{code.join("\n")}</pre>,
      );
      continue;
    }

    // ── Markdown table ──────────────────────────────────────────────
    if (line.trim().startsWith("|")) {
      const tl = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { tl.push(lines[i]); i++; }
      const rows = tl.filter((l) => !/^\s*\|[\s\-:|]+\|\s*$/.test(l));  // drop the |---| separator
      if (rows.length > 1) {
        const parse = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const headers = parse(rows[0]);
        els.push(
          <div key={`t${els.length}`} style={{ overflowX: "auto", margin: "6px 0" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11.5 }}>
              <thead><tr>{headers.map((h, hi) => (
                <th key={hi} style={{ padding: "5px 10px", background: "var(--bg2)", fontWeight: 600, textAlign: "left", borderBottom: "2px solid var(--gold2)", whiteSpace: "nowrap" }}
                  dangerouslySetInnerHTML={{ __html: inlineMd(h) }} />
              ))}</tr></thead>
              <tbody>{rows.slice(1).map((row, ri) => (
                <tr key={ri} style={{ background: ri % 2 ? "var(--bg2)" : "transparent" }}>
                  {parse(row).map((c, ci) => (
                    <td key={ci} style={{ padding: "4px 10px", color: "var(--ink2)", borderBottom: "1px solid var(--line)" }}
                      dangerouslySetInnerHTML={{ __html: inlineMd(c) }} />
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>,
        );
        continue;
      }
    }

    // ── Heading ─────────────────────────────────────────────────────
    if (/^#{1,4}\s/.test(line)) {
      els.push(<div key={`h${els.length}`} style={{ fontWeight: 600, fontSize: 13, margin: "6px 0 2px" }}
        dangerouslySetInnerHTML={{ __html: inlineMd(line.replace(/^#{1,4}\s/, "")) }} />);
      i++; continue;
    }

    // ── Bullet / numbered list ──────────────────────────────────────
    if (/^(\s*[\-\*•]|\s*\d+\.)\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^(\s*[\-\*•]|\s*\d+\.)\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([\-\*•]|\d+\.)\s+/, "")); i++;
      }
      els.push(<ul key={`ul${els.length}`} style={{ margin: "4px 0 6px 16px", padding: 0 }}>
        {items.map((x, xi) => (
          <li key={xi} style={{ fontSize: 12.5, lineHeight: 1.55, marginBottom: 2 }}
            dangerouslySetInnerHTML={{ __html: inlineMd(x) }} />
        ))}
      </ul>);
      continue;
    }

    // ── Blank line / paragraph ──────────────────────────────────────
    if (!line.trim()) { els.push(<div key={`br${els.length}`} style={{ height: 5 }} />); i++; continue; }
    els.push(<p key={`p${els.length}`} style={{ margin: "2px 0", fontSize: 13, lineHeight: 1.6 }}
      dangerouslySetInnerHTML={{ __html: inlineMd(line) }} />);
    i++;
  }
  return <div>{els}</div>;
}
