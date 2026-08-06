// Typed API surface. One function per backend endpoint.
// View code should never call `fetch` directly — always go through here.

import { del, get, patch, post, put } from "./client";

// ---------- Documents ----------------------------------------------------
export const fetchDocuments = (scope, opts) => get(scope ? `/documents?scope=${scope}` : "/documents", opts);
// M46 · supported upload formats (drives the accept filter + the hint)
export const fetchSupportedTypes = (opts) => get("/documents/supported-types", opts);
export const fetchDocument = (id, opts) => get(`/documents/${id}`, opts);
// .xlsx workbook as JSON sheets (PDF.js can't render spreadsheets).
export const fetchDocumentSheets = (id, opts) => get(`/documents/${id}/sheets`, opts);
// Browser-direct URL for the streamed file. Same-origin so the cookie rides.
export const documentFileUrl = (id) => `/api/documents/${id}/file`;
export const deleteDocument = (id, opts) => del(`/documents/${id}`, opts);
// M53 · user annotations / highlights (draw boxes → captured text + notes).
export const listAnnotations = (docId, opts) => get(`/documents/${docId}/annotations`, opts);
export const createAnnotation = (docId, body, opts) => post(`/documents/${docId}/annotations`, body, opts);
export const patchAnnotation = (docId, annId, body, opts) => patch(`/documents/${docId}/annotations/${annId}`, body, opts);
export const deleteAnnotation = (docId, annId, opts) => del(`/documents/${docId}/annotations/${annId}`, opts);
export const exportAnnotationsMarkdown = (docId, opts) => get(`/documents/${docId}/annotations/markdown`, opts);
// Google Drive Picker import — bring in pre-existing Drive files (drive.file scope).
export const fetchDrivePickerConfig = (opts) => get("/connectors/drive/picker-config", opts);
export const importDriveFile = (fileId, opts) => post("/connectors/drive/import", { fileId }, opts);
// M29 · soft-archive endpoints. Use when DELETE returns 409 because the
// doc is referenced by a closed audit, or when you just want to declutter
// the list without losing history.
export const archiveDocument = (id, opts) => post(`/documents/${id}/archive`, undefined, opts);
// M29.2 · per-doc actions surfaced from the AllDocuments table when
// "Reqs matched = 0". Both admin+reviewer.
export const rematchDocument = (id, opts) => post(`/documents/${id}/rematch`, undefined, opts);
export const attachDocumentToRequirement = (docId, requirementId, opts) =>
  post(`/documents/${docId}/attach`, { requirementId }, opts);
// Re-run classifier + fact-extractor on an already-ingested doc. Returns the
// fresh Document with updated docType / docTypeConfidence / extractedFields.
export const reclassifyDocument = (id, opts) =>
  post(`/documents/${id}/reclassify`, {}, opts);
// Phase 3: re-run extraction with the strong model → {model, reanalyzed, document}.
export const reanalyzeDocument = (id, opts) =>
  post(`/documents/${id}/reanalyze`, {}, opts);
// HITL: manually override the document TYPE (classifier can be wrong). Records
// a field_edits audit row. Returns the updated Document.
export const setDocumentType = (id, docType, reason, opts) =>
  patch(`/documents/${id}/type`, { docType, reason }, opts);
// The classifier's known doc_type enum — for the Type editor autocomplete.
export const fetchDocTypes = (opts) => get("/documents/_meta/doc-types", opts);
// HITL: override a single field on a document's extracted_fields.
// field_path examples: "fields.total", "fields.vendor.name",
// "fields.top_transactions.0.category", "fields.line_items.3.amount".
export const editDocumentField = (id, payload, opts) =>
  patch(`/documents/${id}/fields`, payload, opts);
// Region → field: draw a box, name it → a new field whose value is the text under
// the box. payload: { label, page, bbox:[x0,y0,x1,y1] normalized }.
export const addFieldFromRegion = (id, payload, opts) =>
  post(`/documents/${id}/fields/from-region`, payload, opts);
// Add a field by typing its name + value directly (no box): { label, value }.
export const addField = (id, payload, opts) =>
  post(`/documents/${id}/fields/add`, payload, opts);
// Delete a top-level extracted field by key.
export const deleteField = (id, key, opts) =>
  del(`/documents/${id}/fields/${encodeURIComponent(key)}`, opts);
// Invoice line item from a box → appends {description, amount, currency} to line_items.
export const addLineItemFromRegion = (id, payload, opts) =>
  post(`/documents/${id}/line-items/from-region`, payload, opts);
export const fetchRecallGaps = (id, opts) =>
  get(`/documents/${id}/recall-gaps`, opts);
// Audit trail of every manual edit on this document.
export const fetchEditHistory = (id, opts) =>
  get(`/documents/${id}/edit-history`, opts);

// ---------- Custom categories (M28.5) -------------------------------------
// Merged list = canonical + global custom + (when vendorPk given) vendor-local.
export const fetchCategories = ({ mode, vendorPk } = {}, opts) => {
  const qs = new URLSearchParams({ mode });
  if (vendorPk != null) qs.set("vendor_pk", String(vendorPk));
  return get(`/categories?${qs.toString()}`, opts);
};
// Add a category. Reviewers can only create scope='vendor'.
// Admin/Owner can create either scope.
export const createCategory = (payload, opts) =>
  post("/categories", payload, opts);
