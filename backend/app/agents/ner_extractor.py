"""General-purpose LLM NER — Foundation-Fix-B (the "universal" entity pass).

`app/entities.py` is a regex pass tuned for compliance text (money / ISO /
control-IDs / dates / email). Its own docstring flags the follow-up: *"M9 swaps
this for an LLM-driven NER step that understands semantics."* This is that step.

Where the regex pass and `graph/bootstrap.py` only surface finance/compliance
vocabulary (and bootstrap only from the 13 curated schemas' `extracted_fields`),
this reads *free text* and emits the entity kinds a **general** document carries —
people, organisations, locations, products, laws/clauses, obligations, roles —
so the graph + `search_entities` tool work on arbitrary documents, not just the
known verticals. That is the universal-analyzer unlock.

Design:
- ONE LLM call per document (chunks batched, capped) — not per chunk. Cheap tier.
- Routed through `llm.gateway` so PII redaction at the boundary applies (person
  names are intentionally NOT masked by default — they're the search key).
- **Grounded**: every returned span must be found verbatim in the source text or
  it is dropped (kills hallucinated entities), mirroring the bbox-locate discipline.
- Persisted as `Entity(source='llm_ner')` rows wrapped in a
  `GraphRun(kind='llm_entity')` so a pass is auditable + rollback-able (delete
  where graph_run_pk = X), sharing the `canonical` normalisation the graph uses.
- **B3** · the same call also returns RELATIONS between the found entities
  (directed edges, lower_snake_case slugs). A relation is kept only when BOTH
  endpoints are grounded entities, then persisted as `EntityRelation(source=
  'llm_ner')` under the same run — generalising the graph beyond bootstrap's
  finance-specific edges so `/graph/traverse` works on arbitrary corpora.

OFF by default (`DOCAIQ_NER_BACKEND=regex`); enable `llm`/`both` per the flag.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.graph.canonical import canon_date, canon_name, canon_org, money_canonical
from app.llm import gateway, ledger
from app.model_registry import REGISTRY as _AI_REGISTRY
from app.orm import Document, Entity, EntityRelation, GraphRun

log = logging.getLogger("docaiq.ner")


# ── Kind vocabulary ────────────────────────────────────────────────────────
# The finance/compliance kinds are the SAME strings the graph layer already
# uses (app/graph/bootstrap.py KIND_*), so llm_ner rows and fact_bootstrap rows
# share one enum and dedup/traversal treats them uniformly. The rest widen the
# vocabulary to general documents.
KIND_PERSON = "person"
KIND_ORG = "org"
KIND_LOCATION = "location"
KIND_DATE = "date"
KIND_MONEY = "money"
KIND_STANDARD = "standard"
KIND_IDENTIFIER = "identifier"
# New — general-document kinds.
KIND_PRODUCT = "product"
KIND_EVENT = "event"
KIND_LAW = "law_or_clause"
KIND_OBLIGATION = "obligation"
KIND_ROLE = "role"
KIND_CONTACT = "contact"
KIND_MISC = "misc"

_KINDS: list[str] = [
    KIND_PERSON, KIND_ORG, KIND_LOCATION, KIND_DATE, KIND_MONEY,
    KIND_STANDARD, KIND_IDENTIFIER, KIND_PRODUCT, KIND_EVENT, KIND_LAW,
    KIND_OBLIGATION, KIND_ROLE, KIND_CONTACT, KIND_MISC,
]

# Bound the work: one call, capped input + output.
_MAX_INPUT_CHARS = 8000   # ~2000 tokens of document text per NER call
_MAX_ENTITIES = 60        # cap output so a pathological doc can't blow the budget
_MAX_RELATIONS = 80       # relations between the found entities (B3)

# Provider-configurable model, mirroring the classifier's routing so a bare id
# keeps legacy OpenRouter routing. `or` (not getenv default) so a set-but-empty
# env from compose `${VAR:-}` still falls back to the default.
_MODEL = os.getenv("DOCAIQ_NER_MODEL") or _AI_REGISTRY["ner_extraction"].default_model
_DIRECT_PREFIXES = ("openrouter/", "dashscope/", "google/")


def _routed_model() -> str:
    return _MODEL if _MODEL.startswith(_DIRECT_PREFIXES) else f"openrouter/{_MODEL}"


def _routed_provider() -> str:
    return _routed_model().split("/", 1)[0]


# Haiku-class pricing (via OpenRouter, 2026-Q1) — same as the classifier.
_COST_IN = 1.0
_COST_OUT = 5.0


@dataclass
class NerEntity:
    kind: str
    text: str
    page: int = 1
    confidence: float | None = None
    canonical: str | None = None
    metadata: dict | None = field(default=None)


@dataclass
class NerRelation:
    """A directed edge between two entities the model found. `src`/`dst` are the
    entities' verbatim texts — resolved to Entity pks at persist time; a relation
    whose endpoints aren't both grounded entities is dropped."""
    src: str
    dst: str
    relation: str
    confidence: float | None = None


