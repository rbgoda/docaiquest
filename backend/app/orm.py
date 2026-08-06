"""ORM tables. Every row carries `tenant_id`; the repository layer always
filters by it. Defense-in-depth via Postgres RLS is a future hardening
(deferred from M4 — app-level filtering is enough today).

JSONB is used for any field the UI already consumes as a nested object
(bullets / trace / tools on chat messages, diff sections, routing config blob).
Avoids over-normalization for read-mostly nested data.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.config import get_settings
from app.db import Base

_EMBED_DIM = get_settings().embed_dim


# ---- Tenants ------------------------------------------------------------
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # M36 · plan_type + usage counters. 'paid' = dedicated container (legacy
    # default, unchanged enforcement). 'free' = lives in shared SaaS container
    # with plan limits (50 docs/month, 1 audit, 5 LLM calls/hour).
    plan_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="paid")
    doc_count_this_month: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    audits_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    llm_calls_this_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    llm_hour_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---- Audit runs ---------------------------------------------------------
class AuditRun(Base):
    __tablename__ = "audit_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(64), nullable=False)  # AR-2026-0418
    vendor: Mapped[str] = mapped_column(String(256), nullable=False)
    # `framework` is the legacy single-string display field. `frameworks`
    # is the source of truth (list of framework names this audit covers).
    # `framework` is kept populated with frameworks[0] (or comma-joined for
    # display) so old UI/report templates keep rendering without changes.
    framework: Mapped[str] = mapped_column(String(128), nullable=False)
    frameworks: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    compliant: Mapped[int] = mapped_column(Integer, nullable=False)
    review: Mapped[int] = mapped_column(Integer, nullable=False)
    missing: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    due: Mapped[str] = mapped_column(String(64), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    spend: Mapped[float] = mapped_column(Float, nullable=False)
    started: Mapped[str] = mapped_column(String(64), nullable=False)
    lead_reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor_contact: Mapped[str] = mapped_column(String(128), nullable=False)
    # Set when the vendor clicks "Submit for review" in VendorHome. NULL =
    # vendor still working. Reviewer dashboards filter on this to know which
    # audits actually have evidence queued.
    vendor_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when admin/reviewer closes the audit via POST /audit-runs/{id}/close.
    # Active queries filter to NULL so dashboards only show in-progress work.
    # The audit_runs row + its joins stay intact for forensics; the closed
    # snapshot lives in audit_history.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # M30.6 · soft-archive (2026-05-24). Hard-deleting a closed audit
    # destroys audit_history + audit_run_requirements + RFIs (the
    # compliance trail). Archive hides the audit from default lists +
    # the History view but preserves every row + the report endpoint,
    # so an audit firm can keep regulator-defensible records without
    # the audit cluttering the operational UI.
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ---- Audit history ------------------------------------------------------
class AuditHistory(Base):
    __tablename__ = "audit_history"
    __table_args__ = (UniqueConstraint("tenant_id", "id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor: Mapped[str] = mapped_column(String(256), nullable=False)
    framework: Mapped[str] = mapped_column(String(128), nullable=False)
    closed: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    duration: Mapped[str] = mapped_column(String(64), nullable=False)
    findings: Mapped[int] = mapped_column(Integer, nullable=False)
    critical_findings: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    cost: Mapped[str] = mapped_column(String(32), nullable=False)
    # M30.7 · soft-archive (2026-05-24). Moved here from audit_runs because
    # legacy closed audits (seed data) have no audit_runs row — only the
    # history snapshot. Putting the flag on audit_history lets us archive
    # ANY closed audit, legacy or not.
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ---- Documents ----------------------------------------------------------
class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id_external"),
        # Per-user dedup guard — one row per (tenant, owner, file) for real
        # connector/upload docs. Partial: seeded docs (no sha) and auditing-product
        # docs (no owner) are unconstrained. Matches migration 0080.
        Index(
            "uq_documents_owner_sha", "tenant_id", "owner_user_id", "sha256",
            unique=True,
            postgresql_where=text("sha256 IS NOT NULL AND owner_user_id IS NOT NULL"),
        ),
    )

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(64), nullable=False)  # doc-iso
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    size: Mapped[str] = mapped_column(String(32), nullable=False)
    modified: Mapped[str] = mapped_column(String(64), nullable=False)
    # M46 · §compliance · server-side creation time (retention clock).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    current_page: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    # `content` was a renderer discriminator when we shipped seeded JSX
    # demo docs. Those went away 2026-05-16 — every Document row is now a
    # real upload rendered via PDF.js, so the value is effectively always
    # "pdf". Kept as a column for back-compat / future doc-type metadata
    # (e.g. "spreadsheet", "image") without a migration.
    content: Mapped[str] = mapped_column(String(64), nullable=False)
    # Real-upload metadata. NULL for seeded demo docs.
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Ingestion lifecycle (M7). NULL for seeded demo docs; the worker only
    # processes uploaded files. Values: pending | processing | ready | failed.
    ingestion_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ingestion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # KYC extraction (Phase 1, 2026-05-18). When the matcher auto-attaches a
    # document to a KYC-* requirement at conf ≥ threshold, the worker runs
    # the KYC extractor (Anthropic vision via OpenRouter) which pulls
    # typed fields per doc-type (passport: name/dob/passport_no/expiry;
    # Aadhaar: name/dob/aadhaar_no; utility: provider/address/billing_date;
    # etc.). Schema is `{doc_type: str, fields: dict, extracted_at: iso}`.
    # NULL for non-KYC docs or before extraction completes.
    extracted_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-line geometry map built at parse time from page.get_text("dict").
    # {line_id: {page, y0_pct, h_pct, page_w, page_h}} — line IDs are hex
    # strings ("0x1:3f" = page 1, line 63). Full-width bands (x spans page).
    # NULL for non-PDF or pre-0106 docs.
    line_map: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-block registry built at parse time from the parser IR (Docling / structured).
    # {block_id: {kind, page, x0_pct, y0_pct, x1_pct, y1_pct, page_w, page_h}}
    # Stable across re-ingestion — block IDs are sequential hex (b_0001, b_0002, …).
    # NULL for non-Docling or pre-0107 docs.
    block_map: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # M54 · LLM-translated markdown cache, keyed by language code.
    # {"fr": {"body": "…", "annotated_body": "…", "translated_at": "2026-…", "model": "…", "status": "…"}, …}
    translations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Cached faithful whole-document Markdown (vision-rendered, on-demand). NULL until first built.
    rendered_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_markdown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rendered_markdown_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # G3 · OCR page-quality summary for vision-OCR'd docs (NULL for non-OCR docs).
    # {pagesScored, lowConfidencePages, minScore, flagged, threshold,
    #  pages:[{page, score, flags}]} — see app/ocr_quality.summarize_pages.
    ocr_quality: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # M51 · user-applied tags (labels) — a list of strings. Set via the
    # workspace assistant (set_tags) or future UI; used for filtering. NULL/[]
    # when untagged.
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # M11.6 classify-first pipeline (2026-05-18). Filled by the classifier
    # agent on every doc upload — one cheap LLM call returns top-3 doc-
    # type guesses. The targeted matcher then walks only requirements
    # whose `required_docs` labels overlap the classified type, skipping
    # ~95% of the validator calls the old broad-matcher made. NULL until
    # classification runs.
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doc_type_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    doc_type_alternatives: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # M17 phase 1 — sub-tenant vendor scoping. NULL means the doc belongs
    # to the tenant generally (seeded docs, admin uploads). When set,
    # vendor-scoped queries should filter by this. Phase 2 will add the
    # repository filtering; phase 1 is the column only.
    vendor_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vendors.pk", ondelete="SET NULL"), index=True, nullable=True
    )
    # M46 · Documents System · per-user workspace ownership. NULL in the
    # auditing product (no per-user scoping there). In a documents stack it's
    # set to the uploading user's pk so repositories/retrieval scope every
    # query to that user's own documents — self-registered users in a shared
    # documents stack never see each other's files.
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.pk", ondelete="CASCADE"), index=True, nullable=True
    )
    # M46 · Documents System · group sharing. When set, the doc is shared into a
    # group (a shared Drive folder); every group member can see + manage it. The
    # original sharer stays the owner_user_id (provenance). NULL = personal.
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document_groups.pk", ondelete="SET NULL"), index=True, nullable=True
    )
    # M46 · Documents System · connector provenance + retention.
    #  · source       — "upload" (default/NULL) | "drive" | "link"
    #  · source_ref   — the re-pull handle for connector docs (e.g. a Drive
    #                   file id). Lets us re-fetch the original on demand after
    #                   the stored blob has been purged.
    #  · retain_original — when False, the worker purges the stored blob after
    #                   ingestion (chunks/embeddings stay; original is re-pulled
    #                   from source_ref on demand). Defaults True (keep original)
    #                   so uploads + the auditing product are never purged.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retain_original: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # M44.P11.2 · PII-at-rest. `pii_protected` = this doc's stored text
    # (chunks + extracted_fields) is tokenized; real values live encrypted
    # in pii_vault. `pii_revealed` = per-doc toggle an owner/admin/reviewer
    # flipped to detokenize on read. Both default false (feature off).
    pii_protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    pii_revealed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # M44.P10 · two-phase delete marker. NULL normally; 'pending' set during
    # Phase 1 (learn-and-promote); reverts on rollback; row gone after Phase 2.
    deletion_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Reviewer sign-off state (M27 · 2026-05-20). The audit trail of every
    # status change lives in `document_reviews` — these columns carry the
    # LIVE state used by the Expenses tab status pill + insights filters.
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )  # 'pending' | 'reviewed' | 'exception'
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Soft-archive (M29 · 2026-05-23). Once an audit closes, hard-deleting
    # a referenced document would break history snapshots and next-cycle
    # clones. Archive hides the doc from default UI but keeps all rows +
    # S3 object intact. Mirror of Vendor.is_archived. Hard-delete is still
    # allowed via DELETE while the doc is only referenced by active audits.
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ---- Requirements -------------------------------------------------------
class Requirement(Base):
    """An *atomic* compliance check (REQ-027 = "ISO certificate valid in scope").
    The AI's classified status lives here — same logical requirement across
    audit runs. The HUMAN reviewer's per-audit verdict moved to
    AuditRunRequirement so the same REQ-027 can be approved for one vendor
    and rejected for another within the same tenant."""

    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("tenant_id", "id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(64), nullable=False)  # REQ-027
    group: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    subtitle: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok/warn/miss/todo
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    doc_id_external: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prior_doc_id_external: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Acceptable evidence labels — list of document types the reviewer
    # expects to see attached for this requirement. Curated by admin in
    # Settings → Requirements. Used by the matcher to score doc-vs-req
    # relevance and shown verbatim in the Review screen "Accepted evidence
    # includes…" line.
    required_docs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    # Optional matcher prompt override. When set, the matcher uses this as
    # the user_message instead of the generic template. NULL falls back to
    # the default behaviour. See agents/matcher.py for how it's consumed.
    match_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditRunRequirement(Base):
    """Per-audit-run row for each (audit_run, requirement). Holds the human
    reviewer's verdict — separate from the requirement's AI-classified status.
    Pre-populated at seed time with N audit_runs × M requirements rows."""

    __tablename__ = "audit_run_requirements"
    __table_args__ = (UniqueConstraint("audit_run_pk", "requirement_pk"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    audit_run_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("audit_runs.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    requirement_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("requirements.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verdict_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verdict_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Reviewer's free-text reason. Optional on approve; conventionally
    # required by the UI for reject / needs-info so the vendor knows what
    # to fix. Surfaces verbatim on the VendorHome row.
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M31.6 · Multi-evidence per requirement. The legacy single-doc
    # Requirement.doc_id_external is the "primary" attachment (highest
    # confidence or first attached) for backwards compat with the UI's
    # one-column-per-row layout. evidence_docs holds the full list of all
    # documents the matcher (or manual attach) found as supporting
    # evidence for THIS audit_run × requirement. Each entry:
    #   {doc_id, confidence, attached_at, attached_by, source}
    # where source ∈ {"ai", "manual", "reusable"}.
    evidence_docs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditSubject(Base):
    """A natural person (or named entity) an audit is for. KYC-style audits
    are subject-bound — the matcher must reject documents that don't pertain
    to one of the listed subjects. One audit can have many subjects (e.g.
    multiple company directors).

    Stored separately from `audit_runs` so subjects can be added/removed
    without touching the audit row, and so the matcher can list them by
    audit_pk in one query."""

    __tablename__ = "audit_subjects"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    audit_run_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("audit_runs.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Free-text role: 'director', 'UBO', 'shareholder', 'individual', etc.
    # Surfaces as a pill in UI; doesn't affect matching today (could later
    # gate certain reqs by role — e.g. 'source of wealth' only for UBOs).
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dob: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Hint for the extraction agent (passport no, national ID, etc). Optional.
    id_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M31.2.1 · alternative names this subject may appear under in
    # documents. Indian passports often surface as 'GODA, ARUDAIH
    # BALVANTRAI' while the person commonly goes by 'Rajesh Goda'.
    # Aliases let admin list every form so the matcher accepts any of
    # them as evidence of this subject. JSON list of strings.
    aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- Vendors ------------------------------------------------------------
class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (UniqueConstraint("tenant_id", "id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(64), nullable=False)  # v-atlas
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    logo: Mapped[str] = mapped_column(String(8), nullable=False)
    active_audits: Mapped[int] = mapped_column(Integer, nullable=False)
    open_items: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    last_activity: Mapped[str] = mapped_column(String(64), nullable=False)
    contacts: Mapped[int] = mapped_column(Integer, nullable=False)
    frameworks: Mapped[list] = mapped_column(JSONB, nullable=False)
    tier: Mapped[str] = mapped_column(String(64), nullable=False)
    # Default lead reviewer for new audit-runs against this vendor. Nullable —
    # admin can leave blank at creation time and the wizard falls back to the
    # creator's own email. Not an FK to users.email by design (admin can name
    # a pending invitee before the account exists).
    primary_reviewer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Owner-only soft-delete. Archived vendors are hidden from the default
    # /api/vendors list and from the AdminVendors picker, but their PK
    # remains valid so historical audit_runs / RFIs / vendor users still
    # resolve. Unarchive restores them to the list, lossless.
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ---- Highlights ---------------------------------------------------------
class Highlight(Base):
    __tablename__ = "highlights"
    __table_args__ = (UniqueConstraint("tenant_id", "id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(64), nullable=False)  # hl-iso-1
    doc_id_external: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    pin: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_label: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    is_box: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # PDF page bbox in normalized coords [x0, y0, x1, y1] (0..1). NULL when the
    # citation was extracted from a seeded JSX doc — the frontend falls back to
    # text-search. M8 entity extraction populates this for new uploads.
    bbox: Mapped[list | None] = mapped_column(JSONB, nullable=True)


# ---- Chat messages ------------------------------------------------------
class ChatMessage(Base):
    """One row per message. Replaces the JSON conversations + chat_extras
    overlay from M2 — fixture-loaded messages and user-posted messages share
    this table; ordering is by primary key (auto-increment)."""

    __tablename__ = "chat_messages"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Thread anchor — one of three scopes, exactly one populated per row:
    #   · requirement_id_external — M2 Review chat (per requirement)
    #   · doc_id_external          — M11.7 chat-with-a-single-document
    #   · workspace_key            — M44.P12 overall-documents chat. Value is
    #     `vendor:<vendor_pk>` (cross-doc Q&A over one vendor's document set)
    #     or `tenant` (all-tenant, future). When set, the other two are NULL.
    requirement_id_external: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    doc_id_external: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(8), nullable=False)  # user | ai
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bullets: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    trace: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # M11.7 doc-chat citations — list of {chunk_pk, page, bbox, quote} the AI
    # cited for this answer. Frontend renders yellow markers from these.
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- Diffs --------------------------------------------------------------
class Diff(Base):
    """Document-pair diffs. Stored as one row keyed on the "current" doc id;
    sections + summary live in JSONB because they're consumed as nested
    structures and never queried individually."""

    __tablename__ = "diffs"
    __table_args__ = (UniqueConstraint("tenant_id", "current_doc_id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    current_doc_id_external: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_doc_id_external: Mapped[str] = mapped_column(String(64), nullable=False)
    sections: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)


# ---- Users + roles ------------------------------------------------------
class User(Base):
    """Tenant-scoped user. `password_hash` is only populated in dev mode
    (DOCAIQ_AUTH_PROVIDER=dev); in OIDC mode users are auto-provisioned on
    first login from token claims and `password_hash` stays NULL."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # M17 phase 1 — when a user has the `vendor` role, this binds them to a
    # specific Vendor row. Phase 2 will enforce: vendor users only see their
    # own vendor's docs/RFIs. NULL for internal users (admin / reviewer).
    vendor_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vendors.pk", ondelete="SET NULL"), index=True, nullable=True
    )
    # M42 · access-request gate. When TRUE, the user's workspace is on
    # hold pending re-approval. Login endpoints return 423 Locked and the
    # frontend renders a 'Workspace under review' panel. Backfilled to
    # TRUE for every existing user in the shared free SaaS tenant by
    # migration 0037; new users default to FALSE.
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    # Session revocation (mig 0099): stamped into each JWT as `tv`; bumping this
    # invalidates every token issued before the bump (logout-all / password change /
    # freeze). Enforced only when settings.session_revocation is on.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    # M48 · email verification (Documents public signup). Email/password signups
    # start FALSE and confirm via a Resend link; Google sign-ins are set TRUE at
    # provision (Google already verified). Existing users grandfathered TRUE.
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    # Drive backup encryption (opt-in, user-owned password). When enabled, the
    # Drive workspace backup is encrypted with a scrypt key derived from the
    # user's password. We store ONLY the salt + a verification token (a sentinel
    # encrypted with the key) — never the password or the key. backup_check lets
    # us validate a supplied password without decrypting the whole backup.
    backup_encryption: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    backup_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backup_check: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # M47 · per-user subscription. plan ∈ trial|free|pro|enterprise. A new user
    # starts on a 7-day 'trial' (full Pro access); after trial_ends_at the
    # effective plan falls back to 'free' unless they're pro/enterprise.
    # Superadmin can set plan + trial_ends_at directly.
    plan: Mapped[str] = mapped_column(String(16), nullable=False, server_default="trial")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a promo-granted paid plan expires (NULL = no expiry). effective_plan reverts
    # pro/enterprise to 'free' once past this.
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="joined"
    )


