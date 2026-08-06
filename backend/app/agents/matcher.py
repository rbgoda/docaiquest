"""Matcher agent.

Closes the loop between ingestion (M7), retrieval (M8), and validation (M9):
given a freshly-ingested document, walk every open requirement in the tenant,
ask the validator whether the document satisfies it, and auto-attach when the
validator returns a high-confidence "yes."

Per-tenant scope, single-pass, idempotent:

  * Candidates = `Requirement` rows where `doc_id_external IS NULL` AND
    `status IN ('todo','warn','miss')`. Already-matched and OK requirements
    are left alone — re-verifying is a separate flow we don't need yet.
  * For each candidate, we retrieve the top chunks from the candidate
    document only (`retrieve(..., doc_id_external=doc.id_external)`), then
    run the validator with the requirement title/subtitle as the query.
  * Auto-attach (`doc_id_external = doc.id_external`, `status = 'ok'`)
    when validator confidence ≥ tenant's `thresholds.autoApprove`
    (default 0.85). Below that we currently leave the requirement
    untouched; sub-threshold candidates table is M11.5.

Cost note: this is N validator calls per upload, where N is the candidate
count. On the seeded fixture N≈5; on a real tenant with hundreds of open
requirements we'd want to (a) coarse-filter by retrieval score before
spending the LLM call, and (b) parallelize. Both are follow-ups; this MVP
runs sequentially so we can debug the wiring first.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.structured_match import check as structured_check
from app.agents.validator import validate
from app.db import set_current_tenant
from app.orm import (
    AuditRun,
    AuditRunRequirement,
    AuditSubject,
    ChatMessage,
    Document,
    DocumentChunk,
    Entity,
    Requirement,
)
from app.llm.prompts import get_prompt
from app.repositories import routing_configs as rc_repo
from app.retrieval import retrieve

log = logging.getLogger("docaiq.agents.matcher")

# Matcher-specific system prompt. Critical difference from the chat validator:
# here `confidence` is `P(document satisfies requirement)`, NOT "how grounded
# is my answer." If the model says "no, the doc doesn't establish this,"
# confidence MUST be near zero. Reusing the chat prompt caused a false
# positive where a confident "no" came back at 0.88 and got auto-attached.
@dataclass
class MatchDecision:
    requirement_id_external: str
    requirement_title: str
    confidence: float | None
    action: str  # "attached" | "below_threshold" | "no_evidence" | "validator_failed"
    answer_excerpt: str


def _append_evidence(
    db: Session,
    tenant_id: str,
    requirement_pk: int,
    doc_id_external: str,
    confidence: float | None,
    *,
    source: str = "ai",
    attached_by: str = "ai",
) -> None:
    """M31.6 · append a doc to evidence_docs[] on every audit_run_requirements
    row for this requirement. Idempotent — re-running the matcher on the
    same doc doesn't duplicate entries. Each entry:
      {doc_id, confidence, attached_at (ISO UTC), attached_by, source}
    """
    from datetime import datetime, timezone
    arr_rows = db.scalars(
        select(AuditRunRequirement).where(
            AuditRunRequirement.tenant_id == tenant_id,
            AuditRunRequirement.requirement_pk == requirement_pk,
        )
    ).all()
    if not arr_rows:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    for arr in arr_rows:
        existing = list(arr.evidence_docs or [])
        # Skip if this doc is already in the list.
        if any(isinstance(e, dict) and e.get("doc_id") == doc_id_external for e in existing):
            continue
        existing.append({
            "doc_id": doc_id_external,
            "confidence": confidence,
            "attached_at": now_iso,
            "attached_by": attached_by,
            "source": source,
        })
        arr.evidence_docs = existing


_CHUNK_ID_RE = re.compile(r"chunk-(\d+)")


def _post_auto_attach_citation_message(
    db: Session,
    *,
    tenant_id: str,
    requirement_id_external: str,
    doc: Document,
    confidence: float | None,
    answer_excerpt: str,
    cited_chunk_ids: list[str],
    hit_chunk_pks: list[int],
) -> None:
    """M40 · Phase D · post an initial AI ChatMessage on auto-attach so the
    Review screen's doc-viewer renders citation boxes immediately without
    requiring the reviewer to type the first chat question.

    Citation chunk_pks come first from the validator's parsed citations
    (chunks the answer text actually referenced), and fall back to the
    retrieval hits if the answer didn't enumerate any. Bbox is read from
    DocumentChunk.bbox (PyMuPDF normalized coords from ingestion).

    Idempotent — if a matcher-authored auto-attach message already exists
    for this (req, doc) pair, skip. Otherwise re-running the matcher would
    duplicate the boxes on the reviewer's screen.
    """
    # Avoid creating a duplicate when re-running the matcher (e.g. after a
    # routing-config tune that re-fires the cascade). The `meta` marker
    # `auto-attach:<doc_id_external>` is unique per (req, doc).
    marker = f"auto-attach:{doc.id_external}"
    existing = db.scalar(
        select(ChatMessage).where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.requirement_id_external == requirement_id_external,
            ChatMessage.meta == marker,
        )
    )
    if existing is not None:
        return

    # Resolve chunk_pks: prefer validator's cited "chunk-N" tokens; fall
    # back to retrieval hit pks if the answer didn't enumerate any (the LLM
    # sometimes summarizes without inline citation markers).
    cited_pks: list[int] = []
    for c in cited_chunk_ids:
        m = _CHUNK_ID_RE.search(c)
        if m:
            cited_pks.append(int(m.group(1)))
    if not cited_pks:
        # Top 3 hits — keep the overlay readable; more pins crowd the page.
        cited_pks = list(hit_chunk_pks[:3])
    if not cited_pks:
        return

    chunks = db.scalars(
        select(DocumentChunk).where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.pk.in_(cited_pks),
            DocumentChunk.document_pk == doc.pk,  # don't cite cross-doc chunks
        )
    ).all()
    if not chunks:
        return

    citations = [
        {
            "chunk_pk": c.pk,
            "page": c.page,
            "bbox": c.bbox,  # may be None for vision-OCR chunks; FE falls back
            "quote": (c.text or "")[:240],
        }
        for c in chunks
    ]
    conf_str = f"{confidence:.2f}" if confidence is not None else "—"
    text = (
        f"Auto-matched at confidence {conf_str} based on the highlighted "
        f"evidence. "
    )
    if answer_excerpt:
        text += answer_excerpt
    db.add(
        ChatMessage(
            tenant_id=tenant_id,
            requirement_id_external=requirement_id_external,
            doc_id_external=None,  # requirement-scoped, not doc-chat
            role="ai",
            text=text,
            citations=citations,
            confidence=confidence,
            meta=marker,
        )
    )


def _all_unattached_in_tenant(db: Session, tenant_id: str) -> list[Requirement]:
    """Legacy candidate pool — every open req in the tenant. Used only when
    a doc has no vendor_pk (admin manual ingest, legacy seed)."""
    return db.scalars(
        select(Requirement)
        .where(
            Requirement.tenant_id == tenant_id,
            Requirement.doc_id_external.is_(None),
            Requirement.status.in_(("todo", "warn", "miss")),
        )
        .order_by(Requirement.pk)
    ).all()


def match_document(db: Session, document_pk: int, tenant_id: str) -> list[MatchDecision]:
    """Run the matcher for a single document. Returns one decision per
    candidate requirement evaluated (skipped reqs are not included)."""
    set_current_tenant(tenant_id)

    doc = db.scalar(
        select(Document).where(Document.tenant_id == tenant_id, Document.pk == document_pk)
    )
    if doc is None:
        raise RuntimeError(f"matcher: document pk={document_pk} not found in tenant {tenant_id}")
    if doc.ingestion_status != "ready":
        log.info("matcher: doc pk=%s not ready (status=%s); skipping", doc.pk, doc.ingestion_status)
        return []

    # M31.2.2 · Scope candidate reqs to audits that involve THIS doc's vendor.
    # Previously we evaluated every open req in the tenant, so a Singaporean
    # passport landed on a Brazil-country KYC req that wasn't in the doc's
    # audit. Now: if the doc has vendor_pk, candidates = reqs attached to
    # any audit_run whose vendor matches. Fallback to all-tenant only when
    # the doc lacks a vendor_pk (rare — legacy uploads or admin manual ingest).
    if doc.vendor_pk is not None:
        # Look up the vendor's name (audit_runs.vendor is denormalised string).
        from app.orm import Vendor as VendorORM
        vendor_row = db.scalar(
            select(VendorORM).where(VendorORM.tenant_id == tenant_id, VendorORM.pk == doc.vendor_pk)
        )
        vendor_name = vendor_row.name if vendor_row else None
        if vendor_name:
            # Find req PKs across all audits for this vendor — open AND closed.
            scoped_pks = db.scalars(
                select(AuditRunRequirement.requirement_pk)
                .join(AuditRun, AuditRun.pk == AuditRunRequirement.audit_run_pk)
                .where(
                    AuditRunRequirement.tenant_id == tenant_id,
                    AuditRun.tenant_id == tenant_id,
                    AuditRun.vendor == vendor_name,
                )
                .distinct()
            ).all()
            # M31.6 · Include reqs that ARE already attached (to a different
            # doc) so the matcher can record THIS doc as additional evidence
            # via evidence_docs[]. Previously the filter required
            # doc_id_external IS NULL, which meant a second-uploaded doc was
            # never considered for an already-attached req — even if it
            # legitimately backed the same control (passport + Aadhar both
            # carry DOB).
            unattached = db.scalars(
                select(Requirement)
                .where(
                    Requirement.tenant_id == tenant_id,
                    Requirement.status.in_(("todo", "warn", "miss", "ok")),
                    Requirement.pk.in_(scoped_pks) if scoped_pks else False,
                )
                .order_by(Requirement.pk)
            ).all() if scoped_pks else []
            log.info(
                "matcher: scoped to vendor %r → %d candidate req(s) (was: all tenant)",
                vendor_name, len(unattached),
            )
        else:
            # vendor_pk set but vendor row missing — fall through to broad scan.
            unattached = _all_unattached_in_tenant(db, tenant_id)
    else:
        # No vendor context — fall back to all-tenant (legacy + admin uploads).
        unattached = _all_unattached_in_tenant(db, tenant_id)
    pinned_to_this_doc = db.scalars(
        select(Requirement)
        .where(
            Requirement.tenant_id == tenant_id,
            Requirement.doc_id_external == doc.id_external,
        )
        .order_by(Requirement.pk)
    ).all()
    candidates = unattached + pinned_to_this_doc
    pinned_pks = {r.pk for r in pinned_to_this_doc}

    if not candidates:
        log.info("matcher: no candidate requirements for doc pk=%s", doc.pk)
        return []

    cfg = rc_repo.get(db) or {}
    auto_threshold = float(cfg.get("thresholds", {}).get("autoApprove", 0.85))
    log.info("matcher: evaluating %d candidate(s) for doc pk=%s (threshold=%.2f)",
             len(candidates), doc.pk, auto_threshold)

    # M31.2.3 / T2.2 · Graph-aware matching. The graph bootstrap pass
    # extracts typed entities from this doc (persons, dates, identifiers,
    # locations, etc) and stores them in the `entities` table. The
    # matcher uses this in two ways:
    #
    #   1. Person names → fallback for subject precheck (when the KYC
    #      field extractor returned empty extracted_fields).
    #   2. Full entity summary → injected into the LLM's user_message
    #      as "WHAT WE KNOW about this document", so the validator
    #      doesn't have to re-derive facts from raw chunks.
    all_entities = db.scalars(
        select(Entity).where(
            Entity.tenant_id == tenant_id,
            Entity.document_pk == doc.pk,
        )
    ).all()
    doc_person_names = [e.text for e in all_entities if e.kind == "person" and e.text]
    # Group entities by kind for compact summary in the LLM prelude.
    by_kind: dict[str, list[str]] = {}
    for e in all_entities:
        if e.text:
            by_kind.setdefault(e.kind, []).append(e.text)
    # Keep summary compact: cap to 5 distinct values per kind.
    graph_summary_lines: list[str] = []
    for kind in ("person", "org", "location", "date", "identifier", "money", "standard"):
        vals = by_kind.get(kind, [])
        if not vals:
            continue
        # Dedupe preserving order (case-insensitive).
        seen = set()
        uniq = []
        for v in vals:
            k = v.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(v)
            if len(uniq) >= 5:
                break
        graph_summary_lines.append(f"  {kind}: {', '.join(uniq)}")
    graph_summary = "\n".join(graph_summary_lines)
    if doc_person_names:
        log.info("matcher: doc pk=%s · graph persons: %s", doc.pk, doc_person_names)
    if graph_summary:
        log.debug("matcher: doc pk=%s · graph summary:\n%s", doc.pk, graph_summary)

    decisions: list[MatchDecision] = []
    for req in candidates:
        # M31.2 · Look up audit subjects bound to this requirement. A req
        # can appear in multiple audits; collect every distinct subject
        # name AND every alias across all audits this req is attached to.
        # When non-empty, the structured precheck rejects docs whose
        # extracted name doesn't match any, AND the LLM gets the names
        # in its user_message.
        subject_rows = db.scalars(
            select(AuditSubject)
            .join(AuditRunRequirement, AuditRunRequirement.audit_run_pk == AuditSubject.audit_run_pk)
            .where(
                AuditSubject.tenant_id == tenant_id,
                AuditRunRequirement.tenant_id == tenant_id,
                AuditRunRequirement.requirement_pk == req.pk,
            )
        ).all()
        req_subjects: list[str] = []
        for s in subject_rows:
            if s.name:
                req_subjects.append(s.name)
            for alias in (s.aliases or []):
                if isinstance(alias, str) and alias.strip():
                    req_subjects.append(alias.strip())
        # Dedupe preserving order.
        seen_lower = set()
        req_subjects = [
            n for n in req_subjects
            if not (n.lower() in seen_lower or seen_lower.add(n.lower()))
        ]

        # Structured precheck — runs before the LLM call. Kills false
        # positives (wrong country, wrong period, wrong doc family, wrong
        # subject) cheaply without spending a validator round-trip.
        doc_fields = ((doc.extracted_fields or {}).get("fields")
                      if doc.extracted_fields else None)
        req_text = ". ".join(p for p in [
            req.title, req.subtitle or "", " ".join(req.required_docs or []),
        ] if p)
        precheck = structured_check(
            req_text, doc_fields, doc.doc_type, req.group,
            subjects=req_subjects,
            doc_person_names=doc_person_names,
        )
        if not precheck.pass_:
            log.info("matcher: precheck rejected req=%s doc=%s · %s",
                     req.id_external, doc.id_external, precheck.constraint)
            decisions.append(MatchDecision(
                requirement_id_external=req.id_external,
                requirement_title=req.title,
                confidence=None,
                action="below_threshold",
                answer_excerpt=(precheck.reason or "")[:240],
            ))
            continue

        # Retrieval query · title + subtitle by default. If admin set a
        # custom match_prompt, append it so the retriever surfaces chunks
        # relevant to the specific question being asked (e.g. asking about
        # "Big 4 firm" pulls in chunks mentioning the firm name).
        query_parts = [req.title, req.subtitle or ""]
        if req.match_prompt:
            query_parts.append(req.match_prompt)
        query = ". ".join(p for p in query_parts if p).strip()
        # Pre-check: doc-scoped retrieval. No chunks → no evidence → skip the
        # LLM call entirely. Cheap and avoids wasting free-tier quota.
        hits = retrieve(db, query, doc_id_external=doc.id_external, top_k=4)
        if not hits:
            decisions.append(MatchDecision(
                requirement_id_external=req.id_external,
                requirement_title=req.title,
                confidence=None,
                action="no_evidence",
                answer_excerpt="",
            ))
            continue

        # Reframe as a yes/no question for the validator. Its system prompt
        # already enforces evidence-grounded prose + a Confidence: 0.XX line.
        # Admin-supplied `match_prompt` overrides the default template
        # verbatim — lets admins tune precision per control (e.g. "Must
        # explicitly state MFA AND specify a cadence"). When NULL, the
        # generic template runs.
        subject_note = ""
        if req_subjects:
            names = ", ".join(f"\"{n}\"" for n in req_subjects)
            subject_note = (
                f"\n\nSUBJECT CONSTRAINT — This audit is for: {names}. "
                "The document MUST pertain to one of these named persons. "
                "If the document carries a different person's name, answer NO."
            )
        # T2.2 · Inject graph-derived structured facts so the LLM doesn't
        # have to rediscover them from raw chunks. The validator's system
        # prompt still requires citations from chunks; the WHAT-WE-KNOW
        # block is reference info, not a substitute for evidence.
        graph_block = ""
        if graph_summary:
            graph_block = (
                "\n\nWHAT WE KNOW about this document (from prior extraction · "
                "for reference only, still cite chunks for any claim):\n"
                f"{graph_summary}"
            )
        if req.match_prompt and req.match_prompt.strip():
            user_message = (
                f"Requirement: \"{req.title}\"\n\n"
                f"Admin guidance for this match: {req.match_prompt.strip()}"
                f"{subject_note}"
                f"{graph_block}\n\n"
                "Answer based ONLY on the evidence excerpts. Be strict — say no "
                "if the excerpts do not satisfy the requirement under the guidance above."
            )
        else:
            user_message = (
                f"Does this document satisfy the requirement \"{req.title}\"?"
                f"{subject_note}"
                f"{graph_block} "
                "Answer based ONLY on the evidence excerpts. Be strict — say no "
                "if the excerpts do not directly establish the requirement."
            )

        try:
            response = validate(
                db,
                user_message=user_message,
                requirement_id_external=req.id_external,
                requirement_title=req.title,
                top_k=4,
                doc_id_external=doc.id_external,
                system_prompt=get_prompt("matcher"),
            )
        except Exception as e:
            log.warning("matcher: validator failed for %s: %s", req.id_external, e)
            decisions.append(MatchDecision(
                requirement_id_external=req.id_external,
                requirement_title=req.title,
                confidence=None,
                action="validator_failed",
                answer_excerpt=str(e)[:200],
            ))
            continue

        conf = response.confidence
        excerpt = (response.answer or "")[:240]

        if req.pk in pinned_pks:
            # Pre-linked via per-row Upload — record confidence so the UI
            # shows the badge, but don't reset status (vendor's intent
            # stands until a reviewer acts on it).
            req.confidence = conf
            action = "attached" if (conf is not None and conf >= auto_threshold) else "pre_linked"
        elif conf is not None and conf >= auto_threshold:
            # M31.6 · Multi-evidence. If req.doc_id_external is already set,
            # keep the existing primary (first/best) but ALSO append this
            # doc to evidence_docs of every audit_run_requirements row for
            # this req. That way both passport + Aadhar can back DOB even
            # though only one is the 'primary' for the legacy UI column.
            if not req.doc_id_external:
                req.doc_id_external = doc.id_external
                req.status = "ok"
                req.confidence = conf
            _append_evidence(db, tenant_id, req.pk, doc.id_external, conf, source="ai")
            # M40 · Phase D · post an initial AI ChatMessage with bbox-bearing
            # citations so the Review screen's doc viewer renders yellow boxes
            # on the cited evidence immediately — no need for the reviewer to
            # ask a chat question to see what the AI matched on.
            try:
                _post_auto_attach_citation_message(
                    db,
                    tenant_id=tenant_id,
                    requirement_id_external=req.id_external,
                    doc=doc,
                    confidence=conf,
                    answer_excerpt=excerpt,
                    cited_chunk_ids=response.citations or [],
                    hit_chunk_pks=[h.chunk_pk for h in response.hits],
                )
            except Exception as e:
                # Non-fatal — the attach succeeded; only the visual citation
                # boxes are missing. Log and continue so one bad chunk lookup
                # doesn't roll back a clean match.
                log.warning("matcher: failed to post auto-attach citation message for %s: %s",
                            req.id_external, e)
            action = "attached"
        else:
            action = "below_threshold"

        decisions.append(MatchDecision(
            requirement_id_external=req.id_external,
            requirement_title=req.title,
            confidence=conf,
            action=action,
            answer_excerpt=excerpt,
        ))

    db.commit()
    attached = sum(1 for d in decisions if d.action == "attached")
    log.info("matcher: doc pk=%s — evaluated %d, attached %d", doc.pk, len(decisions), attached)

    # KYC extraction chain (Phase 1, 2026-05-18). If the matcher attached
    # this doc to any KYC-* requirement, run the typed-field extractor
    # via vision so the document's structured fields land in
    # `extracted_fields`. Runs after commit so matcher work is durable
    # even if the extractor flakes (rate-limited, network blip, etc.).
    try:
        _maybe_extract_kyc_fields(db, doc, decisions)
    except Exception as e:
        log.warning("matcher: KYC extraction chain raised for doc pk=%s: %s", doc.pk, e)

    return decisions


def _maybe_extract_kyc_fields(db: Session, doc: Document, decisions: list[MatchDecision]) -> None:
    """If the doc was attached to a KYC-* requirement, run the vision
    extractor, persist:
      1. the typed fields on `documents.extracted_fields` (snapshot)
      2. a row in `kyc_records` (durable, queryable, re-runnable)
      3. update the linked `kyc_subjects` row via the identity stitcher
    Only the first KYC attachment is extracted; the doc represents one
    physical thing (a passport, an Aadhaar, a utility bill) → one schema."""
    # Lazy imports so the matcher path stays working when extraction
    # dependencies aren't configured (no OpenRouter key, etc).
    from app.agents.kyc_extractor import (
        KYC_REQUIREMENT_TO_DOC_TYPE,
        extract,
        result_to_jsonb,
    )
    from app.repositories import kyc as kyc_repo
    from app.identity_stitcher import stitch

    if not doc.s3_key:
        return
    for d in decisions:
        if d.action != "attached":
            continue
        doc_type = KYC_REQUIREMENT_TO_DOC_TYPE.get(d.requirement_id_external)
        if doc_type is None:
            continue
        log.info(
            "matcher: chaining KYC extractor for doc pk=%s requirement=%s doc_type=%s",
            doc.pk, d.requirement_id_external, doc_type,
        )
        result = extract(s3_key=doc.s3_key, mime=doc.mime_type or "", doc_type=doc_type)
        if result is None:
            return

        # 1. Snapshot on the document row (kept for back-compat)
        doc.extracted_fields = result_to_jsonb(result)

        # 2. Durable kyc_records row
        record = kyc_repo.insert_record(
            db,
            document_pk=doc.pk,
            doc_type=result.doc_type,
            fields=result.fields,
            confidence=result.confidence,
            model=result.model,
            notes=result.notes,
        )

        # 3. Identity stitcher → find or create KycSubject
        try:
            stitch(db, record, requirement_id_external=d.requirement_id_external)
        except Exception as e:
            # Stitcher is best-effort; don't break the extraction chain if
            # name/DOB parsing hiccups. The record stays unstitched and
            # the reviewer can reconcile manually later.
            log.warning("matcher: identity stitcher raised for record %s: %s", record.pk, e)

        db.commit()
        log.info(
            "matcher: KYC extraction persisted for doc pk=%s — record %s, confidence=%.2f, %d fields",
            doc.pk, record.pk, result.confidence, len(result.fields),
        )
        return  # only extract once per doc
