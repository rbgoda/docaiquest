// M46 · Documents System API surface — kept OUT of the shared src/api/index.js
// so the documents product's endpoints don't touch the central api module.
// All of these are documents-product only (the backend 404s/403s them on the
// auditing product).
import { del, get, patch, post } from "./client";

// ---------- Dashboard ----------------------------------------------------
export const fetchDocumentsDashboard = (opts) => get("/documents-dashboard", opts);

// ---------- Assistant · watchlist (renewals / expiries / due dates) -------
export const fetchWatchlist = (opts) => get("/assistant/watchlist", opts);

// ---------- Alerts (unified Assistant + Intelligence) ---------------------
export const fetchAlerts = (opts) => get("/alerts/unified", opts);
export const fetchAlertRules = (opts) => get("/alerts/rules", opts);
export const createAlertRule = (body, opts) => post("/alerts/rules", body, opts);
export const deleteAlertRule = (id, opts) => del(`/alerts/rules/${id}`, opts);

// ---------- Dashboard (user-customizable infographic widgets) -------------
export const fetchDashboardConfig = (opts) => get("/dashboard/config", opts);
export const saveDashboardConfig = (config, opts) => put("/dashboard/config", config, opts);
export const previewDashboardWidget = (spec, opts) => post("/dashboard/widgets/preview", spec, opts);
export const proposeDashboardWidgets = (opts) => post("/dashboard/widgets/propose", {}, opts);

// ---------- Analytics · on-demand dashboards (doc-type driven) ------------
export const fetchAnalyticsDashboards = (opts) => get("/analytics/dashboards", opts);
export const buildAnalyticsDashboard = (theme, docIds, months = 0, opts) =>
  post(`/analytics/dashboards/${theme}`, { docIds, months }, opts);
export const fetchAnalyticsInsights = (theme, docIds, months = 0, opts) =>
  post(`/analytics/dashboards/${theme}/insights`, { docIds, months }, opts);

// ---------- Developer / API keys -----------------------------------------
export const listApiKeys = (opts) => get("/keys", opts);
export const createApiKey = (name, opts) => post("/keys", { name }, opts);
export const revokeApiKey = (id, opts) => del(`/keys/${id}`, opts);

// ---------- Intelligence Dashboard ----------------------------------------
// Phase A: portfolio header + zero-LLM attention alerts.
export const fetchIntelligenceOverview = (opts) => get("/intelligence/overview", opts);
// Phase B: built-in views evaluated over the user's own documents.
export const fetchIntelligenceViews = (opts) => get("/intelligence/views", opts);
// Phase C: AI assembles custom views from the (values-free) corpus profile.
export const proposeIntelligenceViews = (opts) => post("/intelligence/propose", {}, opts);
export const updateIntelligenceView = (key, body, opts) => patch(`/intelligence/views/${key}`, body, opts);

// ---------- Chat feedback (thumbs + free text → improvement loop) ---------
export const submitChatFeedback = (body, opts) => post("/chat-feedback", body, opts);
export const fetchChatFeedback = (opts) => get("/chat-feedback", opts);

// ---------- App-level product feedback (the "Send feedback" screen) -------
export const submitFeedback = (body, opts) => post("/feedback", body, opts);
// Redeem a promo code → upgrade the caller's plan for the code's duration.
export const redeemPromo = (code, opts) => post("/me/redeem-promo", { code }, opts);
export const fetchMyFeedback = (opts) => get("/feedback/mine", opts);

// ---------- Self-registration --------------------------------------------
export const register = (body, opts) => post("/auth/register", body, opts);