class UserRole(Base):
    """One row per (user, role). Roles: owner | admin | reviewer | vendor."""

    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_pk", "role"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.pk", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    user: Mapped["User"] = relationship(back_populates="roles")


class ConnectorAccount(Base):
    """M46 · Documents System · a user's connected external source (today only
    Google Drive). One row per (tenant, owner_user, provider). Stores the OAuth
    token for the real `google` backend; the `stub` dev backend just records a
    sentinel so "connected" state persists. Tenant + per-user scoped exactly
    like documents — a user only ever sees their own connectors."""

    __tablename__ = "connector_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_user_id", "provider"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.pk", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # "drive"
    backend: Mapped[str] = mapped_column(String(16), nullable=False)   # "stub" | "google"
    # OAuth material (NULL for the stub backend). access_token is short-lived;
    # refresh_token is the durable grant used to mint new access tokens.
    access_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # M46 · B7 · per-user 'encrypt my Drive files' choice. When True, files
    # DocAIQ stores in this user's Drive are encrypted in place (openable only
    # via DocAIQ); when False they're plaintext (openable directly in Drive).
    encrypt_files: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChatFeedback(Base):
    """M46 · Documents System · thumbs-up/down + free-text feedback on a chat
    answer. Feeds the improvement loop: 👎 demotes the answer in the reflexion
    cache AND logs WHY here so prompts/extraction can be tuned. Tenant + per-user
    scoped."""

    __tablename__ = "chat_feedback"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # "up" | "down"
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M46 · rich feedback modal (xpenseaiq-style box): the 👎 form captures a
    # category ("wrong"/"incomplete"/"offtopic"/"other"), a separate suggestion
    # ("what would the right answer be?"), and an optional 1–5 star rating.
    category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # M46 · up to 3 client-side-compressed screenshots (JPEG data URLs) so we
    # can SEE the issue the reviewer is reporting, not just read about it.
    screenshots: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProductFeedback(Base):
    """App-level product feedback (the 'Send feedback' screen) — distinct from
    ChatFeedback, which rates one chat answer. Mirrors the XpenseAIQ model: a
    rating + category + comments + suggestion submitted from anywhere in the app,
    reviewed + resolved in the superadmin console (status new→reviewed→resolved).
    Tenant + per-user scoped."""

    __tablename__ = "product_feedback"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5 stars
    category: Mapped[str] = mapped_column(String(16), nullable=False, server_default="general")
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    screenshots: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # up to 3 compressed JPEG data URLs
    has_issues: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="new", index=True)
    # Auto-triage draft or a superadmin's manual resolution note. status flow:
    # new → in_progress (triaged) → reviewed → resolved.
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Human review (mig 0100): the reviewer either ACCEPTS the resolution (→ status
    # 'verified') or flags FOLLOW-UP NEEDED with a note that re-opens the item.
    followup_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    followup_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Hierarchical version ref "1.1.<patch>.<seq>": patch = #resolved/verified at creation
    # (so the main version 1.1.<patch> grows as feedback is resolved), seq = per-patch counter.
    ref: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    verified_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromoCode(Base):
    """A shareable promo code that grants a paid plan for a fixed duration when a user
    redeems it. Superadmin creates codes; redemption sets the user's plan + plan_expires_at
    (now + duration_days) and bumps `redemptions`."""

    __tablename__ = "promo_codes"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False)              # pro | enterprise
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null = unlimited
    redemptions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # code validity
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_promo_tenant_code"),)


