// FieldOverlay · M40 Phase E
//
// Renders the document's extracted typed fields in two surfaces:
//
//   <FieldBoxes  doc … />     → colored rectangles drawn on top of the
//                               document image / page, one per field, color
//                               matched to the field type.
//   <FieldLegend doc … />     → right-rail panel listing each field as a row
//                               (icon · label · value · color chip), clickable
//                               to pulse the corresponding box on the doc.
//
// The data source is Document.extractedFields (Pydantic dump of
// app/agents/fact_extractor.py output), which carries:
//   * top-level typed fields  (holder_name, total, expiry_date, …)
//   * field_bboxes: { field_name → {page, x0, y0, x1, y1, page_w, page_h} }
//     populated by _locate_field_bboxes in the fact extractor (PyMuPDF
//     text-box hit on the extracted value's printed form).
//
// Field families covered today (per fact_extractor SCHEMAS):
//   * id_document       holder_name · dob · doc_no · nationality · expiry…
//   * invoice / receipt vendor · customer · line_items · subtotal · tax …
//   * bank_statement    account_holder · account_no_last_4 · period_start …
//   * policy_or_procedure  policy_title · owner · effective_date · scope …
//   * certificate       certificate_type · issuer · subject · scope · expiry
//   * agreement         parties · effective_date · expiry_date · jurisdiction
//
// Unknown field names fall back to a neutral gray pill with the raw name —
// the legend stays useful even when a new doc_type ships before we extend
// FIELD_META. The matcher's chunk citations are NOT rendered here; those are
// click-only and live in DocumentViewer's overlay path.

import React from "react";
import Icon from "./Icon.jsx";

// ── Field-meta map ────────────────────────────────────────────────────────
// Each entry: { label, color, icon }
//   color  → token from globals.css (gold/rose/emerald/amber/violet/blue)
//            OR an inline rgb tuple. Box uses the rgb at 0.30 alpha + 0.85
//            border; legend chip uses solid color.
//   icon   → name from Icon.jsx (file, calendar, user, clock, money, link…)
//   label  → human-readable, shown in legend (raw key shown in tooltip)
//
// The constant lives alongside the component because field names ARE the
// product vocabulary — moving it to a JSON file would just add lookups.

const COL = {
  red:     "216,98,94",     // identity/business names (rose family)
  amber:   "224,162,59",    // money / totals
  emerald: "63,164,122",    // phone / contact / signed / OK
  gold:    "200,160,76",    // standards / IDs / numbers
  violet:  "139,127,214",   // dates / periods
  blue:    "104,150,206",   // addresses / orgs / accounts
  rose:    "230,140,136",   // critical (expired, fail)
  neutral: "164,164,180",   // unknown / fallback
};