# ── Prompt ─────────────────────────────────────────────────────────────────
def _system_prompt() -> str:
    from app.llm.prompts import get_prompt
    kinds_str = "\n".join(f"  - {k}" for k in _KINDS)
    return get_prompt("ner", kinds_str=kinds_str,
                      max_entities=str(_MAX_ENTITIES),
                      max_relations=str(_MAX_RELATIONS))


def _canonical_for(kind: str, text: str) -> str | None:
    if kind == KIND_PERSON:
        return canon_name(text) or None
    if kind == KIND_ORG:
        return canon_org(text) or None
    if kind == KIND_MONEY:
        return money_canonical(text) or None
    if kind == KIND_DATE:
        return canon_date(text) or None
    return None


def _rel_slug(s: str) -> str:
    """Normalise a relation label to a short lower_snake_case slug."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:64]


def _parse(text: str) -> tuple[list[NerEntity], list[NerRelation]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # A long entity list can hit max_tokens mid-JSON; salvage the truncated
        # object with json-repair (closes open strings/brackets) rather than
        # dropping every entity for the document. Same tolerant path the vision
        # JSON calls use.
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(text))
            log.info("ner: recovered truncated/invalid JSON via json-repair")
        except Exception:  # noqa: BLE001
            log.warning("ner: response wasn't valid JSON (unrecoverable): %s · raw: %r", e, text[:200])
            return [], []
    if not isinstance(data, dict):
        return [], []
    ents: list[NerEntity] = []
    for raw in (data.get("entities") or [])[: _MAX_ENTITIES * 2]:
        kind = raw.get("kind")
        val = (raw.get("text") or "").strip()
        if kind not in _KINDS or not val:
            continue
        try:
            conf = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        ents.append(NerEntity(kind=kind, text=val[:512], confidence=conf))
    rels: list[NerRelation] = []
    for raw in (data.get("relations") or [])[: _MAX_RELATIONS * 2]:
        src = (raw.get("src") or "").strip()
        dst = (raw.get("dst") or "").strip()
        slug = _rel_slug(raw.get("relation") or "")
        if not src or not dst or not slug:
            continue
        try:
            conf = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        rels.append(NerRelation(src=src[:512], dst=dst[:512], relation=slug, confidence=conf))
    return ents, rels


# ── Pure extraction (one LLM call) ─────────────────────────────────────────
def extract_graph_llm(
    blocks: list[tuple[int, str]],
) -> tuple[list[NerEntity], list[NerRelation], dict]:
    """Run one grounded NER+relation call over a document's chunks.

    `blocks` is a list of (page, text) tuples (one per chunk). Returns
    (entities, relations, telemetry). Entities are grounded (span found verbatim
    in the source), page-attributed, deduped by (kind, canonical|lower(text)),
    and canonicalised. Relations are kept only when BOTH endpoints are grounded
    entities (B3). Telemetry is always returned so the caller writes a ledger row.
    """
    telemetry: dict = {
        "model": _MODEL, "provider": _routed_provider(),
        "input_tokens": 0, "output_tokens": 0, "latency_ms": 0,
        "status": "failed", "error": None,
    }

    # Build the capped prompt text; keep a per-page index for grounding/attribution.
    combined_parts: list[str] = []
    total = 0
    for page, txt in blocks:
        txt = (txt or "").strip()
        if not txt:
            continue
        if total + len(txt) > _MAX_INPUT_CHARS:
            txt = txt[: max(0, _MAX_INPUT_CHARS - total)]
        if txt:
            combined_parts.append(txt)
            total += len(txt)
        if total >= _MAX_INPUT_CHARS:
            break
    combined = "\n\n".join(combined_parts)
    if not combined.strip():
        telemetry["status"] = "ok"  # nothing to do, not a failure
        return [], [], telemetry

    settings = get_settings()
    provider = _routed_provider()
    missing = (
        (provider == "openrouter" and not settings.openrouter_api_key)
        or (provider == "dashscope" and not settings.dashscope_api_key)
        or (provider == "google" and not settings.google_genai_api_key)
    )
    if missing:
        log.warning("ner: no API key for provider %s; skipping NER", provider)
        telemetry["error"] = "no_api_key"
        telemetry["provider"] = "stub"
        return [], [], telemetry

    from app.db import get_current_tenant as _get_tid
    try:
        _tid = _get_tid()
    except Exception:  # noqa: BLE001
        _tid = None
    try:
        result = gateway.call(
            model=_routed_model(),
            messages=[
                gateway.Message(role="system", content=_system_prompt()),
                gateway.Message(
                    role="user",
                    content="Extract entities from this document:\n\n---\n"
                    + combined + "\n---\n\nReturn the JSON now.",
                ),
                # NOTE: single call → cheap. temperature low for stability.
            ],
            max_tokens=3000,  # headroom: a long entity+relation list truncated at 1200
            temperature=0.1,
            tenant_id=_tid,
            task_kind="ner",
        )
    except Exception as e:  # noqa: BLE001 — provider / network
        log.warning("ner: LLM call failed: %s", e)
        telemetry["error"] = str(e)[:200]
        return [], [], telemetry

    telemetry.update(
        latency_ms=int(result.latency_ms),
        input_tokens=int(result.input_tokens or 0),
        output_tokens=int(result.output_tokens or 0),
        provider=result.provider,
        status="ok",
    )

    parsed_ents, parsed_rels = _parse(result.text or "")

    # Ground + attribute + dedup. A span must appear verbatim (case-insensitive)
    # in the source, else it's a hallucination and gets dropped. Page = first
    # chunk that contains it.
    combined_lower = combined.lower()
    seen: set[tuple[str, str]] = set()
    grounded: list[NerEntity] = []
    grounded_texts: set[str] = set()  # lower(text) of every kept entity — for rel endpoints
    for e in parsed_ents:
        needle = e.text.lower()
        if needle not in combined_lower:
            continue  # not grounded → drop
        page = 1
        for pg, txt in blocks:
            if txt and needle in txt.lower():
                page = pg
                break
        canonical = _canonical_for(e.kind, e.text)
        dedup_key = (e.kind, canonical or needle)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        grounded.append(NerEntity(
            kind=e.kind, text=e.text, page=page,
            confidence=e.confidence, canonical=canonical,
        ))
        grounded_texts.add(needle)
        if len(grounded) >= _MAX_ENTITIES:
            break

    # B3 · keep only relations whose BOTH endpoints are grounded entities
    # (strongest possible check — a relation on a hallucinated node is dropped).
    # Dedup by (src, dst, relation). Self-loops dropped.
    rel_seen: set[tuple[str, str, str]] = set()
    grounded_rels: list[NerRelation] = []
    for r in parsed_rels:
        s, d = r.src.lower(), r.dst.lower()
        if s not in grounded_texts or d not in grounded_texts or s == d:
            continue
        key = (s, d, r.relation)
        if key in rel_seen:
            continue
        rel_seen.add(key)
        grounded_rels.append(r)
        if len(grounded_rels) >= _MAX_RELATIONS:
            break

    return grounded, grounded_rels, telemetry


def extract_entities_llm(
    blocks: list[tuple[int, str]],
) -> tuple[list[NerEntity], dict]:
    """Back-compat entities-only view of `extract_graph_llm` (one call, relations
    discarded). Kept for callers/tests that only want entities."""
    ents, _rels, telemetry = extract_graph_llm(blocks)
    return ents, telemetry


# ── Persistence (own GraphRun, idempotent) ─────────────────────────────────
def run(db: Session, doc: Document, blocks: list[tuple[int, str]]) -> int:
    """Extract + persist NER entities for one document. Idempotent: deletes any
    prior llm_entity run for this doc before writing. Wraps the pass in a
    GraphRun so it's auditable and rollback-able. Returns the entity count.
    Caller owns the surrounding transaction/commit (matches bootstrap.run)."""
    # Tear down any prior NER run for this doc.
    prior = db.scalars(
        select(GraphRun).where(
            GraphRun.document_pk == doc.pk,
            GraphRun.kind == "llm_entity",
        )
    ).all()
    for r in prior:
        db.execute(delete(EntityRelation).where(EntityRelation.graph_run_pk == r.pk))
        db.execute(delete(Entity).where(Entity.graph_run_pk == r.pk))
        db.delete(r)
    db.flush()

    run_row = GraphRun(
        tenant_id=doc.tenant_id,
        document_pk=doc.pk,
        kind="llm_entity",
        model=_MODEL,
        status="running",
    )
    db.add(run_row)
    db.flush()

    entities, relations, telemetry = extract_graph_llm(blocks)
    try:
        ledger.record_call(
            db,
            task="ner",
            tier="t2",
            provider=telemetry["provider"],
            model=telemetry["model"],
            input_tokens=telemetry["input_tokens"],
            output_tokens=telemetry["output_tokens"],
            cost_per_input_mtok=_COST_IN,
            cost_per_output_mtok=_COST_OUT,
            latency_ms=telemetry["latency_ms"],
            status=telemetry["status"],
            error=telemetry["error"],
        )
    except Exception:  # noqa: BLE001 — ledger is best-effort
        pass

    if telemetry["status"] != "ok":
        run_row.status = "failed"
        run_row.error = (telemetry.get("error") or "ner call failed")[:1000]
        run_row.completed_at = datetime.now(timezone.utc)
        db.flush()
        return 0

    # Persist entities, keeping a text→Entity map so relations can resolve their
    # verbatim endpoints back to the row pks.
    by_text: dict[str, Entity] = {}
    for e in entities:
        row = Entity(
            tenant_id=doc.tenant_id,
            document_pk=doc.pk,
            chunk_pk=None,
            vendor_pk=doc.vendor_pk,
            kind=e.kind,
            text=e.text[:512],
            canonical=(e.canonical or "")[:256] or None,
            page=e.page,
            entity_metadata=e.metadata,
            source="llm_ner",
            graph_run_pk=run_row.pk,
            confidence=e.confidence,
        )
        db.add(row)
        by_text[e.text.lower()] = row
    db.flush()  # populate row.pk so relations can FK to them

    # B3 · persist relations. Endpoints were already validated to be grounded
    # entities in extract_graph_llm, so both lookups resolve.
    rel_count = 0
    for r in relations:
        src_row = by_text.get(r.src.lower())
        dst_row = by_text.get(r.dst.lower())
        if src_row is None or dst_row is None:
            continue
        db.add(EntityRelation(
            tenant_id=doc.tenant_id,
            vendor_pk=doc.vendor_pk,
            src_entity_pk=src_row.pk,
            dst_entity_pk=dst_row.pk,
            relation=r.relation[:64],
            confidence=r.confidence,
            evidence_doc_pk=doc.pk,
            source="llm_ner",
            graph_run_pk=run_row.pk,
        ))
        rel_count += 1

    run_row.status = "complete"
    run_row.entities_added = len(entities)
    run_row.relations_added = rel_count
    run_row.completed_at = datetime.now(timezone.utc)
    db.flush()
    log.info(
        "ner: doc pk=%s → %d entities, %d relations (model=%s)",
        doc.pk, len(entities), rel_count, _MODEL,
    )
    return len(entities)