class LlmUsageRollup(Base):
    """Pre-aggregated LLM utilization so the admin panel reads a small, bounded table instead
    of scanning llm_call_audit (~1.5M rows/mo at scale). `period='day'` rows cover the last 30
    days (rebuilt each run); `period='month'` rows are one-per-month and PERSIST after the raw
    ledger is purged. `user_email=''` = tenant-wide aggregate; non-empty = per-user."""

    __tablename__ = "llm_usage_rollup"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_email: Mapped[str] = mapped_column(String(256), nullable=False, server_default="")  # '' = all-users
    period: Mapped[str] = mapped_column(String(8), nullable=False)                            # day | month
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_email", "period", "period_start", "provider", "model",
                         name="uq_llm_rollup"),
        Index("ix_llm_rollup_read", "tenant_id", "user_email", "period", "period_start"),
    )


class SchemaLibrary(Base):
    """A versioned, HITL-approved extraction schema for a document type — drafted by the
    Schema-Architect agent (or seeded/crystallized), reviewed by a human, then promoted live.
    `status`: proposed → approved/rejected. Once approved, extraction routes this type here."""

    __tablename__ = "schema_library"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    type_slug: Mapped[str] = mapped_column(String(64), nullable=False)     # e.g. "passport"
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False)            # the schema field map
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="proposed")
    source: Mapped[str] = mapped_column(String(24), nullable=False, server_default="architect")
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)  # LLM that drafted it
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_doc_pk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "type_slug", "version", name="uq_schema_lib"),)