// Field-by-field metadata. Keys are extractor field names (snake_case).
// Order roughly by family for readability.
const FIELD_META = {
  // ── identity ────────────────────────────────────────────────────────
  holder_name:           { label: "Holder",          color: COL.red,     icon: "user" },
  sex:                   { label: "Sex",             color: COL.violet,  icon: "user" },
  date_of_birth:         { label: "Date of birth",   color: COL.violet,  icon: "calendar" },
  place_of_birth:        { label: "Place of birth",  color: COL.blue,    icon: "link" },
  nationality:           { label: "Nationality",     color: COL.blue,    icon: "flag" },
  document_number:       { label: "Document no.",    color: COL.gold,    icon: "hash" },
  national_id_number:    { label: "National ID",     color: COL.gold,    icon: "hash" },
  date_of_issue:         { label: "Issued",          color: COL.violet,  icon: "calendar" },
  date_of_expiry:        { label: "Expires",         color: COL.rose,    icon: "calendar" },
  issuing_authority:     { label: "Issuer",          color: COL.blue,    icon: "shield" },
  issuing_country:       { label: "Country",         color: COL.blue,    icon: "flag" },
  issuing_country_code:  { label: "Country code",    color: COL.blue,    icon: "flag" },
  doc_subtype:           { label: "ID type",         color: COL.gold,    icon: "file" },
  is_expired:            { label: "Expired?",        color: COL.rose,    icon: "alert" },
  mrz_line_1:            { label: "MRZ 1",           color: COL.neutral, icon: "code" },
  mrz_line_2:            { label: "MRZ 2",           color: COL.neutral, icon: "code" },

  // ── invoice / revenue / receipt ─────────────────────────────────────
  invoice_number:        { label: "Invoice no.",     color: COL.gold,    icon: "hash" },
  receipt_number:        { label: "Receipt no.",     color: COL.gold,    icon: "hash" },
  issue_date:            { label: "Issued",          color: COL.violet,  icon: "calendar" },
  due_date:              { label: "Due",             color: COL.violet,  icon: "calendar" },
  date:                  { label: "Date",            color: COL.violet,  icon: "calendar" },
  vendor:                { label: "Vendor",          color: COL.red,     icon: "user" },
  vendor_name:           { label: "Vendor",          color: COL.red,     icon: "user" },
  seller:                { label: "Seller",          color: COL.red,     icon: "user" },
  customer:              { label: "Customer",        color: COL.blue,    icon: "user" },
  customer_or_claimant:  { label: "Customer",        color: COL.blue,    icon: "user" },
  subtotal:              { label: "Subtotal",        color: COL.amber,   icon: "money" },
  tax:                   { label: "Tax",             color: COL.amber,   icon: "money" },
  tax_rate:              { label: "Tax rate",        color: COL.amber,   icon: "money" },
  total:                 { label: "Total",           color: COL.amber,   icon: "money" },
  currency:              { label: "Currency",        color: COL.gold,    icon: "money" },
  payment_terms:         { label: "Payment terms",   color: COL.gold,    icon: "clock" },
  payment_method:        { label: "Payment method",  color: COL.gold,    icon: "card" },
  status:                { label: "Status",          color: COL.emerald, icon: "check" },
  category:              { label: "Category",        color: COL.gold,    icon: "tag" },
  revenue_category:      { label: "Revenue cat.",    color: COL.gold,    icon: "tag" },

  // ── customer payment ────────────────────────────────────────────────
  payment_reference:     { label: "Payment ref.",    color: COL.gold,    icon: "hash" },
  payment_date:          { label: "Paid on",         color: COL.violet,  icon: "calendar" },
  amount:                { label: "Amount",          color: COL.amber,   icon: "money" },
  payer_name:            { label: "Payer",           color: COL.red,     icon: "user" },
  payer_account:         { label: "Payer acct.",     color: COL.blue,    icon: "card" },
  recipient_account:     { label: "Recipient acct.", color: COL.blue,    icon: "card" },
  method:                { label: "Method",          color: COL.gold,    icon: "card" },
  against_invoice_number:{ label: "Against invoice", color: COL.gold,    icon: "link" },
  memo:                  { label: "Memo",            color: COL.neutral, icon: "note" },

  // ── bank / statement ────────────────────────────────────────────────
  bank_or_org_name:      { label: "Bank",            color: COL.red,     icon: "user" },
  statement_kind:        { label: "Statement",       color: COL.gold,    icon: "file" },
  account_holder:        { label: "Holder",          color: COL.red,     icon: "user" },
  account_number_last_4: { label: "Account",         color: COL.blue,    icon: "card" },
  statement_period_start:{ label: "Period start",    color: COL.violet,  icon: "calendar" },
  statement_period_end:  { label: "Period end",      color: COL.violet,  icon: "calendar" },
  opening_balance:       { label: "Opening bal.",    color: COL.amber,   icon: "money" },
  closing_balance:       { label: "Closing bal.",    color: COL.amber,   icon: "money" },
  payment_due_date:      { label: "Payment due",     color: COL.rose,    icon: "calendar" },
  minimum_payment_due:   { label: "Min payment",     color: COL.amber,   icon: "money" },
  previous_balance:      { label: "Prev. bal.",      color: COL.amber,   icon: "money" },
  payments_received:     { label: "Payments in",     color: COL.emerald, icon: "money" },

  // ── policy / procedure ──────────────────────────────────────────────
  policy_title:          { label: "Policy",          color: COL.red,     icon: "file" },
  effective_date:        { label: "Effective",       color: COL.violet,  icon: "calendar" },
  last_reviewed_date:    { label: "Last reviewed",   color: COL.violet,  icon: "calendar" },
  next_review_date:      { label: "Next review",     color: COL.violet,  icon: "calendar" },
  owner:                 { label: "Owner",           color: COL.red,     icon: "user" },
  approver:              { label: "Approver",        color: COL.red,     icon: "user" },
  scope:                 { label: "Scope",           color: COL.violet,  icon: "note" },

  // ── certificate ─────────────────────────────────────────────────────
  certificate_type:      { label: "Type",            color: COL.gold,    icon: "shield" },
  subject_org:           { label: "Subject org.",    color: COL.red,     icon: "user" },
  certificate_number:    { label: "Cert no.",        color: COL.gold,    icon: "hash" },
  expiry_date:           { label: "Expires",         color: COL.rose,    icon: "calendar" },

  // ── agreement ───────────────────────────────────────────────────────
  agreement_type:        { label: "Agreement",       color: COL.gold,    icon: "file" },
  jurisdiction:          { label: "Jurisdiction",    color: COL.blue,    icon: "flag" },
  total_value:           { label: "Total value",     color: COL.amber,   icon: "money" },
  termination_clause_summary: { label: "Termination", color: COL.neutral, icon: "note" },
  term_description:      { label: "Term",            color: COL.violet,  icon: "clock" },
  is_signed:             { label: "Signed?",         color: COL.emerald, icon: "check" },
};