// ---------- Google Drive connector ---------------------------------------
export const fetchDriveStatus = (opts) => get("/connectors/drive", opts);
export const connectDrive = (opts) => post("/connectors/drive/connect", {}, opts);
export const disconnectDrive = (opts) => del("/connectors/drive", opts);
export const fetchDriveFolders = (opts) => get("/connectors/drive/folders", opts);
export const syncDriveFolder = (body, opts) => post("/connectors/drive/sync", body, opts);
// M46 · dedicated docaiq_docs inbox folder
export const fetchDriveInbox = (opts) => get("/connectors/drive/inbox", opts);
export const syncDriveInbox = (body, opts) => post("/connectors/drive/sync-inbox", body || {}, opts);
// M46 · copy direct uploads → Drive, purge server copies, free space
export const backupUploadsToDrive = (opts) => post("/connectors/drive/backup-uploads", {}, opts);
// M46 · self-learning classification · re-type 'other' docs from their AI summary
export const reclassifyOther = (opts) => post("/documents/reclassify-other", {}, opts);
// M46 · §2 · the user's self-learned doc-type vocabulary
export const fetchLearnedTypes = (opts) => get("/documents/learned-types", opts);
// M46 · §5 · per-user encrypted workspace in your own Drive
export const fetchWorkspaceStatus = (opts) => get("/documents/workspace/status", opts);
export const syncWorkspace = (opts) => post("/documents/workspace/sync", {}, opts);
// M46 · B7 · per-user "encrypt my Drive files" toggle
export const setDriveEncryption = (enabled, opts) => post("/connectors/drive/encryption", { enabled }, opts);
// M47 · §5 · disaster recovery — detect + restore the Drive workspace snapshot
export const fetchRestoreStatus = (opts) => get("/connectors/drive/restore/status", opts);
export const runRestore = (syncOriginals = true, password = null, opts) =>
  post("/connectors/drive/restore", { syncOriginals, ...(password ? { password } : {}) }, opts);
// Optional password encryption of the Drive backup (user-owned key).
export const setBackupEncryption = (enabled, password, opts) =>
  post("/connectors/drive/backup-encryption", { enabled, ...(password ? { password } : {}) }, opts);
export const unlockBackup = (password, opts) => post("/connectors/drive/backup-unlock", { password }, opts);
// M48 · email verification — resend the confirmation link to the signed-in user
export const resendVerification = (opts) => post("/auth/resend-verification", {}, opts);
// M46 · A5 · semantic search across the user's docs
export const searchDocuments = (q, opts) => get(`/documents/search?q=${encodeURIComponent(q)}`, opts);
// M46 · A4 · bulk actions over selected docs
export const bulkDocuments = (action, docIds, groupId, opts) => post("/documents/bulk", { action, docIds, groupId }, opts);
// M46 · A3 · centroid "apply learned type to N similar docs"
export const fetchTypeCandidates = (slug, opts) => get(`/documents/learned-types/${encodeURIComponent(slug)}/candidates`, opts);
export const applyTypeToDocs = (slug, docIds, opts) => post(`/documents/learned-types/${encodeURIComponent(slug)}/apply`, { docIds }, opts);
// M46 · §compliance · GDPR/PDPA data-subject rights
export const exportMyData = (opts) => get("/me/export", opts);
export const eraseMyAccount = (opts) => del("/me", opts);
// M46 · §compliance · consent
export const fetchConsent = (opts) => get("/me/consent", opts);
export const acceptConsent = (kind, opts) => post("/me/consent", { kind }, opts);

// M46 · sharing groups
export const fetchGroups = (opts) => get("/groups", opts);
export const createGroup = (name, opts) => post("/groups", { name }, opts);
export const addGroupMember = (groupId, email, opts) => post(`/groups/${groupId}/members`, { email }, opts);
export const removeGroupMember = (groupId, email, opts) => del(`/groups/${groupId}/members/${encodeURIComponent(email)}`, opts);
export const renameGroup = (groupId, name, opts) => patch(`/groups/${groupId}`, { name }, opts);
export const deleteGroup = (groupId, opts) => del(`/groups/${groupId}`, opts);
export const shareDocToGroup = (docId, groupIds, opts) => post(`/documents/${docId}/share-to-group`, { groupIds }, opts);
export const fetchGroupDocuments = (groupId, opts) => get(`/groups/${groupId}/documents`, opts);
export const fetchGroupActivity = (groupId, opts) => get(`/groups/${groupId}/activity`, opts);
// A1 · per-group "ask across this group's documents" chat
export const fetchGroupChat = (groupId, opts) => get(`/groups/${groupId}/chat`, opts);
export const postGroupChat = (groupId, text, opts) => post(`/groups/${groupId}/chat/messages`, { text }, opts);

// ---------- Connector doc retention --------------------------------------
// Re-materialise a connector doc's original after a retention purge.
export const repullDocument = (id, opts) => post(`/documents/${id}/repull`, {}, opts);

// ---------- M47 · superadmin console (plan management) -------------------
// Superadmin-only (gated by DOCAIQ_DOCUMENTS_SUPERADMIN_EMAILS). 403 otherwise.
export const fetchSuperadminUsers = (opts) => get("/superadmin/users", opts);
export const setUserPlan = (pk, plan, opts) => post(`/superadmin/users/${pk}/plan`, { plan }, opts);
export const extendUserTrial = (pk, days, opts) => post(`/superadmin/users/${pk}/trial`, { days }, opts);