class QaResult(Base):
    """Server-side state for the live QA tracker in the admin console — one row per (tenant,
    question). Shared across the team + persistent (unlike the standalone tracker's localStorage).
    `qid` is the seed question id (e.g. 'invoice-3') or a custom id ('c<ts>')."""

    __tablename__ = "qa_result"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    qid: Mapped[str] = mapped_column(String(64), nullable=False)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="untested")
    issue: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    last_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "qid", name="uq_qa_result_tenant_qid"),)


class SuperadminAllow(Base):
    """DB-backed admin allowlist, managed from the console — added to the static env allowlist
    (`documents_superadmin_emails`) so operators can grant/revoke console access without a redeploy.
    Env emails are the immutable bootstrap; these are the mutable additions."""

    __tablename__ = "superadmin_allow"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_superadmin_allow_tenant_email"),)


class PlanConfig(Base):
    """Superadmin-configurable plan limits + toggles (free/pro/enterprise). One
    row per (tenant, plan); missing rows fall back to subscriptions.DEFAULT_PLANS.
    Lets an operator enable/disable a tier, set the monthly document + AI-message
    quotas, toggle paid LLM models / LLM access per tier, and flag the Enterprise
    dedicated-container option — all without a code change."""

    __tablename__ = "plan_config"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan: Mapped[str] = mapped_column(String(16), primary_key=True)  # free|pro|enterprise|trial
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    docs_monthly: Mapped[int | None] = mapped_column(Integer, nullable=True)   # null = unlimited
    ai_monthly: Mapped[int | None] = mapped_column(Integer, nullable=True)     # null = unlimited
    features: Mapped[list | None] = mapped_column(JSONB, nullable=True)        # Pro feature keys
    paid_models: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    llm_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    dedicated_container: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppInstance(Base):
    """Fleet registry of DocAIQ deployments (the shared SaaS + Enterprise
    dedicated containers). A dedicated container registers on boot and sends
    heartbeats to the central (shared) instance; the superadmin approves/revokes
    and sees which deployments are alive. Single pane of glass over the fleet."""

    __tablename__ = "app_instances"

    instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan: Mapped[str] = mapped_column(String(16), nullable=False, server_default="enterprise")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")  # pending|approved|revoked
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # users/docs counts etc from the instance
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class LlmProviderConfig(Base):
    """Superadmin LLM provider config (openrouter/dashscope/anthropic/google/openai).
    Lets an operator paste/replace an API key at runtime (encrypted at rest),
    enable/disable a provider, and set a default model — without a redeploy. The
    effective key (DB override else env) is applied onto the settings object at
    boot + on save, so the gateway needs no change."""

    __tablename__ = "llm_provider_config"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet; null = use env
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CustomLlmProvider(Base):
    """Superadmin-defined OpenAI-compatible LLM provider (e.g. Groq, Together,
    Fireworks, vLLM).  base_url INCLUDES the API version segment (…/openai/v1);
    the gateway appends /chat/completions.  api_key_enc is Fernet-encrypted
    (same derivation as LlmProviderConfig); null = no auth header."""

    __tablename__ = "custom_llm_providers"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LearnedSchema(Base):
    """M46 · Documents System · a SELF-LEARNING extraction schema per doc-type
    cluster. The universal extractor records which field labels + record kinds
    it found for each classifier doc_type; the next document of that type is
    hinted with the accumulated list so extraction gets more complete and
    consistent the more documents of a kind the workspace sees. Tenant-scoped
    metadata (field names only, never document content)."""

    __tablename__ = "learned_schemas"
    __table_args__ = (UniqueConstraint("tenant_id", "doc_type"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # {field_label: times_seen}
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # {record_kind: times_seen}
    record_kinds: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Move-1 (b) · {field_label: {"types": {type: count}, "values": [distinct examples]}}
    # — populated ONLY for training-eligible (consented free-plan) docs; drives typed
    # schema crystallization. Empty for paid docs (field-names-only stays the default).
    field_examples: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LearnedDocType(Base):
    """M46 · self-learning CLASSIFICATION memory. When the closed-enum
    classifier returns 'other'/low-confidence, the type reconciler derives an
    open-vocabulary type from the doc's own AI summary and registers it here,
    per user. The growing vocabulary lets the workspace classify similar future
    docs and powers the cross-doc 'apply to similar' suggestion. Metadata only
    (a slug + label + counts, never document content)."""

    __tablename__ = "learned_doc_types"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_user_id", "type_slug"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    type_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ai")  # ai | human
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # M46 · §2 · running-mean embedding of the docs assigned this type + the
    # count averaged in. Drives cheap (no-LLM) distilled classification.
    centroid: Mapped[list[float] | None] = mapped_column(Vector(_EMBED_DIM), nullable=True)
    centroid_n: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeneratedSchema(Base):
    """Move-1 PR3 · a CRYSTALLIZED per-type extraction schema. The nightly
    schema_crystallize job distils a stable LearnedSchema cluster (a doc-type seen
    enough times with a consistent core field-set) into a concrete typed schema.
    When `status='active'` the universal extractor promotes these labels to
    first-class top-level fields for docs of that cluster — recurring ad-hoc types
    graduate from the key_facts bag to named fields ("universal by learning").
    Field-NAMES only, never document values. Tenant-scoped."""

    __tablename__ = "generated_schemas"
    __table_args__ = (UniqueConstraint("tenant_id", "cluster_key"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # the detected_doc_type cluster (matches LearnedSchema.doc_type).
    cluster_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # {field_label: {"type": "string", "description": "..."}} — typed properties
    # the extractor merges onto the universal base schema.
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # proposed → awaiting adoption/review · active → merged into extraction ·
    # rejected → operator-declined.
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="proposed")
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="crystallize")
    # cluster seen_count at the time of (re)crystallization — provenance/debug.
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GoldenEvalCase(Base):
    """Golden eval corpus · one labeled extraction sample captured from a CONSENTED
    free-tier document (KIND_MODEL_TRAINING). Stores the extraction snapshot (doc_type
    + fields + confidence + trust) so we can build/curate a real, diverse evaluation set
    and track coverage by doc type. `verified` flips true when a human corrects/confirms
    the fields (ground truth). Consent-gated — paid docs are never captured. Superadmin-
    export only (field values may contain PII; the free-plan consent covers this use)."""

    __tablename__ = "golden_eval_cases"
    __table_args__ = (UniqueConstraint("tenant_id", "document_pk", name="uq_golden_eval_tenant_doc"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False, index=True)
    doc_id_external: Mapped[str | None] = mapped_column(String(128), nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detected_doc_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    field_confidence: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    trust_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="free_consented")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    edit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class FaithfulnessCase(Base):
    """Chat-faithfulness eval corpus · one AI chat answer to a CONSENTED free-tier
    user — question + answer + cited evidence + abstained flag, plus (attached later
    from ChatFeedback) the human 👍/👎 label, category, suggested-correct-answer and
    rating. Lets us measure/regression-test RAG faithfulness over real usage. Consent-
    gated (paid chats never captured); superadmin-export only (may hold PII)."""

    __tablename__ = "faithfulness_cases"
    __table_args__ = (UniqueConstraint("tenant_id", "message_pk", name="uq_faithfulness_tenant_msg"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_id_external: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, server_default="doc")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[str | None] = mapped_column(String(64), nullable=True)
    abstained: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    label: Mapped[str | None] = mapped_column(String(8), nullable=True)          # up | down
    category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="free_consented")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class SavedView(Base):
    """Intelligence Dashboard · Phase C · a persisted view spec, per user.
    `source='ai'` rows are cached AI-proposed views; `source='user'` are pins/
    edits. Built-in views are code-defined and NOT stored here. The `spec` JSONB
    is the same declarative shape the view engine evaluates. Metadata only."""

    __tablename__ = "saved_views"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_user_id", "view_key",
                                       name="uq_saved_views_owner_key"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    view_key: Mapped[str] = mapped_column(String(64), nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ai")  # ai | user
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DashboardConfig(Base):
    """Dashboard — per-user widget layout persisted as a JSONB array of widget
    specs. One row per (tenant, owner). Auto-generated on first visit when no
    saved config exists."""

    __tablename__ = "dashboard_configs"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_user_id",
                                       name="uq_dashboard_configs_owner"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class AlertRule(Base):
    """User-defined alert rules — watch specific documents, document types, or
    date fields and surface alerts in the Dashboard alert bar. Owner-scoped."""

    __tablename__ = "alert_rules"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # "watch_docs" | "watch_types" | "field_date"
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Flexible config:
    #   watch_docs:  {docIds: [...]}
    #   watch_types: {docTypes: [...]}
    #   field_date:  {fieldName: "...", daysBefore: N, docTypes: [...]}
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="t")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class DocumentGroup(Base):
    """M46 · Documents System · a sharing group. Members (added by gmail) can all
    manage the documents shared into the group. The group is backed by a shared
    Google Drive folder (created in the owner's Drive, shared to each member) so
    the shared docs stay user-owned-in-Drive."""

    __tablename__ = "document_groups"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_by_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # The shared Drive folder backing the group (None for the stub backend).
    drive_folder_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentGroupMember(Base):
    """M46 · membership of a DocumentGroup. `user_id` is NULL until an invited
    email signs up; the Drive folder share is keyed on the email regardless."""

    __tablename__ = "document_group_members"
    __table_args__ = (UniqueConstraint("group_id", "member_email"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_groups.pk", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    member_email: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="member")  # owner | member
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceSync(Base):
    """M46 · §5 · pointer to a user's encrypted workspace.sqlite in their Drive."""

    __tablename__ = "workspace_sync"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_user_id", name="uq_workspace_sync_owner"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConsentRecord(Base):
    """M46 · §compliance · one row per (user, consent kind). `kind` is
    'processing' (signup) or 'personal_data' (pre-first-upload). `version` lets
    us re-prompt when the consent text changes."""

    __tablename__ = "consent_records"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "kind", name="uq_consent_user_kind"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentGroupEvent(Base):
    """M46 · §1 · group activity log — one row per group action (created,
    renamed, member added/removed, doc shared/unshared). Members read it to see
    who did what. CASCADE-deleted with the group."""

    __tablename__ = "document_group_events"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_groups.pk", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentGroupShare(Base):
    """M46 · a document shared into a group (many-to-many). Replaces the single
    documents.group_id FK so one doc can live in several groups at once. The
    sharer stays the doc's owner_user_id; every member of any group it's shared
    into can see + manage it."""

    __tablename__ = "document_group_shares"
    __table_args__ = (UniqueConstraint("document_pk", "group_id", name="uq_doc_group_share"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_groups.pk", ondelete="CASCADE"), nullable=False, index=True
    )
    # M46 · A2 · Drive file id of the copy in the group folder, so unshare can
    # delete that copy (NULL = no Drive copy / stub backend).
    drive_copy_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- Entities (extracted at ingest, M8) ---------------------------------
class Entity(Base):
    """Node in the graph layer. Two populations live here:

    - **Regex extractions** (the original M9 use) — money/date/control-IDs
      pulled by app/entities.py during ingestion. `source='regex'`.
    - **Graph nodes** (L3) — Person / Org / Money / Date / Location /
      Standard etc derived from `documents.extracted_fields` via the
      bootstrap pass in app/graph/bootstrap.py. `source='fact_bootstrap'`.

    Both share the same shape — the `kind` enum widens for graph use,
    and the L3-specific columns (`vendor_pk`, `canonical`, `confidence`,
    `graph_run_pk`) are nullable so old regex rows keep working unchanged.
    """

    __tablename__ = "entities"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document_chunks.pk", ondelete="CASCADE"), nullable=True
    )
    # L3 · denormalized vendor for fast per-vendor filter on graph queries.
    vendor_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vendors.pk", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # L3 · normalized form used for dedup / fuzzy match across docs.
    # e.g. "Mr. Goda Rajesh Balvantrai" → "goda rajesh balvantrai".
    canonical: Mapped[str | None] = mapped_column(String(256), nullable=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # L3 · which extraction pass produced this row, and how it was made.
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="regex")
    graph_run_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("graph_runs.pk", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft-delete for append-only graph runs — when a doc is re-extracted,
    # old entities are deprecated rather than CASCADE-deleted so other docs
    # that reference them via alias edges aren't broken.
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deprecated_by_run_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("graph_runs.pk", ondelete="SET NULL"), nullable=True
    )


class GraphRun(Base):
    """Audit log of every graph-extraction pass. Re-running an extraction
    creates a new row; rolling back is "delete entities/relations where
    graph_run_pk = X". Lets us re-run safely without nuking unrelated rows."""

    __tablename__ = "graph_runs"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # bootstrap | llm_entity | llm_relation | reconcile
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    entities_added: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    relations_added: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EntityRelation(Base):
    """Directed edge between two entities. `relation` is a free-form
    string slug — keep these short and consistent (signed_by, paid_to,
    effective_on, references, governed_by, etc). Carries `vendor_pk`
    denormalized for fast per-vendor traversal."""

    __tablename__ = "entity_relations"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    vendor_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vendors.pk", ondelete="SET NULL"), nullable=True
    )
    src_entity_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("entities.pk", ondelete="CASCADE"), nullable=False
    )
    dst_entity_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("entities.pk", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where this edge came from — useful for the "show me proof" UX in chat.
    evidence_doc_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), nullable=True, index=True
    )
    evidence_chunk_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document_chunks.pk", ondelete="SET NULL"), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="fact_bootstrap")
    graph_run_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("graph_runs.pk", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft-delete for append-only graph runs (same pattern as Entity).
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deprecated_by_run_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("graph_runs.pk", ondelete="SET NULL"), nullable=True
    )


# ---- Document chunks + embeddings ---------------------------------------
class DocumentChunk(Base):
    """One row per text chunk produced by the M7 ingestion pipeline.

    `embedding` is a pgvector column; M8 adds an HNSW index for cosine search.
    The dimension is fixed at boot from `DOCAIQ_EMBED_DIM`. Switching embedding
    backends means dropping + re-ingesting (intentional — embeddings from
    different models aren't comparable)."""

    __tablename__ = "document_chunks"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # P9.5 · chunk kind discriminator. 'text' = a normal sliding-window text
    # chunk (default, all chunks pre-P9.5); 'table' = a Markdown table rendered
    # by pdfplumber. Table chunks are retrieved like any other but preserved
    # verbatim by the materializer instead of being reflowed through the LLM.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="text")
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBED_DIM), nullable=False)
    # Retrieval Step 2 · dual-column BGE-M3 (1024d) embedding. NULL until backfilled; retrieval
    # flips to it when embed_v2_active. Kept alongside `embedding` for a reversible migration.
    embedding_v2: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    # M11.7 bounding box on the page (PDF coords). Populated at ingest by
    # PyMuPDF page.search_for(); NULL when the chunk text isn't found on the
    # page (rare — chunks straddling page boundaries, heavily normalized text).
    # Shape: {"page": int, "x0": float, "y0": float, "x1": float, "y1": float}
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Line IDs this chunk spans — keys into Document.line_map for geometry
    # lookup. ["0x1:3f", "0x1:40", ...]. NULL for pre-0106 or non-PDF docs.
    line_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Block IDs this chunk spans — keys into Document.block_map for geometry +
    # type lookup. ["b_0001", "b_0003", ...]. NULL for pre-0107 or non-IR docs.
    block_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # M43.P1 · Contextual Retrieval (Anthropic, Sep 2024). ~50-100 token
    # sentence that situates the chunk within the document — embedded
    # alongside the chunk text so retrieval recall improves +35-49%.
    # NULL on chunks ingested pre-M43.P1; they continue to work with the
    # normal hybrid retrieval, just don't get the contextual boost.
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Chunk inspection: a reviewer can exclude a chunk from retrieval (kept for provenance).
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Migration safety: incremented on pipeline changes so stale-chunk detection
    # and targeted re-processing can operate without full re-ingestion.
    pipeline_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── M44.P11.2 · PII-at-rest vault ─────────────────────────────────────────
