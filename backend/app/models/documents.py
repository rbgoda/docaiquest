from pydantic import BaseModel, ConfigDict


class Document(BaseModel):
    # repositories/documents._to_dict is the source of truth and emits more
    # fields than are explicitly listed here (piiProtected/piiRevealed, groupIds,
    # ownedByMe, hitlEditCount, reviewReasons, …). extra="allow" passes them
    # through instead of Pydantic silently stripping them from every response.
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    path: str
    size: str
    modified: str
    pages: int
    # Explicit (documented) so they're always present + typed.
    piiProtected: bool | None = None
    piiRevealed: bool | None = None
    groupIds: list[int] | None = None
    ownedByMe: bool | None = None
    currentPage: int
    type: str
    content: str
    # M6 upload metadata. None for seeded demo docs.
    mimeType: str | None = None
    sha256: str | None = None
    uploadedBy: str | None = None
    hasFile: bool = False
    # M7 ingestion lifecycle. None for seeded demo docs (not ingested).
    ingestionStatus: str | None = None
    ingestionError: str | None = None
    # KYC extraction (Phase 1). None until the extractor runs.
    # Shape: { "doc_type": "passport", "fields": {...}, "extracted_at": ISO,
    #          "confidence": 0.91, "model": "anthropic/claude-haiku-4.5" }
    extractedFields: dict | None = None
    # G3 · OCR page-quality summary for scanned docs (None for non-OCR).
    ocrQuality: dict | None = None
    # Unified trust score {score, level, reasons} — classification + OCR + field conf.
    trust: dict | None = None
    # M51 · user-applied tags (labels), set via the workspace assistant.
    tags: list[str] = []
    # M11.6 classify-first. Top-1 doc-type + confidence + top-3 alternatives.
    # Used by the targeted matcher to skip irrelevant requirements.
    docType: str | None = None
    docTypeConfidence: float | None = None
    docTypeAlternatives: list | None = None
    # Sub-tenant vendor scoping. NULL means tenant-general (admin uploads,
    # seeded docs). Frontend's ExpensesTab filters receipts to the active
    # vendor by matching vendorPk against the user's active vendor.pk.
    vendorPk: int | None = None
    # M46 · Documents System connector provenance + retention.
    #  · source="drive" for a connector-synced doc (NULL for uploads)
    #  · sourceRef = the re-pull handle (e.g. Drive file id)
    #  · retainOriginal=false + hasFile=false ⇒ the blob was purged after
    #    ingest and the original is re-pullable on demand via /repull.
    source: str | None = None
    sourceRef: str | None = None
    retainOriginal: bool = True
    # Reviewer sign-off (M27, 2026-05-20). Audit trail of state flips lives
    # in document_reviews via /edit-history; these are the LIVE values.
    reviewStatus: str = "pending"  # 'pending' | 'reviewed' | 'exception'
    reviewNote: str | None = None
    reviewedBy: str | None = None
    reviewedAt: str | None = None
    # M27.1 · count of HITL field overrides recorded against this doc in
    # field_edits. Drives the Expenses/Income Accuracy column badge.
    hitlEditCount: int = 0
    # M28 · list of human-readable reasons this doc still needs review.
    # Computed by app.document_review.review_reasons() on every read.
    # Empty list = safe to auto-approve at the tenant's threshold (and may
    # already have been, see reviewedBy='ai-auto'). Each entry has
    # {code, severity, message, hint}. UI shows these in the "Why this
    # needs review" banner inside the doc panel when status is pending.
    reviewReasons: list[dict] = []
    # M29 · soft-archive (2026-05-23). When isArchived, hidden from default
    # /api/documents list; pass ?include_archived=true to surface them.
    isArchived: bool = False
    archivedAt: str | None = None
    archivedBy: str | None = None
