"""M44.P13 PR2 · Knowledge promoter — tenant → control-plane contribution.

Selects this tenant's LOCAL, earned understanding rows (never seeded
`source='global'` rows — no echo loop), reduces each to a value-free skeleton
via the skeletonizer (the privacy barrier), and POSTs the batch to the
control-plane staging pool, keyed by an OPAQUE per-tenant token.

Gated on two conditions: ``contribute_learning`` (tenant consent, opt-out) and
``control_plane_internal_url`` (the CP must be reachable). When either is off
this is a no-op. Runs nightly via the Arq cron in ``app/worker.py`` — never in
a request path.

Privacy posture:
  · only ``source='local'`` rows are eligible (global rows never re-promote);
  · every row passes through ``skeletonizer.skeletonize`` — values, PII,
    identities, tenant/doc ids are stripped or the row is dropped;
  · the contribution carries an opaque token, never the slug; the global pool
    never stores identity.
"""
from __future__ import annotations

import hashlib
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orm import AgentSkillMemory, ExtractionCorrection, GeneratedSchema
from app.services import skeletonizer

log = logging.getLogger("docaiq.knowledge_promoter")


def tenant_token(tenant_id: str, salt: str = "") -> str:
    """Stable, opaque, non-identity pseudonym for a tenant. Same tenant →
    same token (so the CP can count distinct contributors); different tenants
    → different tokens; the slug is not stored in the global pool."""
    return hashlib.sha256(f"{salt}:{tenant_id}".encode()).hexdigest()[:32]


def _to_contrib(skel: dict) -> dict:
    """Reshape the skeletonizer's flat output ({kind, doc_type, <structure>})
    into the control-plane contribution shape ({kind, doc_type, skeleton:
    <structure>}) the CP's SkeletonIn model expects."""
    structure = {k: v for k, v in skel.items() if k not in ("kind", "doc_type")}
    return {"kind": skel["kind"], "doc_type": skel["doc_type"], "skeleton": structure}


def collect_skeletons(db: Session, tenant_id: str) -> list[dict]:
    """Skeletonize every promotable LOCAL understanding row for this tenant,
    in control-plane contribution shape. Rows the skeletonizer refuses
    (returns None) are silently dropped."""
    out: list[dict] = []

    for c in db.scalars(
        select(ExtractionCorrection).where(
            ExtractionCorrection.tenant_id == tenant_id,
            ExtractionCorrection.source == "local",
        )
    ).all():
        skel = skeletonizer.skeletonize(
            "extraction_correction", doc_type=c.doc_type, pattern=c.pattern
        )
        if skel is not None:
            out.append(_to_contrib(skel))

    for s in db.scalars(
        select(AgentSkillMemory).where(
            AgentSkillMemory.tenant_id == tenant_id,
            AgentSkillMemory.source == "local",
        )
    ).all():
        skel = skeletonizer.skeletonize(
            "agent_skill", doc_type=s.doc_type,
            question_template=s.question_template, tool_sequence=s.tool_sequence,
        )
        if skel is not None:
            out.append(_to_contrib(skel))

    # Move-1 PR4 · crystallized schemas. Only LOCALLY-earned ones (source=
    # 'crystallize', status='active') — never the ones seeded from the global
    # pool (source='global'), so there's no echo loop.
    for g in db.scalars(
        select(GeneratedSchema).where(
            GeneratedSchema.tenant_id == tenant_id,
            GeneratedSchema.source == "crystallize",
            GeneratedSchema.status == "active",
        )
    ).all():
        skel = skeletonizer.skeletonize(
            "generated_schema", doc_type=g.cluster_key, fields=g.fields
        )
        if skel is not None:
            out.append(_to_contrib(skel))

    return out


def promote_to_global(db: Session, tenant_id: str | None = None) -> dict:
    """Collect + contribute this tenant's skeletons to the control plane.
    Returns a stats dict (observable in the Arq result store). No-op (with a
    reason) when consent is off or the CP is unreachable."""
    settings = get_settings()
    tid = tenant_id or settings.tenant_id

    if not settings.contribute_learning:
        return {"status": "skipped", "reason": "contribute_learning=false", "contributed": 0}
    cp_url = (settings.control_plane_internal_url or "").rstrip("/")
    if not cp_url:
        return {"status": "skipped", "reason": "no control_plane_internal_url", "contributed": 0}

    skeletons = collect_skeletons(db, tid)
    if not skeletons:
        return {"status": "ok", "reason": "nothing promotable", "contributed": 0}

    token = tenant_token(tid, settings.knowledge_token_salt)
    payload = {"tenant_token": token, "skeletons": skeletons}
    try:
        resp = httpx.post(
            f"{cp_url}/api/platform/knowledge/contrib",
            json=payload, timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as e:
        log.warning("knowledge contrib failed: %s", e)
        return {"status": "error", "reason": str(e)[:200], "contributed": 0}

    log.info("knowledge contrib: %s skeletons → accepted=%s rejected=%s",
             len(skeletons), body.get("accepted"), body.get("rejected"))
    return {
        "status": "ok",
        "collected": len(skeletons),
        "contributed": body.get("accepted", 0),
        "rejected": body.get("rejected", 0),
    }