class PIIVaultEntry(Base):
    """One row per (document, token). Holds the REAL PII value, Fernet-
    encrypted, so the text we persist in document_chunks / extracted_fields
    can carry only the placeholder (`[CREDIT_CARD_1]`). Detokenized on the
    fly when an authorized user reveals the document. CASCADE-deletes with
    the parent document, so hard-deleting a doc purges its PII."""

    __tablename__ = "pii_vault"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)        # [CREDIT_CARD_1]
    kind: Mapped[str] = mapped_column(String(32), nullable=False)         # credit_card / passport / …
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)    # Fernet ciphertext
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── M43.P1.5 · Hermes-style self-critique memory ──────────────────────────
class ReflexionPair(Base):
    """One row per chat-answer that went through the Critic-Refine loop.

    The (question_embed) vector enables cosine retrieval of similar
    prior questions — those critiques get injected as "Common mistakes
    to avoid" few-shot in the validator's prompt on the next query,
    forming a self-improving loop without fine-tuning.

    Reviewer thumbs feedback drives `helpful_count` /
    `marked_unhelpful_count`. The few-shot filter only pulls critiques
    where helpful_count > marked_unhelpful_count, so noise critiques
    self-prune over time.
    """

    __tablename__ = "reflexion_pairs"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_embed: Mapped[list[float] | None] = mapped_column(
        Vector(_EMBED_DIM), nullable=True,
    )
    draft_answer: Mapped[str] = mapped_column(Text, nullable=False)
    critique: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str] = mapped_column(Text, nullable=False)
    doc_id_external: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # M46 · §4 · per-owner scope (documents product). NULL for auditing rows and
    # for pre-migration rows. Serving reads require an exact owner match when an
    # owner is in context, so cross-user cache/few-shot leakage is closed.
    owner_user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    marked_unhelpful_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    passed_on_first: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # M44.P10 · 'doc_specific' rows are EVIDENCE (purged when their doc is
    # deleted); 'general' rows (doc_id_external NULL) are UNDERSTANDING and
    # survive deletion + are eligible for P13 global promotion.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="doc_specific")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ---- M44.P10 · UNDERSTANDING tier (generalizable, survives doc delete) ---