export const fetchGraphDuplicates = (vendorPk, opts) =>
  get(`/graph/reconcile/duplicates${vendorPk != null ? `?vendor_pk=${vendorPk}` : ""}`, opts);
export const fetchGraphPayments = (vendorPk, opts) =>
  get(`/graph/reconcile/payments${vendorPk != null ? `?vendor_pk=${vendorPk}` : ""}`, opts);
// Per-document entity graph — nodes + edges for the force-directed graph tab.
export const fetchDocGraph = (docId, opts) =>
  get(`/graph/document/${docId}`, opts);
// Cross-document ego-network centered on a resolved entity identity.
export const fetchIdentityGraph = (query, depth = 2, opts) =>
  get(`/graph/identity-graph?q=${encodeURIComponent(query)}&depth=${depth}&direction=both`, opts);
// Dismiss a single duplicate finding as a false positive. Deletes only the
// duplicate_of edge; both receipts stay.
export const dismissDuplicate = (relationPk, opts) =>
  del(`/graph/reconcile/duplicates/${relationPk}`, opts);
// Reviewer sign-off — single doc + bulk
export const reviewDocument = (id, payload, opts) =>
  post(`/documents/${id}/review`, payload, opts);
export async function uploadDocument(file, { signal, requirementId, vendorPk } = {}) {
  const form = new FormData();
  form.append("file", file);
  if (requirementId) form.append("requirement_id", requirementId);
  if (vendorPk != null) form.append("vendor_pk", String(vendorPk));
  const res = await fetch("/api/documents", {
    method: "POST",
    body: form,
    credentials: "same-origin",
    signal,
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch {}
    throw new Error(`Upload failed: ${res.status}${detail ? ` · ${detail}` : ""}`);
  }
  return res.json();
}

// ---------- Requirements -------------------------------------------------
// Requirements are joined with the per-audit-run verdict when auditRunId is
// supplied; without it, verdicts come back null.
export const fetchRequirements = (auditRunId, opts) => {
  const qs = auditRunId ? `?audit_run_id=${encodeURIComponent(auditRunId)}` : "";
  return get(`/requirements${qs}`, opts);
};
export const fetchRequirement = (id, auditRunId, opts) => {
  const qs = auditRunId ? `?audit_run_id=${encodeURIComponent(auditRunId)}` : "";
  return get(`/requirements/${id}${qs}`, opts);
};
// `body` MUST include `auditRunId` (verdicts are per-(run, requirement)).
export const setRequirementVerdict = (id, body, opts) =>
  patch(`/requirements/${id}/verdict`, body, opts);

// Single-requirement add (admin-only). Mirrors a single CSV row.
// Body: { id, title, group?, subtitle?, status? }
export const createRequirement = (body, opts) => post("/requirements", body, opts);

// Replace the acceptable-evidence list for a requirement (admin-only).
// body = { requiredDocs: [string, ...] } — empty list clears.
export const setRequirementRequiredDocs = (id, body, opts) =>
  patch(`/requirements/${id}/required-docs`, body, opts);

// Set or clear the matcher-prompt override (admin-only).
// body = { matchPrompt: "..." }  — empty string resets to default template.
export const setRequirementMatchPrompt = (id, body, opts) =>
  patch(`/requirements/${id}/match-prompt`, body, opts);

// ---------- Auth ---------------------------------------------------------
export const fetchAuthConfig = (opts) => get("/auth/config", opts);
export const fetchMe = (opts) => get("/me", opts);

// TODO #32 — self-service profile update (name only; email + roles are guarded).
export const updateMe = (body, opts) => patch("/me", body, opts);
export const loginWithPassword = (body, opts) => post("/auth/login", body, opts);
export const logout = (opts) => post("/auth/logout", {}, opts);
// Public "Contact us" form → emails the inquiry to the team.
export const submitContact = (body, opts) => post("/contact", body, opts);
// ---------- Doc-chat (M11.7) ---------------------------------------------
// Chat with a single document: summary + Q&A + citations + markdown/JSON exports.
export const fetchDocChat = (docId, opts) => get(`/documents/${docId}/chat`, opts);
export const generateDocSummary = (docId, opts) =>
  post(`/documents/${docId}/summary`, {}, opts);
export const postDocChatMessage = (docId, text, opts) =>
  post(`/documents/${docId}/chat/messages`, { text }, opts);
// Deterministic whole-document Markdown from the parsed text — all users, no page cap,
// no LLM (instant DB read). Returns {docId, format, body}, same shape as exportDocMarkdown.
export const exportFullMarkdown = (docId, opts = {}) => {
  const { force, raw, language, ...rest } = opts;
  const params = [];
  if (force) params.push("force=1");
  if (raw) params.push("raw=1");
  if (language) params.push(`language=${encodeURIComponent(language)}`);
  const qs = params.length ? `?${params.join("&")}` : "";
  return get(`/documents/${docId}/markdown/full${qs}`, rest);
};
// Linked tab: near-duplicate copies + graph-related documents.
export const fetchRelatedDocuments = (docId) => get(`/documents/${docId}/related`);
// JSON tab: extracted values rendered in the approved schema's shape + conformance.
export const fetchSchemaJson = (docId) => get(`/documents/${docId}/schema-json`);
// JSON tab: deterministic extraction-coverage audit (salient page values captured vs missed).
export const fetchCoverage = (docId) => get(`/documents/${docId}/coverage`);
// Chunks tab: inspect/edit/enable-disable the retrieval chunks.
export const fetchDocChunks = (docId) => get(`/documents/${docId}/chunks`);
export const patchDocChunk = (docId, chunkPk, body) => patch(`/documents/${docId}/chunks/${chunkPk}`, body);
// Editable full-text: save a human-corrected Markdown override, or reset to the build.
export const saveMarkdownOverride = (docId, markdown, opts = {}) => {
  const { reprocess, changedBlockIds, ...rest } = opts;
  const qs = reprocess ? "?reprocess=true" : "";
  const body = { markdown };
  if (changedBlockIds && changedBlockIds.length > 0) {
    body.changed_block_ids = changedBlockIds;
  }
  return put(`/documents/${docId}/markdown${qs}`, body, rest);
};
export const resetMarkdownOverride = (docId, opts) =>
  del(`/documents/${docId}/markdown/override`, opts);
export const exportDocJson = (docId, opts) =>
  post(`/documents/${docId}/json`, {}, opts);

// ── Translation & Export (M54) ──────────────────────────────────────────────
// Translate a document's markdown to a target language (preserves block markers).
// Returns {docId, language, body, annotatedBody, translatedAt, model, cached}.
export const translateDocument = (docId, targetLanguage, opts) =>
  post(`/documents/${docId}/translate`, { target_language: targetLanguage }, opts);

// Fetch all available translations for a document.
// Returns {docId, translations: {fr: {translated_at, model, status}, ...}}.
export const fetchDocumentTranslations = (docId, opts) =>
  get(`/documents/${docId}/translations`, opts);

// Export a single document's extracted fields as JSON or CSV.
// For JSON returns {docId, name, docType, fields: {...}}.
// For CSV returns a StreamingResponse (binary) — use downloadDocumentExport helper.
export const exportDocumentFields = (docId, format, opts = {}) => {
  const qs = `?format=${encodeURIComponent(format)}`;
  return get(`/documents/${docId}/export${qs}`, opts);
};

// Download a document export as a file (handles binary CSV response).
export const downloadDocumentExport = async (docId, format, filename) => {
  const res = await fetch(`/api/documents/${encodeURIComponent(docId)}/export?format=${encodeURIComponent(format)}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `${docId}-fields.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

// M44.P11.2 · PII-at-rest reveal/hide. Owner/admin/reviewer only (enforced
// server-side + audited). Returns {docId, piiProtected, piiRevealed}.
export const revealDocPii = (docId, opts) =>
  post(`/documents/${docId}/pii/reveal`, {}, opts);
export const hideDocPii = (docId, opts) =>
  post(`/documents/${docId}/pii/hide`, {}, opts);

// Note: the markdown/json endpoints pass opts through to the fetch wrapper,
// so AbortController.signal flows correctly — used by the chat panel for a
// 60s frontend timeout when the free-tier provider stalls.
// documentFileUrl is already exported above (line ~37) — used by the
// PDF viewer to stream the file.

// ---------- Workspace chat (M44.P12) -------------------------------------
// "Ask across all documents" — cross-document Q&A scoped to a vendor's doc
// set (the Documents tab). Pass vendorPk to scope; omit for tenant-wide.
const _wsQs = (vendorPk, conv) => {
  const p = [];
  if (vendorPk != null) p.push(`vendor_pk=${vendorPk}`);
  if (conv) p.push(`conv=${encodeURIComponent(conv)}`);
  return p.length ? `?${p.join("&")}` : "";
};
export const fetchWorkspaceChat = (vendorPk, conv, opts) =>
  get(`/workspace-chat${_wsQs(vendorPk, conv)}`, opts);
export const postWorkspaceChatMessage = (vendorPk, text, docIds, conv, opts) =>
  post(`/workspace-chat/messages`, { text, vendorPk: vendorPk ?? null, docIds: docIds?.length ? docIds : null, conv: conv ?? null }, opts);
// The signed-in user's saved cross-document conversations (newest first) — the history picker.
export const listWorkspaceThreads = (vendorPk, opts) =>
  get(`/workspace-chat/threads${vendorPk != null ? `?vendor_pk=${vendorPk}` : ""}`, opts);
// Delete ONE cross-document conversation — owner-scoped; wipes only the
// signed-in user's conversation, never their documents.
export const clearWorkspaceChat = (vendorPk, conv, opts) =>
  del(`/workspace-chat${_wsQs(vendorPk, conv)}`, opts);