// Resolve a field name → { label, color, icon }, falling back to a neutral
// gray for unknown names so the legend still surfaces the value.
function metaFor(name) {
  const m = FIELD_META[name];
  if (m) return m;
  return { label: name, color: COL.neutral, icon: "tag" };
}

// Format the field value for display. Most are strings; objects + arrays are
// collapsed compactly so the legend row stays one-line where possible.
function formatValue(v) {
  if (v == null || v === "") return null;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    if (!v.length) return null;
    // line_items / signature_blocks → "N items" so the legend stays compact.
    return `${v.length} item${v.length === 1 ? "" : "s"}`;
  }
  if (typeof v === "object") {
    // { name, address } → name first.
    return v.name || v.description || JSON.stringify(v).slice(0, 60);
  }
  return String(v);
}

// Pull the list of (name, value, bbox, page) tuples from extractedFields.
//
// Real shape (per fact_extractor.py + DocumentChatPanel FactsCard):
//   extractedFields = {
//     fields:        { holder_name: "...", date_of_birth: "...", ... },
//     field_bboxes:  { holder_name: {page, x0, y0, x1, y1, page_w, page_h}, ... },
//     doc_type:      "id_document" | "invoice" | ...,
//     confidence:    0.92,
//     notes:         "...",
//   }
//
// Older shape (legacy seeded fixtures, kept for back-compat):
//   extractedFields = { holder_name: "...", field_bboxes: {...}, _doc_confidence: 0.9 }
//
// Both are handled — we prefer .fields when present, otherwise iterate the
// top-level minus meta keys.
function extractFieldRows(extractedFields) {
  if (!extractedFields || typeof extractedFields !== "object") return [];
  const bboxes = extractedFields.field_bboxes || {};
  // Prefer the canonical nested shape; fall back to top-level for legacy.
  const fields = extractedFields.fields && typeof extractedFields.fields === "object"
    ? extractedFields.fields
    : extractedFields;
  const META_KEYS = new Set([
    "fields", "field_bboxes", "doc_type", "confidence", "notes",
    "_doc_confidence", "_notes",
  ]);
  const rows = [];
  for (const [name, value] of Object.entries(fields)) {
    if (META_KEYS.has(name)) continue;
    if (name.startsWith("_")) continue;
    const formatted = formatValue(value);
    const bbox = bboxes[name] || null;
    if (formatted == null && !bbox) continue;
    rows.push({ name, value: formatted, bbox, meta: metaFor(name) });
  }
  return rows;
}