class ExtractionCorrection(Base):
    """Anonymized, type-level extraction patterns ("the LLM often misses field
    Y on doc_type X"). Generalizable knowledge, not customer data — eligible
    for P13 global promotion when source='local'."""

    __tablename__ = "extraction_corrections"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    pattern: Mapped[dict] = mapped_column(JSONB, nullable=False)
    observations_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # 'local' = earned here (promotable to global); 'global' = seeded from the
    # control-plane pool (never re-promoted — prevents an echo loop).
    source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="local")
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class AgentSkillMemory(Base):
    """Tool sequences that succeed for a doc_type + an anonymized question
    template ("what is the {id_field}?"). Generalizable agent skill —
    promotable to global when source='local'."""

    __tablename__ = "agent_skill_memory"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    question_template: Mapped[str] = mapped_column(Text, nullable=False)
    tool_sequence: Mapped[list] = mapped_column(JSONB, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="local")
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class EntityCanonical(Base):
    """Canonical org/person names + alias spellings, surviving any one doc's
    deletion. LOCAL ONLY — real names are customer data and are NEVER promoted
    to the global pool (hence no `source` column)."""

    __tablename__ = "entity_canonical"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)          # person | org
    canonical: Mapped[str] = mapped_column(String(256), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    observed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class EntityIdentity(Base):
    """Durable cross-document entity node (graph step 3). One row per resolved
    identity (person/org) unifying the per-doc `Entity` mentions that denote the
    same real-world entity. DERIVED — the resolver rebuilds it from the current
    mentions after each ingest, so it survives any single doc's re-extraction and
    stays consistent with the graph. Owner-scoped in the documents product."""

    __tablename__ = "entity_identity"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "kind", "identity_key",
                         name="uq_entity_identity"),
        Index("ix_entity_identity_scope", "tenant_id", "owner_user_id", "kind"),
    )

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)           # person | org
    identity_key: Mapped[str] = mapped_column(String(256), nullable=False)  # stable canonical
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    doc_pks: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---- LLM call audit ledger (M44.P11) ------------------------------------
class LLMCallAudit(Base):
    """One row per external LLM call. Stores hashes of prompt/response
    (not contents) so we have a tamper-evident log without becoming a
    PII custodian ourselves.

    Compliance-grade reports answer:
      · 'where did tenant X's data go?' → filter on data_residency
      · 'how much PII did we redact this quarter?' → SUM(pii_entities_redacted)
      · 'forensically reproduce call Y' → replay against the chunks
        + the model recorded here.
    """

    __tablename__ = "llm_call_audit"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    task_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    doc_id_external: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pii_entities_redacted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    pii_kinds: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    data_residency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ---- Document artifacts (M44.P4) ----------------------------------------
