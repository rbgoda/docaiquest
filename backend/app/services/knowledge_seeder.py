"""M44.P13 PR3 · Knowledge seeder — control-plane → tenant (the RECEIVE side).

Pulls the curated active global knowledge pool from the control plane and
bulk-inserts it into THIS tenant's local understanding tables as
``source='global'`` — so a freshly provisioned "vanilla data" container boots
pre-loaded with generalizable knowledge, and existing containers keep getting
smarter via the nightly sync.

Local-first: a global row is inserted ONLY when no row with the same key
already exists. A tenant's own earned (``source='local'``) knowledge always
wins and is never overwritten; re-syncing is idempotent.

Gated on ``receive_global_learning`` (consent, default on) +
``control_plane_internal_url`` (CP reachable). No-op otherwise. Runs once on
worker startup (provision-time seed) and nightly via Arq cron — never in a
request path.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orm import AgentSkillMemory, ExtractionCorrection, GeneratedSchema

log = logging.getLogger("docaiq.knowledge_seeder")


def sync_from_global(db: Session, tenant_id: str | None = None) -> dict:
    """Pull the active pool and seed local tables as source='global'. Returns
    a stats dict. No-op (with a reason) when consent is off / CP unreachable."""
    settings = get_settings()
    tid = tenant_id or settings.tenant_id

    if not settings.receive_global_learning:
        return {"status": "skipped", "reason": "receive_global_learning=false", "seeded": 0}
    cp_url = (settings.control_plane_internal_url or "").rstrip("/")
    if not cp_url:
        return {"status": "skipped", "reason": "no control_plane_internal_url", "seeded": 0}

    try:
        resp = httpx.get(f"{cp_url}/api/platform/knowledge/active", timeout=30.0)
        resp.raise_for_status()
        items = resp.json()
    except httpx.HTTPError as e:
        log.warning("knowledge sync (GET active) failed: %s", e)
        return {"status": "error", "reason": str(e)[:200], "seeded": 0}

    seeded = 0
    for it in items:
        kind = it.get("kind")
        doc_type = it.get("doc_type")
        skeleton = it.get("skeleton") or {}
        if not doc_type or not isinstance(skeleton, dict):
            continue
        if kind == "extraction_correction" and _seed_correction(db, tid, doc_type, skeleton):
            seeded += 1
        elif kind == "agent_skill" and _seed_skill(db, tid, doc_type, skeleton):
            seeded += 1
        elif kind == "generated_schema" and _seed_generated_schema(db, tid, doc_type, skeleton):
            seeded += 1

    if seeded:
        db.commit()
    log.info("knowledge sync: %s available, %s newly seeded", len(items), seeded)
    return {"status": "ok", "available": len(items), "seeded": seeded}


def _seed_correction(db: Session, tid: str, doc_type: str, skeleton: dict) -> bool:
    """Insert a global extraction_correction unless one with the same
    (doc_type, pattern_kind, wrong_field) already exists (local-first)."""
    pattern = skeleton.get("pattern")
    if not isinstance(pattern, dict) or "wrong_field" not in pattern:
        return False
    wf = pattern["wrong_field"]
    existing = db.scalar(
        select(ExtractionCorrection).where(
            ExtractionCorrection.tenant_id == tid,
            ExtractionCorrection.doc_type == doc_type,
            ExtractionCorrection.pattern_kind == "frequent_mismatch",
            ExtractionCorrection.pattern["wrong_field"].astext == wf,
        )
    )
    if existing is not None:
        return False
    db.add(ExtractionCorrection(
        tenant_id=tid, doc_type=doc_type, pattern_kind="frequent_mismatch",
        pattern=pattern, source="global",
    ))
    return True


def _seed_generated_schema(db: Session, tid: str, doc_type: str, skeleton: dict) -> bool:
    """Move-1 PR4 · seed a global crystallized schema as a PROPOSED GeneratedSchema
    (source='global') unless this tenant already has a schema for this cluster
    (local-first — a tenant's own crystallization always wins). Reconstructs the
    typed-field map from the skeleton's {label: type}. Never auto-active: a schema
    from OTHER tenants must be adopted/reviewed locally before it goes live."""
    sk_fields = skeleton.get("fields")
    if not isinstance(sk_fields, dict) or not sk_fields:
        return False
    existing = db.scalar(
        select(GeneratedSchema).where(
            GeneratedSchema.tenant_id == tid,
            GeneratedSchema.cluster_key == doc_type,
        )
    )
    if existing is not None:
        return False
    fields = {
        str(lab)[:64]: {
            "type": typ if isinstance(typ, str) else "string",
            "description": (
                f"The document's {str(lab).replace('_', ' ')} — a field crystallized "
                "across the shared knowledge pool for this document type."
            ),
        }
        for lab, typ in sk_fields.items() if lab
    }
    if not fields:
        return False
    db.add(GeneratedSchema(
        tenant_id=tid, cluster_key=doc_type,
        label=doc_type.replace("_", " ").title(),
        fields=fields, status="proposed", source="global", seen_count=0,
    ))
    return True


def _seed_skill(db: Session, tid: str, doc_type: str, skeleton: dict) -> bool:
    """Insert a global agent_skill unless one with the same
    (doc_type, question_template) already exists (local-first)."""
    qt = skeleton.get("question_template")
    ts = skeleton.get("tool_sequence")
    if not qt or not isinstance(ts, list) or not ts:
        return False
    existing = db.scalar(
        select(AgentSkillMemory).where(
            AgentSkillMemory.tenant_id == tid,
            AgentSkillMemory.doc_type == doc_type,
            AgentSkillMemory.question_template == qt,
        )
    )
    if existing is not None:
        return False
    db.add(AgentSkillMemory(
        tenant_id=tid, doc_type=doc_type, question_template=qt,
        tool_sequence=ts, source="global",
    ))
    return True