// ── FieldBoxes · overlay drawn on top of one page / image ─────────────────
//
// `pageWidth/pageHeight` are the rendered viewport dimensions of the page
// or image. `page` is 1-based and filters to fields on that page.
// `focusedField` (optional) is the field NAME being pulsed by a legend click.
//
// Accepts both bbox shapes (mirrors PdfDocumentViewer):
//   * { x0, y0, x1, y1, page_w, page_h }  ← from PyMuPDF (fact extractor)
//   * [ x0, y0, x1, y1 ]                  ← normalized 0..1 (matcher path)

export function FieldBoxes({ extractedFields, page = 1, pageWidth, pageHeight, focusedField, onSelectField, activeBlockIds = [], fieldBlockMap = {} }) {
  if (!extractedFields || !pageWidth || !pageHeight) return null;
  const rows = extractFieldRows(extractedFields).filter(r => r.bbox && (r.bbox.page === page || (!r.bbox.page && page === 1)));
  if (!rows.length) return null;
  return (
    <>
      {rows.map(r => {
        const bb = r.bbox;
        let left, top, w, h;
        if (Array.isArray(bb) && bb.length === 4) {
          left = bb[0] * pageWidth;
          top = bb[1] * pageHeight;
          w = (bb[2] - bb[0]) * pageWidth;
          h = (bb[3] - bb[1]) * pageHeight;
        } else if (bb.page_w && bb.page_h) {
          // Percentage-based shape from the line-map pipeline
          // PyMuPDF coords are top-left origin — no flip needed
          if (bb.y0_pct !== undefined) {
            left = (bb.x0_pct || 0) * pageWidth;
            top = bb.y0_pct * pageHeight;
            w = ((bb.x1_pct || 1) - (bb.x0_pct || 0)) * pageWidth;
            h = (bb.y1_pct - bb.y0_pct) * pageHeight;
          } else {
            // Legacy shape from PyMuPDF — top-left origin, scale directly
            const sx = pageWidth / bb.page_w;
            const sy = pageHeight / bb.page_h;
            left = bb.x0 * sx;
            top = bb.y0 * sy;
            w = (bb.x1 - bb.x0) * sx;
            h = (bb.y1 - bb.y0) * sy;
          }
        } else {
          return null;
        }
        const isPulsed = focusedField === r.name;
        const linkedBlockIds = fieldBlockMap[r.name] || [];
        const isFieldActive = linkedBlockIds.some(bid => activeBlockIds.includes(bid));
        const rgb = r.meta.color;
        const label = (r.meta.label || r.name || "").replace(/_/g, " ");
        const shortVal = String(r.value || "").slice(0, 30);
        return (
          <div
            key={r.name}
            onClick={onSelectField ? () => onSelectField(r.name) : undefined}
            title={`${label} · ${r.value || "(empty)"}`}
            style={{
              position: "absolute",
              left, top, width: w, height: Math.max(h, 18),
              background: isFieldActive ? "rgba(124,111,214,0.25)" : `rgba(${rgb},0.20)`,
              border: isFieldActive ? "3px solid rgba(124,111,214,0.90)" : `2px solid rgba(${rgb},0.85)`,
              borderRadius: 2,
              cursor: onSelectField ? "pointer" : "default",
              pointerEvents: onSelectField ? "auto" : "none",
              animation: isPulsed ? "docaiq-field-pulse 1.2s ease-out 2" : (isFieldActive ? "docaiq-block-field-pulse 1.5s ease-in-out 2" : "none"),
              boxShadow: isFieldActive ? "0 0 0 3px rgba(124,111,214,0.4)" : (isPulsed ? `0 0 0 3px rgba(${rgb},0.4)` : `0 0 0 1px rgba(${rgb},0.15)`),
              zIndex: isFieldActive ? 6 : (isPulsed ? 5 : 1),
            }}>
            {/* Label badge — shown on the bbox */}
            <span style={{
              position: "absolute", top: -9, left: 2,
              background: `rgba(${rgb},0.90)`,
              color: "#fff", fontSize: 8, fontWeight: 600,
              padding: "1px 5px", borderRadius: 2,
              whiteSpace: "nowrap", maxWidth: w - 4, overflow: "hidden",
              textOverflow: "ellipsis", lineHeight: 1.4,
            }}>{label} {shortVal ? `· ${shortVal}` : ""}</span>
          </div>
        );
      })}
    </>
  );
}