class DocumentArtifact(Base):
    """One row per document. Holds materialized artifacts (markdown /
    JSON / summary / entities / TOC) generated ONCE at ingest and served
    from DB on every subsequent request. See migration 0041 for full
    strategy rationale."""

    __tablename__ = "document_artifacts"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    processing_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'full' | 'reduced' | 'summary_only' | 'skipped'
    processing_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_text_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_short: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_long: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    key_entities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    table_of_contents: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(), onupdate=func.now(), nullable=False,
    )


# ---- Agent traces (M44.P2) ----------------------------------------------
class AgentTrace(Base):
    """One row per ReAct step inside a Document Agent run. The FK on
    chat_message_pk lets the frontend hydrate the full trace by clicking
    "Show reasoning" on the answer row.

    `step_index` is 0-based and monotonic within a single chat message.
    `action_name=='final_answer'` marks the terminator step.
    """

    __tablename__ = "agent_traces"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    chat_message_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_messages.pk", ondelete="CASCADE"), nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    thought: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_args: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ---- Routing config -----------------------------------------------------
class RoutingConfig(Base):
    """One row per tenant. The full config blob lives in JSONB — the M2 PUT
    endpoint already validates against the Pydantic model so we don't need
    a normalized schema here."""

    __tablename__ = "routing_configs"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---- Requirement RFIs (M13) --------------------------------------------
