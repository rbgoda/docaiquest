// Extracted from DocumentChatPanel.jsx — a tiny, dependency-free Markdown renderer for previews.
import React, { useMemo } from "react";
import { safeUrl } from "../../safeUrl.js";

function MiniMarkdown({ source }) {
  const blocks = useMemo(() => parseMarkdown(source || ""), [source]);
  return <>{blocks.map((b, i) => renderBlock(b, i))}</>;
}

function parseMarkdown(src) {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (/^```/.test(line)) {
      const lang = line.replace(/^```/, "").trim();
      const body = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // consume closing fence
      blocks.push({ kind: "code", lang, text: body.join("\n") });
      continue;
    }

    // Heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ kind: "heading", level: h[1].length, text: h[2] });
      i += 1;
      continue;
    }

    // Horizontal rule
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      blocks.push({ kind: "hr" });
      i += 1;
      continue;
    }

    // Blockquote — one or more leading-'>' lines (nestable via '>>').
    if (/^\s*>/.test(line)) {
      const ql = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        ql.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      blocks.push({ kind: "quote", text: ql.join("\n") });
      continue;
    }

    // Table — at least one ' | ' line + a separator row
    if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const headerCells = splitTableRow(line);
      i += 2; // skip header + separator
      const rows = [];
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push({ kind: "table", header: headerCells, rows });
      continue;
    }

    // List (ul / ol, nestable, with GFM task items "- [ ] / - [x]")
    if (/^(\s*)([-*+]|\d+\.)\s+/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const cur = lines[i];
        const u = /^(\s*)[-*+]\s+(.*)$/.exec(cur);
        const o = /^(\s*)(\d+)\.\s+(.*)$/.exec(cur);
        if (!u && !o) break;
        const indent = (u ? u[1] : o[1]).length;
        let text = u ? u[2] : o[3];
        let checked = null;  // null = not a task item
        const task = /^\[([ xX])\]\s+(.*)$/.exec(text);
        if (task) { checked = task[1].toLowerCase() === "x"; text = task[2]; }
        items.push({ indent, text, ordered: !!o, checked });
        i += 1;
      }
      blocks.push({ kind: "list", items });
      continue;
    }

    // Blank line — separator
    if (line.trim() === "") {
      i += 1;
      continue;
    }

    // Paragraph (greedy until blank line / heading / list / fence / quote).
    // Keep the individual lines so hard line breaks are preserved (<br/>).
    const para = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() !== ""
      && !/^#{1,6}\s/.test(lines[i])
      && !/^```/.test(lines[i])
      && !/^\s*>/.test(lines[i])
      && !/^(\s*)[-*+]\s+/.test(lines[i])
      && !/^(\s*)\d+\.\s+/.test(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: "para", lines: para });
  }
  return blocks;
}

function splitTableRow(line) {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(s => s.trim());
}

function renderBlock(b, idx) {
  switch (b.kind) {
    case "heading": {
      const Tag = `h${Math.min(b.level, 6)}`;
      const sizes = { 1: 22, 2: 18, 3: 15, 4: 13, 5: 12, 6: 12 };
      return React.createElement(Tag, {
        key: idx,
        style: {
          fontFamily: "var(--serif, 'Fraunces', serif)",
          fontSize: sizes[b.level] || 13,
          margin: "14px 0 6px 0",
          fontWeight: b.level <= 2 ? 600 : 500,
        },
      }, renderInline(b.text));
    }
    case "hr":
      return <hr key={idx} style={{ border: 0, borderTop: "1px solid var(--line)", margin: "12px 0" }} />;
    case "code":
      return (
        <pre key={idx} className="bg1 border p-2" style={{
          fontFamily: "var(--mono)", fontSize: 11, borderRadius: 4,
          margin: "8px 0", overflow: "auto", whiteSpace: "pre",
        }}>{b.text}</pre>
      );
    case "list":
      return <React.Fragment key={idx}>{renderListNodes(nestListItems(b.items), `l${idx}`)}</React.Fragment>;
    case "table":
      return (
        <table key={idx} className="border" style={{
          borderCollapse: "collapse", width: "100%", margin: "8px 0", fontSize: 12,
        }}>
          <thead>
            <tr style={{ background: "var(--bg1)" }}>
              {b.header.map((h, j) => (
                <th key={j} className="border" style={{ padding: "4px 8px", textAlign: "left" }}>{renderInline(h)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {b.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className="border" style={{ padding: "4px 8px" }}>{renderInline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "quote":
      return (
        <blockquote key={idx} style={{
          margin: "8px 0", paddingLeft: 12, borderLeft: "3px solid var(--line)",
          color: "var(--ink2)", fontStyle: "italic",
        }}>{(b.text || "").split("\n").map((ln, j) => (
          <div key={j}>{renderInline(ln)}</div>
        ))}</blockquote>
      );
    case "para":
    default: {
      const ln = b.lines || [b.text || ""];
      return (
        <p key={idx} style={{ margin: "4px 0" }}>
          {ln.map((l, j) => (
            <React.Fragment key={j}>{j > 0 && <br/>}{renderInline(l)}</React.Fragment>
          ))}
        </p>
      );
    }
  }
}

// Build a nested tree from flat list items by their leading indent.
function nestListItems(items) {
  const root = { children: [] };
  const stack = [{ indent: -1, node: root }];
  for (const it of items) {
    while (stack.length > 1 && it.indent <= stack[stack.length - 1].indent) stack.pop();
    const parent = stack[stack.length - 1].node;
    const node = { ...it, children: [] };
    parent.children.push(node);
    stack.push({ indent: it.indent, node });
  }
  return root.children;
}

function renderListNodes(nodes, keyPrefix) {
  if (!nodes || !nodes.length) return null;
  const ordered = nodes[0].ordered;
  const Tag = ordered ? "ol" : "ul";
  const isTask = nodes.some((n) => n.checked !== null && n.checked !== undefined);
  return React.createElement(Tag, {
    style: { paddingLeft: isTask ? 18 : 22, margin: "4px 0", listStyle: isTask ? "none" : undefined },
  }, nodes.map((n, j) => (
    <li key={`${keyPrefix}-${j}`} style={{ margin: "2px 0" }}>
      {n.checked !== null && n.checked !== undefined && (
        <input type="checkbox" checked={n.checked} readOnly
          style={{ marginRight: 6, accentColor: "var(--gold2)", verticalAlign: "middle" }} />
      )}
      {renderInline(n.text)}
      {n.children.length ? renderListNodes(n.children, `${keyPrefix}-${j}`) : null}
    </li>
  )));
}

// Inline span parser: **bold** / __bold__, *italic* / _italic_, ~~strike~~,
// `code`, ![alt](src) images, and [text](url) links. Returns React nodes, all
// content rendered as children (no dangerouslySetInnerHTML → auto-escaped).
// Underscore emphasis is boundary-guarded so snake_case / IDs aren't italicised.
function renderInline(text) {
  if (!text) return null;
  const out = [];
  // Order matters: code (most literal) → image (before link, shares [..](..)) →
  // link → strong (** / __) → strike → em (* / _). Single pass, no nesting.
  const rx = new RegExp([
    "(`([^`]+)`)",                              // 1,2  code
    "(!\\[([^\\]]*)\\]\\(([^)]+)\\))",          // 3,4,5 image
    "(\\[([^\\]]+)\\]\\(([^)]+)\\))",           // 6,7,8 link
    "(\\*\\*([^*]+)\\*\\*)",                    // 9,10  **strong**
    "((?<![A-Za-z0-9])__([^_]+)__(?![A-Za-z0-9]))", // 11,12 __strong__
    "(~~([^~]+)~~)",                            // 13,14 ~~strike~~
    "(\\*([^*]+)\\*)",                          // 15,16 *em*
    "((?<![A-Za-z0-9])_([^_]+)_(?![A-Za-z0-9]))",   // 17,18 _em_
  ].join("|"), "g");
  let lastEnd = 0, key = 0, m;
  while ((m = rx.exec(text))) {
    if (m.index > lastEnd) out.push(text.slice(lastEnd, m.index));
    if (m[1]) {
      out.push(<code key={`c${key++}`} className="mono bg1" style={{ padding: "0 4px", borderRadius: 3, fontSize: "0.9em" }}>{m[2]}</code>);
    } else if (m[3]) {
      out.push(<img key={`g${key++}`} src={m[5]} alt={m[4] || ""} style={{ maxWidth: "100%", borderRadius: 4, margin: "4px 0" }} />);
    } else if (m[6]) {
      out.push(<a key={`l${key++}`} href={safeUrl(m[8])} target="_blank" rel="noopener noreferrer" style={{ color: "var(--gold)" }}>{m[7]}</a>);
    } else if (m[9]) {
      out.push(<strong key={`b${key++}`}>{m[10]}</strong>);
    } else if (m[11]) {
      out.push(<strong key={`b${key++}`}>{m[12]}</strong>);
    } else if (m[13]) {
      out.push(<del key={`s${key++}`}>{m[14]}</del>);
    } else if (m[15]) {
      out.push(<em key={`i${key++}`}>{m[16]}</em>);
    } else if (m[17]) {
      out.push(<em key={`i${key++}`}>{m[18]}</em>);
    }
    lastEnd = m.index + m[0].length;
  }
  if (lastEnd < text.length) out.push(text.slice(lastEnd));
  return out;
}


// ── JSON tab ──────────────────────────────────────────────────────────────

// Same remount-safe cache pattern as MarkdownTab — survives tab switches so
// switching JSON → Chat → JSON doesn't re-fire the on-demand extractor LLM
// call (which would also hit the OpenRouter rate limit on free tier).
// Bust via `invalidateJsonCache(docId)` after Re-extract so the next view
// reflects the fresh extraction.
// (JSON_CACHE moved to JsonTab.jsx — its only user — after the module split.)

export default MiniMarkdown;