// ── FieldLegend · right rail of typed fields beside the doc ───────────────
//
// One row per field (icon · color chip · label · value). Clicking a row
// fires onSelectField(name) — the parent flips focusedField, the matching
// FieldBoxes overlay pulses for ~2.4s.
//
// Compact mode (mobile / narrow Review screen) collapses each row to icon +
// short value. Today we always render full mode; the prop is reserved for
// future use.

export function FieldLegend({ doc, focusedField, onSelectField }) {
  const extractedFields = doc?.extractedFields;
  const rows = extractFieldRows(extractedFields);
  if (!rows.length) {
    return (
      <aside className="bg1 border-l flex col" style={{ width: "100%", padding: 12, fontSize: 12 }}>
        <div className="upper ink3" style={{ fontSize: 10, letterSpacing: 0.6, marginBottom: 8 }}>
          Extracted fields
        </div>
        <div className="ink3 text-sm">
          No fields extracted yet. They appear here after the document is
          processed by the fact extractor.
        </div>
      </aside>
    );
  }
  return (
    <aside className="bg1 border-l flex col" style={{ width: "100%", padding: 12, fontSize: 12, overflow: "auto" }}>
      <div className="row between" style={{ marginBottom: 10 }}>
        <div className="upper ink3" style={{ fontSize: 10, letterSpacing: 0.6 }}>
          Extracted fields
        </div>
        <div className="mono ink3" style={{ fontSize: 10 }}>{rows.length}</div>
      </div>
      <div className="flex col gap-1">
        {rows.map(r => {
          const isPulsed = focusedField === r.name;
          const rgb = r.meta.color;
          return (
            <button
              key={r.name}
              type="button"
              onClick={() => onSelectField?.(r.name)}
              title={r.bbox ? `Click to highlight on document · raw key: ${r.name}` : `No coords located · raw key: ${r.name}`}
              className="row gap-2 border bg2 hover-bg"
              style={{
                padding: "6px 8px",
                borderRadius: 5,
                textAlign: "left",
                alignItems: "flex-start",
                color: "var(--ink2)",
                cursor: r.bbox ? "pointer" : "default",
                opacity: r.bbox ? 1 : 0.7,
                outline: isPulsed ? `2px solid rgba(${rgb},0.8)` : "none",
                outlineOffset: 1,
              }}
            >
              {/* color chip */}
              <span style={{
                flexShrink: 0,
                width: 10, height: 10, borderRadius: 2,
                marginTop: 4,
                background: `rgba(${rgb},0.85)`,
                border: `1px solid rgba(${rgb},1)`,
              }}/>
              <div style={{ minWidth: 0, flexGrow: 1 }}>
                <div className="row gap-1" style={{ alignItems: "center" }}>
                  <Icon name={r.meta.icon} size={11}/>
                  <div className="upper ink3 truncate" style={{ fontSize: 9, letterSpacing: 0.5, flexGrow: 1 }}>
                    {r.meta.label}
                  </div>
                  {!r.bbox && (
                    <span className="mono ink3" style={{ fontSize: 8 }} title="No bbox located — value extracted but not pinned to a region on the page">⚐</span>
                  )}
                </div>
                <div className="mono truncate" style={{ fontSize: 11, color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {r.value || <span className="ink3">—</span>}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

// Convenience: count of fields with bbox coords — used by DocumentViewer to
// decide whether to mount the legend column (avoid empty-rail padding).
export function fieldCount(doc) {
  return extractFieldRows(doc?.extractedFields).length;
}