class RequirementRFI(Base):
    """Request-for-Info raised by a reviewer on a specific requirement
    within a specific audit run. Vendor responds; reviewer resolves. One
    RFI per back-and-forth round — if the reviewer needs more, they raise
    another rather than threading. Keeps the data model boring."""

    __tablename__ = "requirement_rfis"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    audit_run_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("audit_runs.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    requirement_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("requirements.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    raised_by: Mapped[str] = mapped_column(String(256), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # `open` (vendor needs to respond), `responded` (vendor replied, awaiting
    # reviewer), `resolved` (reviewer accepted response), `cancelled`.
    vendor_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # M17 phase 1 — sub-tenant vendor scoping. The audit run already implies
    # a vendor (via AuditRun.vendor), but we denormalise to vendor_pk here
    # so the cheap repo filter in phase 2 can do one indexed lookup rather
    # than joining through audit_runs every time.
    vendor_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vendors.pk", ondelete="SET NULL"), index=True, nullable=True
    )


# ---- LLM call ledger (M9) -----------------------------------------------
class LLMCall(Base):
    """One row per provider call. Drives the WhyModal, cost dashboards, and
    routing-rule hit counters. Always tenant-scoped."""

    __tablename__ = "llm_calls"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    requirement_id_external: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_pk: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # per-doc cost
    chat_message_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chat_messages.pk", ondelete="SET NULL"), index=True, nullable=True
    )
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)   # validate | classify | report
    tier: Mapped[str] = mapped_column(String(8), nullable=False)         # t1 | t2 | t3
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)      # ok | escalated | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- Framework packs ----------------------------------------------------
class FrameworkPack(Base):
    """Custom bulk-import pack uploaded by an admin in Settings → Requirements.
    Built-in packs (SOC 2, ISO 27001, HIPAA, etc) still ship as static CSV
    files under `public/samples/frameworks/`; the frontend merges this table
    with the static manifest when rendering the pack grid."""

    __tablename__ = "framework_packs"
    __table_args__ = (UniqueConstraint("tenant_id", "id_external"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    id_external: Mapped[str] = mapped_column(String(128), nullable=False)  # slug (from name)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # M44.F1 · marketplace slug this pack was installed from (NULL for custom
    # CSV uploads). Links the pack to its catalog source for versioning +
    # install-delta — distinct from id_external (which is slugified from name).
    marketplace_slug: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    control_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="custom")
    csv_body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── KYC extraction (Phase 2 + 3) ─────────────────────────────────────────
class KycRecord(Base):
    """One row per successful KYC field extraction. Persists alongside the
    Document.extracted_fields snapshot column so we can re-run extraction
    over time (model upgrade, retry on blurry scan) and query across docs.

    `subject_pk` links to the deduplicated KycSubject identified by the
    identity stitcher. NULL until the stitcher runs for this record."""

    __tablename__ = "kyc_records"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_pk: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KycSubject(Base):
    """One row per deduplicated KYC persona — typically an end-customer
    being onboarded. The identity stitcher groups kyc_records by fuzzy
    name + exact DOB (for individuals) or registration number (for
    business entities). Status flows pending → partial → verified as the
    customer's document set grows."""

    __tablename__ = "kyc_subjects"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_dob: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="individual")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # { requirement_id_external: doc_id_external } — for the Subjects view's
    # per-subject coverage grid without re-joining.
    requirement_coverage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now()
    )


class MerchantCategoryCache(Base):
    """Per-tenant cache of merchant → expense category mappings, populated
    by the categorizer agent. Keyed by canonical form (lowercased,
    de-numbered, country-stripped) so variations of the same merchant
    collapse to one row. Lookup is O(1) by the unique index."""

    __tablename__ = "merchant_category_cache"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    merchant_canon: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FieldEdit(Base):
    """HITL edit-history row. One per manual override of a field on a
    document's extracted_fields JSONB. The document carries the LIVE
    value; this table carries the audit trail (who/when/before/after/why)."""

    __tablename__ = "field_edits"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    # Dotted path into the extracted_fields JSONB
    # e.g. "fields.total", "fields.top_transactions.0.category"
    field_path: Mapped[str] = mapped_column(String(256), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentTextOverride(Base):
    """A human-corrected full-text / Markdown override for a document (one row per doc).
    The deterministic Markdown built from the parsed chunks is the default; when a reviewer
    edits it, the corrected text lives here and is served in preference. Chunks + embeddings
    are left untouched, so retrieval / RAG are unaffected."""

    __tablename__ = "document_text_overrides"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), unique=True, nullable=False
    )
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by: Mapped[str] = mapped_column(String(256), nullable=False)
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentReview(Base):
    """Audit trail of every reviewer sign-off action on a document.
    Live state is on `documents.review_status` / `reviewed_by` /
    `reviewed_at` / `review_note`; this table is the HISTORY."""

    __tablename__ = "document_reviews"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    prior_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # M28 · learning-loop snapshot. Stores extraction confidence, the
    # active threshold, what review reasons fired, and how many HITL edits
    # the reviewer made before flipping. Used to suggest a calibrated
    # documentAutoApprove threshold over time. NULL for auto-approve rows
    # and pre-M28 rows.
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class CustomCategory(Base):
    """M28.5 · reviewer/admin-added category beyond the canonical 15+12 vocab.

    Scope: 'global' (all vendors in tenant) or 'vendor' (one vendor_pk only).
    The categorizer reads these into its system-prompt enum so the AI can
    use the new name on future docs; reviewers see them in the dropdown."""

    __tablename__ = "custom_categories"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)   # 'expense' | 'income'
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # 'global' | 'vendor'
    vendor_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vendors.pk", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ---- Third-party API clients (v1 API/SDK) -------------------------------
class ApiClient(Base):
    """A third-party integration (e.g. AuditAIQ) authorized to call the public
    API. Generalizes the single DOCAIQ_EXTRACTION_API_KEY into per-partner keys.

    The raw key is shown ONCE at creation and never stored — only `key_hash`
    (sha256) is kept, with `key_prefix` (e.g. 'dq_live_ab12…') for display. A
    request presents the key as a Bearer token (or X-API-Key); the dependency
    hashes it, looks it up here, checks scopes + rate limit, stamps last_used."""

    __tablename__ = "api_clients"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Single-tenant container (tenant_id = the documents tenant) but kept for
    # parity + future multi-tenant control planes.
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Enterprise self-serve keys are scoped to ONE user's documents (set to the creating user's pk);
    # partner/admin keys leave this NULL (tenant/group-scoped). require_client sets the owner ContextVar.
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)         # "AuditAIQ (prod)"
    key_prefix: Mapped[str] = mapped_column(String(24), index=True, nullable=False)  # shown in UI
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # sha256
    env: Mapped[str] = mapped_column(String(8), nullable=False, default="live")     # live | test
    # Granted scopes (e.g. ["extract","classify","audit:match"]). Empty = none.
    scopes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    monthly_token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Groups (shared folders) this key may query for audit evidence. NULL/[] =
    # none. A partner key (e.g. AuditAIQ) is granted a customer's group so it can
    # match requirements against exactly that shared folder — and no other.
    allowed_group_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentAnnotation(Base):
    """User-drawn highlights/boxes on a document (M53 · annotation layer).

    Coordinates are NORMALIZED 0..1 (fractions of the rendered page), so they
    re-project cleanly at any zoom/size — the frontend's FieldBoxes already
    renders the [x0,y0,x1,y1] normalized form. `captured_text` is filled at
    create time from the boxed region (PyMuPDF clip for native PDFs, region-OCR
    for scanned). Owner-scoped like documents (per-user isolation)."""
    __tablename__ = "document_annotations"
    __table_args__ = (
        Index("ix_doc_annotations_doc", "tenant_id", "document_pk"),
    )

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.pk", ondelete="CASCADE"), index=True, nullable=True
    )
    document_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.pk", ondelete="CASCADE"), index=True, nullable=False
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # normalized 0..1 region
    x0: Mapped[float] = mapped_column(Float, nullable=False)
    y0: Mapped[float] = mapped_column(Float, nullable=False)
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    captured_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
