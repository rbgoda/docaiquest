// CopyJsonButton — copies raw extracted JSON to clipboard.
// Extracted from views/DocumentChatPanel.jsx (refactoring Phase 2a).

import { useState } from "react";

export default function CopyJsonButton({ data }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(JSON.stringify(data ?? {}, null, 2));
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch { /* clipboard blocked — no-op */ }
      }}
      title="Copy the raw extracted-fields JSON (for export)"
      className="border bg1 hover-bg"
      style={{ padding: "4px 10px", borderRadius: 4, fontSize: 11, cursor: "pointer" }}>
      {copied ? "✓ Copied" : "⬇ JSON"}
    </button>
  );
}
