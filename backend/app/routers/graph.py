"""Graph RAG query API (L3 · per-vendor scoped).

All endpoints are tenant-scoped through the standard middleware, and
default to per-vendor scoping when a `vendor_pk` query param is set. The
DocumentChatPanel / VendorPortal pass the active vendor's pk so each
reviewer sees only the subgraph they're working with.

Endpoints
---------
GET  /api/graph/entities
     ?vendor_pk=...&kind=...&q=...   list / search canonical names
GET  /api/graph/relations
     ?vendor_pk=...&relation=...&entity_pk=...
GET  /api/graph/document/{doc_id}    local subgraph for one doc
GET  /api/graph/traverse
     ?from_entity_pk=...&relation=...&direction=out|in&depth=N
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.graph import insights as graph_insights, reconcile as graph_reconcile, resolve as graph_resolve
from app.db import get_current_tenant
from app.documents_scope import get_current_owner_user_pk
from app.orm import Document, Entity, EntityRelation
from app.security import CurrentUser, get_current_user


def _owned_doc_pks_subq():
    """Subquery of Document.pk owned by the current documents-product user, or
    None in the auditing product (owner scope unset → no extra filter).

    M46 isolation hardening: every documents user holds the `owner` role and
    shares one stack, so RBAC gives no inter-user separation. Entities/relations
    carry a document FK, so restricting graph reads to the caller's own
    documents keeps one user's extracted entities (names, national-IDs, money,
    dates) and reasoning out of another user's view. No-op for auditing.
    """
    uid = get_current_owner_user_pk()
    if uid is None:
        return None
    return select(Document.pk).where(Document.owner_user_id == uid)

_VALID_DIRECTIONS = ("out", "in", "both")

router = APIRouter()


def _entity_to_dict(e: Entity) -> dict[str, Any]:
    return {
        "pk": e.pk,
        "kind": e.kind,
        "text": e.text,
        "canonical": e.canonical,
        "page": e.page,
        "vendorPk": e.vendor_pk,
        "documentPk": e.document_pk,
        "metadata": e.entity_metadata,
        "source": e.source,
        "confidence": e.confidence,
    }


def _relation_to_dict(r: EntityRelation) -> dict[str, Any]:
    return {
        "pk": r.pk,
        "relation": r.relation,
        "srcEntityPk": r.src_entity_pk,
        "dstEntityPk": r.dst_entity_pk,
        "vendorPk": r.vendor_pk,
        "confidence": r.confidence,
        "evidenceDocPk": r.evidence_doc_pk,
        "evidenceChunkPk": r.evidence_chunk_pk,
        "metadata": r.metadata_json,
        "source": r.source,
    }


@router.get("/entities")
def list_entities(
    vendor_pk: int | None = Query(None, description="Filter to one vendor's subgraph"),
    kind: str | None = Query(None, description="person | org | money | date | location | standard | document | identifier | transaction | category"),
    q: str | None = Query(None, description="Substring match on canonical / text"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    tid = get_current_tenant()
    stmt = (
        select(Entity)
        .where(Entity.tenant_id == tid, Entity.deprecated_at.is_(None))
        .order_by(Entity.kind, Entity.canonical.nulls_last(), Entity.pk)
    )
    if vendor_pk is not None:
        stmt = stmt.where(Entity.vendor_pk == vendor_pk)
    if kind:
        stmt = stmt.where(Entity.kind == kind)
    # Keyword-search fallback state (populated when q is set; used below
    # to add synthetic entries for docs with zero real entity rows).
    kw_doc_pks: list[int] = []
    kw_doc_info: dict[int, dict] = {}
    if q:
        needle = f"%{q.lower()}%"
        # Primary match: entity canonical / text contains the query.
        entity_match = (Entity.canonical.ilike(needle)) | (Entity.text.ilike(needle))
        # Secondary match: documents found by the shared keyword search
        # (extracted_fields JSONB + chunk text + doc names) — same search
        # Content and Chat use.  Some documents match the keyword search but
        # have ZERO entity rows (NER didn't extract anything classifiable);
        # we add synthetic entries for those so the entity count matches.
        from app.services.document_search import keyword_search_documents
        uid = get_current_owner_user_pk()
        if uid is not None:
            kw_results = keyword_search_documents(db, q, tenant_id=tid, owner_user_id=uid)
            kw_doc_pks = [r["pk"] for r in kw_results]
            kw_doc_info = {r["pk"]: r for r in kw_results}
        if kw_doc_pks:
            stmt = stmt.where(entity_match | (Entity.document_pk.in_(kw_doc_pks)))
        else:
            stmt = stmt.where(entity_match)
    _owned = _owned_doc_pks_subq()
    if _owned is not None:
        stmt = stmt.where(Entity.document_pk.in_(_owned))
    rows = list(db.scalars(stmt.limit(limit)).all())
    out = [_entity_to_dict(e) for e in rows]

    # ── Synthetic entries for keyword-matched docs with no real entities ──
    if q and kw_doc_pks:
        returned_pks = {e.document_pk for e in rows}
        for pk in kw_doc_pks:
            if pk not in returned_pks:
                info = kw_doc_info.get(pk, {})
                out.append({
                    "pk": None,
                    "kind": "document",
                    "text": q,
                    "canonical": q,
                    "page": info.get("page"),
                    "vendorPk": None,
                    "documentPk": pk,
                    "metadata": {"snippet": info.get("snippet", ""),
                                 "docName": info.get("name", ""),
                                 "source": "keyword_search"},
                    "source": "keyword_search",
                    "confidence": None,
                })
    return out


@router.get("/entity-profile")
def entity_profile(
    q: str = Query(..., min_length=1, description="Entity name to profile (e.g. a person or org)"),
    kind: str | None = Query(None, description="Restrict to a kind: person | org | …"),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Cross-document intelligence profile for one entity: resolves the query to a
    unified identity (merging spelling/word-order variants across documents) and
    aggregates its footprint — the documents it appears in, its co-occurring
    people/orgs (network), a date timeline, and associated amounts/identifiers/
    roles. Owner-scoped. `found: false` when nothing in the corpus matches."""
    from app.services import entity_profile as _ep
    prof = _ep.build_profile(db, q, kind=kind)
    if prof is None:
        return {"found": False, "query": q}
    return {"found": True, "query": q, **prof}


@router.get("/identities")
def list_identities(
    kind: str | None = Query(None, description="person | org"),
    q: str | None = Query(None, description="Substring match on the display name / aliases"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """The durable cross-document entity directory — every resolved person/org across
    the caller's documents, with its alias variants and document count. Owner-scoped."""
    from app.orm import EntityIdentity
    stmt = select(EntityIdentity).where(EntityIdentity.tenant_id == get_current_tenant())
    uid = get_current_owner_user_pk()
    stmt = (stmt.where(EntityIdentity.owner_user_id == uid) if uid is not None
            else stmt.where(EntityIdentity.owner_user_id.is_(None)))
    if kind:
        stmt = stmt.where(EntityIdentity.kind == kind)
    if q:
        stmt = stmt.where(EntityIdentity.display_name.ilike(f"%{q.lower()}%"))
    stmt = stmt.order_by(EntityIdentity.mention_count.desc()).limit(limit)
    rows = db.scalars(stmt).all()
    return [{"name": r.display_name, "kind": r.kind, "docCount": len(r.doc_pks or []),
             "mentions": r.mention_count, "aliases": r.aliases or []} for r in rows]


@router.get("/relations")
def list_relations(
    vendor_pk: int | None = Query(None),
    relation: str | None = Query(None),
    entity_pk: int | None = Query(None, description="Filter to edges touching this entity (either side)"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    tid = get_current_tenant()
    stmt = (
        select(EntityRelation)
        .where(EntityRelation.tenant_id == tid, EntityRelation.deprecated_at.is_(None))
        .order_by(EntityRelation.relation, EntityRelation.pk)
    )
    if vendor_pk is not None:
        stmt = stmt.where(EntityRelation.vendor_pk == vendor_pk)
    if relation:
        stmt = stmt.where(EntityRelation.relation == relation)
    if entity_pk is not None:
        stmt = stmt.where(
            (EntityRelation.src_entity_pk == entity_pk)
            | (EntityRelation.dst_entity_pk == entity_pk)
        )
    _owned = _owned_doc_pks_subq()
    if _owned is not None:
        stmt = stmt.where(EntityRelation.evidence_doc_pk.in_(_owned))
    rows = db.scalars(stmt.limit(limit)).all()
    return [_relation_to_dict(r) for r in rows]


@router.get("/document/{doc_id}")
def document_subgraph(
    doc_id: str,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Local subgraph for one document — all entities created from it
    plus every edge whose evidence_doc_pk matches. Used by the Graph
    tab in DocumentChatPanel to render a force-directed view per doc."""
    tid = get_current_tenant()
    _doc_stmt = select(Document).where(
        Document.tenant_id == tid,
        Document.id_external == doc_id,
    )
    _uid = get_current_owner_user_pk()
    if _uid is not None:  # M46 · documents product · own docs only
        _doc_stmt = _doc_stmt.where(Document.owner_user_id == _uid)
    doc = db.scalar(_doc_stmt)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    entities = db.scalars(
        select(Entity)
        .where(Entity.tenant_id == tid, Entity.document_pk == doc.pk, Entity.deprecated_at.is_(None))
        .order_by(Entity.pk)
    ).all()
    relations = db.scalars(
        select(EntityRelation)
        .where(
            EntityRelation.tenant_id == tid,
            EntityRelation.evidence_doc_pk == doc.pk,
            EntityRelation.deprecated_at.is_(None),
        )
        .order_by(EntityRelation.pk)
    ).all()
    return {
        "docId": doc_id,
        "docPk": doc.pk,
        "entities": [_entity_to_dict(e) for e in entities],
        "relations": [_relation_to_dict(r) for r in relations],
    }


@router.get("/traverse")
def traverse(
    from_entity_pk: int = Query(..., description="Seed entity pk"),
    relation: str | None = Query(None, description="Restrict to one relation slug"),
    direction: str = Query("out", description="out | in | both"),
    depth: int = Query(2, ge=1, le=5),
    vendor_pk: int | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Recursive-CTE traversal from a seed entity. Returns the set of
    entities reachable in ≤ depth hops + every edge traversed.

    Example: from a Person, follow `signed_by` outbound depth=2 to land
    on every Document they signed plus the Dates / Money attached to
    those docs. The chat-side query planner uses this for graph-walk
    queries.
    answers ('every agreement Goda signed in 2026 with effective date')."""
    if direction not in _VALID_DIRECTIONS:
        raise HTTPException(status_code=422, detail="direction must be out, in, or both")
    # Tenant scoping: seed must belong to the caller's tenant. NEVER trust
    # seed.tenant_id from the looked-up row — that would let a logged-in
    # user pass another tenant's entity pk and run the CTE against that
    # tenant's data.
    tid = get_current_tenant()
    _seed_stmt = select(Entity).where(Entity.tenant_id == tid, Entity.pk == from_entity_pk, Entity.deprecated_at.is_(None))
    _owned = _owned_doc_pks_subq()
    if _owned is not None:  # M46 · documents product · own docs only
        _seed_stmt = _seed_stmt.where(Entity.document_pk.in_(_owned))
    seed = db.scalar(_seed_stmt)
    if seed is None:
        raise HTTPException(status_code=404, detail=f"Entity pk={from_entity_pk} not found")

    # vendor_pk is bound as a real SQL parameter (not f-string-interpolated)
    # so the integer cast can't be sidestepped if the column ever changes type.
    vendor_filter = "AND er.vendor_pk = :vendor_pk" if vendor_pk is not None else ""
    relation_filter = "AND er.relation = :relation" if relation else ""

    if direction == "out":
        join_part = "JOIN entity_relations er ON er.src_entity_pk = f.pk JOIN entities e ON e.pk = er.dst_entity_pk"
    elif direction == "in":
        join_part = "JOIN entity_relations er ON er.dst_entity_pk = f.pk JOIN entities e ON e.pk = er.src_entity_pk"
    else:  # both
        join_part = """
            JOIN entity_relations er ON (er.src_entity_pk = f.pk OR er.dst_entity_pk = f.pk)
            JOIN entities e ON e.pk = CASE WHEN er.src_entity_pk = f.pk THEN er.dst_entity_pk ELSE er.src_entity_pk END
        """

    sql = text(f"""
        WITH RECURSIVE walk AS (
            SELECT pk, kind, text, canonical, 0 AS depth, ARRAY[pk] AS path
            FROM entities WHERE pk = :seed_pk AND deprecated_at IS NULL
            UNION ALL
            SELECT e.pk, e.kind, e.text, e.canonical, f.depth + 1, f.path || e.pk
            FROM walk f
            {join_part}
            WHERE er.tenant_id = :tid {vendor_filter} {relation_filter}
              AND er.deprecated_at IS NULL
              AND e.deprecated_at IS NULL
              AND NOT (e.pk = ANY(f.path))
              AND f.depth + 1 <= :depth
        )
        SELECT DISTINCT pk, kind, text, canonical, depth FROM walk ORDER BY depth, kind, pk
    """)

    params: dict[str, Any] = {"seed_pk": from_entity_pk, "tid": tid, "depth": depth}
    if relation:
        params["relation"] = relation
    if vendor_pk is not None:
        params["vendor_pk"] = vendor_pk
    nodes = db.execute(sql, params).all()

    # Re-fetch edges between any pair of returned nodes — that's the
    # actual subgraph the caller will render.
    node_pks = [n[0] for n in nodes]
    if not node_pks:
        return {"nodes": [], "edges": []}

    edge_rows = db.scalars(
        select(EntityRelation).where(
            EntityRelation.tenant_id == tid,
            EntityRelation.src_entity_pk.in_(node_pks),
            EntityRelation.dst_entity_pk.in_(node_pks),
            EntityRelation.deprecated_at.is_(None),
        )
    ).all()

    return {
        "nodes": [
            {"pk": pk, "kind": k, "text": t, "canonical": c, "depth": d}
            for pk, k, t, c, d in nodes
        ],
        "edges": [_relation_to_dict(r) for r in edge_rows],
    }


@router.get("/identity-graph")
def identity_graph(
    q: str = Query(..., min_length=1, description="Entity name to center the graph on"),
    depth: int = Query(2, ge=1, le=5, description="Max hop depth from the seed entity"),
    direction: str = Query("both", description="out | in | both"),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Cross-document ego-network for a resolved entity identity. Resolves the
    query to a unified identity (merging spelling/word-order variants across
    documents), picks the most-connected entity mention as the seed, runs a
    recursive-CTE graph walk, and returns the combined subgraph + identity
    profile. Used by the frontend force-directed graph view.

    When the query doesn't match any entity, returns ``{found: false}`` so the
    frontend can fall back to a per-document subgraph."""
    if direction not in _VALID_DIRECTIONS:
        raise HTTPException(status_code=422, detail="direction must be out, in, or both")
    from app.services import entity_profile as _ep

    tid = get_current_tenant()

    # 1. Resolve the query — find ALL matching identities, not just the single
    #    best. A single-word query like "rajesh" may match entities in separate
    #    documents that don't cluster into the same identity (different
    #    canonicals). We union all their members so the graph includes every
    #    document referencing the name.
    rows = _ep._owner_entity_rows(db)
    q_tokens = graph_resolve._tokens(q)
    need = 2 if len(q_tokens) >= 2 else 1

    # Collect every identity whose canonicals overlap the query enough
    all_matches: list[graph_resolve.Identity] = []
    for ident in graph_resolve.cluster(rows):
        score = max((len(q_tokens & graph_resolve._tokens(c)) for c in ident.canonicals), default=0)
        if score >= need:
            all_matches.append(ident)

    if not all_matches:
        return {"found": False, "query": q, "nodes": [], "edges": []}

    # Primary identity = highest ranked (for display)
    ident = max(all_matches, key=lambda i: (
        max((len(q_tokens & graph_resolve._tokens(c)) for c in i.canonicals), default=0),
        graph_resolve._KIND_PRIORITY.get(i.kind, 1),
        len(i.doc_pks),
    ))
    # Collect member PKs and doc PKs ONLY from members whose own canonical/text
    # contains the query tokens. An identity may span 3 documents but the query
    # "kalyani" only matches entities in 1 of them — counting all 3 would inflate.
    member_pks: list[int] = []
    all_doc_pks: set[int] = set()
    for mid in all_matches:
        for m in mid.members:
            mt = graph_resolve._tokens(m.get("canonical") or m.get("text") or "")
            if len(q_tokens & mt) >= need:
                member_pks.append(m["pk"])
                if m.get("document_pk") is not None:
                    all_doc_pks.add(m["document_pk"])
    # Override ident's doc_pks so the identity card shows the accurate count
    ident.doc_pks = all_doc_pks

    # ── Also search extracted_fields + chunk text for documents where the
    # query name appears but no entity was bootstrapped.  The chat's
    # _docs_mentioning_name and Content search both do this via the shared
    # keyword_search_documents; the identity graph must match so the
    # docCount agrees.  These docs get their document hub entity injected so
    # they appear in the tree, even without person-level entity nodes.
    _nl = q.lower().strip()
    if len(_nl) >= 2:
        try:
            from app.services.document_search import keyword_search_documents
            owner = get_current_owner_user_pk()
            if owner is not None:
                kw_results = keyword_search_documents(db, q, tenant_id=tid, owner_user_id=owner)
                kw_doc_pks = {r["pk"] for r in kw_results}
                if kw_doc_pks:
                    new_docs = kw_doc_pks - all_doc_pks
                    all_doc_pks |= kw_doc_pks
                    ident.doc_pks = all_doc_pks
                    if new_docs:
                        _log = logging.getLogger("docaiq.identity_graph")
                        _log.info("IDENTITY_GRAPH keyword_search match: q=%r docs=%s", q, sorted(new_docs))
        except Exception:
            pass

    if not member_pks and not all_doc_pks:
        return {"found": False, "query": q, "nodes": [], "edges": []}

    # When we have doc matches from extracted_fields but zero entity members
    # (rare: the name only appears in structured fields, never in entities),
    # still return found=true so the frontend can show document hubs.
    if not member_pks and all_doc_pks:
        # Create a synthetic identity from the extracted-fields documents
        doc_entity_rows = db.execute(
            select(Entity.pk, Entity.kind, Entity.text, Entity.canonical, Entity.document_pk)
            .where(
                Entity.tenant_id == tid,
                Entity.document_pk.in_(all_doc_pks),
                Entity.kind == "document",
                Entity.deprecated_at.is_(None),
            )
        ).all()
        nodes = [(r.pk, r.kind, r.text, r.canonical, 0, r.document_pk) for r in doc_entity_rows]
        node_pks = [r.pk for r in doc_entity_rows]
        edges_list = []
        if node_pks:
            edge_rows = db.scalars(
                select(EntityRelation).where(
                    EntityRelation.tenant_id == tid,
                    EntityRelation.src_entity_pk.in_(node_pks),
                    EntityRelation.dst_entity_pk.in_(node_pks),
                    EntityRelation.deprecated_at.is_(None),
                )
            ).all()
            edges_list = [_relation_to_dict(r) for r in edge_rows]
        return {
            "found": True,
            "query": q,
            "seedPk": node_pks[0] if node_pks else None,
            "identity": {
                "name": q.title(),
                "kind": "person",
                "docCount": len(all_doc_pks),
            },
            "profile": {},
            "nodes": [{"pk": n[0], "kind": n[1], "text": n[2], "canonical": n[3] or n[2],
                       "depth": n[4], "documentPk": n[5]} for n in nodes],
            "edges": edges_list,
        }

    # Main seed = most-connected member (used for identity profile display).
    best_pk = member_pks[0]
    best_deg = -1
    for pk in member_pks:
        deg = db.scalar(
            select(func.count()).where(
                EntityRelation.tenant_id == tid,
                EntityRelation.deprecated_at.is_(None),
                (EntityRelation.src_entity_pk == pk) | (EntityRelation.dst_entity_pk == pk),
            )
        ) or 0
        if deg > best_deg:
            best_deg = deg
            best_pk = pk
    seed_pk = best_pk

    # Validate at least one member still exists. If the best-connected seed is
    # deprecated, fall back to any non-deprecated member for display.
    seed = db.scalar(select(Entity).where(Entity.tenant_id == tid, Entity.pk == seed_pk))
    if seed is not None and seed.deprecated_at is not None:
        # Best seed was deprecated — try the other members
        for alt_pk in member_pks:
            if alt_pk == seed_pk:
                continue
            alt = db.scalar(select(Entity).where(Entity.tenant_id == tid, Entity.pk == alt_pk, Entity.deprecated_at.is_(None)))
            if alt is not None:
                seed = alt
                seed_pk = alt_pk
                break
        else:
            # All members are deprecated — still show what we have; the CTE will
            # just have fewer seeds (deprecated_at IS NULL filter drops them).
            seed = seed  # keep the original deprecated seed for display
    if seed is None:
        return {"found": False, "query": q, "nodes": [], "edges": []}

    if direction == "out":
        join_part = "JOIN entity_relations er ON er.src_entity_pk = f.pk JOIN entities e ON e.pk = er.dst_entity_pk"
    elif direction == "in":
        join_part = "JOIN entity_relations er ON er.dst_entity_pk = f.pk JOIN entities e ON e.pk = er.src_entity_pk"
    else:  # both
        join_part = """
            JOIN entity_relations er ON (er.src_entity_pk = f.pk OR er.dst_entity_pk = f.pk)
            JOIN entities e ON e.pk = CASE WHEN er.src_entity_pk = f.pk THEN er.dst_entity_pk ELSE er.src_entity_pk END
        """

    # Seed the CTE from ALL identity members so disconnected subgraphs
    # (same person across documents with no cross-doc edge) all render.
    sql = text(f"""
        WITH RECURSIVE walk AS (
            SELECT pk, kind, text, canonical, 0 AS depth, ARRAY[pk] AS path, document_pk
            FROM entities WHERE pk = ANY(:seed_pks) AND deprecated_at IS NULL
            UNION ALL
            SELECT e.pk, e.kind, e.text, e.canonical, f.depth + 1, f.path || e.pk, e.document_pk
            FROM walk f
            {join_part}
            WHERE er.tenant_id = :tid
              AND er.deprecated_at IS NULL
              AND e.deprecated_at IS NULL
              AND NOT (e.pk = ANY(f.path))
              AND f.depth + 1 <= :depth
        )
        SELECT DISTINCT pk, kind, text, canonical, depth, document_pk FROM walk ORDER BY depth, kind, pk
    """)

    params: dict[str, Any] = {"seed_pks": member_pks, "tid": tid, "depth": depth}
    nodes = db.execute(sql, params).all()

    node_pks = [n[0] for n in nodes]

    # ── Ensure document hub entities for ALL identity documents are included.
    # A person entity in a sparse subgraph (e.g. bank statement where the
    # person connects to an account identifier, not directly to the document)
    # may leave the document entity > depth hops away — but users expect to
    # see a visible doc hub anchoring every document in the identity.
    doc_entity_rows = db.execute(
        select(Entity.pk, Entity.kind, Entity.text, Entity.canonical, Entity.document_pk)
        .where(
            Entity.tenant_id == tid,
            Entity.document_pk.in_(ident.doc_pks),
            Entity.kind == "document",
            Entity.deprecated_at.is_(None),
        )
    ).all()
    missing_doc = [r for r in doc_entity_rows if r.pk not in node_pks]
    if missing_doc:
        max_depth = max((n[4] for n in nodes), default=0)
        nodes = list(nodes)  # type: ignore[assignment]
        for r in missing_doc:
            nodes.append((r.pk, r.kind, r.text, r.canonical, max_depth + 1, r.document_pk))
        node_pks.extend(r.pk for r in missing_doc)

    edges_list: list[dict] = []
    if node_pks:
        edge_rows = db.scalars(
            select(EntityRelation).where(
                EntityRelation.tenant_id == tid,
                EntityRelation.src_entity_pk.in_(node_pks),
                EntityRelation.dst_entity_pk.in_(node_pks),
                EntityRelation.deprecated_at.is_(None),
            )
        ).all()
        edges_list = [_relation_to_dict(r) for r in edge_rows]

    # 4. Build the identity profile for the side panel
    profile = _ep.build_profile(db, q)

    # 5. Pick a query-biased display name — the clean member text with the
    #    highest token overlap with the query. Searching "kalyani" should show
    #    "KALYANI GODA RAJESH", not the longest member "GODA RAJESH BALVANTRAI".
    _texts = [(m.get("text") or "").strip() for m in ident.members]
    _texts = [t for t in _texts if t and "\n" not in t and "[" not in t and "/OR" not in t.upper() and len(t) <= 60]
    if _texts:
        _best_text = max(_texts, key=lambda t: (len(q_tokens & graph_resolve._tokens(t)), len(t)))
    else:
        _best_text = max(ident.canonicals, key=lambda c: (len(q_tokens & graph_resolve._tokens(c)), len(c))) if ident.canonicals else ident.name
    display_name = _best_text if _best_text else ident.name

    _log = logging.getLogger("docaiq.identity_graph")
    _log.info("IDENTITY_GRAPH q=%r docCount=%d doc_pks=%s all_matches=%d seedPk=%s nodeCount=%d",
              q, len(ident.doc_pks), sorted(ident.doc_pks), len(all_matches), seed_pk, len(nodes))
    return {
        "found": True,
        "query": q,
        "seedPk": seed_pk,
        "identity": {
            "name": display_name,
            "kind": ident.kind,
            "docCount": len(ident.doc_pks),
        },
        "profile": profile,
        "nodes": [
            {"pk": pk, "kind": k, "text": t, "canonical": c, "depth": d, "documentPk": doc_pk}
            for pk, k, t, c, d, doc_pk in nodes
        ],
        "edges": edges_list,
    }


# ── Reconciliation endpoints (items #5 + #7 from the expense audit slice) ──


@router.get("/reconcile/duplicates")
def list_duplicates(
    vendor_pk: int | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Receipts flagged as likely duplicates of another receipt.
    Returns hydrated pairs {a:{docId,name}, b:{docId,name}, confidence,
    signals} sorted highest-confidence first."""
    return graph_reconcile.duplicates_for_vendor(db, vendor_pk)


@router.get("/reconcile/payments")
def list_payment_matches(
    vendor_pk: int | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Receipt ↔ bank-statement-transaction matches. Each entry links
    a receipt doc to the transaction node that paid for it, plus the
    bank statement that hosts that transaction."""
    return graph_reconcile.payments_for_vendor(db, vendor_pk)


@router.get("/reconcile/revenue")
def list_revenue_matches(
    vendor_pk: int | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Income-side mirror — revenue_invoice / customer_payment docs paired
    with bank-statement CREDIT transactions that received the money. Proves
    the invoiced money actually arrived in the account."""
    return graph_reconcile.revenue_matches_for_vendor(db, vendor_pk)


@router.delete("/reconcile/duplicates/{relation_pk}", status_code=204)
def dismiss_duplicate(
    relation_pk: int,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> None:
    """Dismiss a duplicate finding as a false positive. Deletes only the
    `duplicate_of` edge — the two receipts themselves stay intact. The
    reconcile pass won't re-emit the same edge until the underlying
    facts change (or the user does Re-run reconciliation), so dismissal
    sticks across page refreshes."""
    tid = get_current_tenant()
    row = db.scalar(
        select(EntityRelation).where(
            EntityRelation.tenant_id == tid,
            EntityRelation.pk == relation_pk,
            EntityRelation.relation == "duplicate_of",
            EntityRelation.deprecated_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Duplicate edge {relation_pk} not found")
    db.delete(row)
    db.commit()


class FindingDrillPayload(BaseModel):
    kind: str
    evidence: dict


@router.post("/insights/drill")
def drill_into_finding(
    payload: FindingDrillPayload,
    vendor_pk: int | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Given a finding (kind + evidence), return the underlying source
    documents the finding was computed from. Lets the reviewer drill
    from an audit-insight headline straight to the receipts/invoices
    that make it up."""
    tenant_id = get_current_tenant()
    return graph_insights.drill_finding(db, tenant_id, payload.kind, payload.evidence, vendor_pk)


@router.get("/insights")
def get_audit_insights(
    vendor_pk: int | None = Query(None),
    date_from: str | None = Query(None, description="ISO YYYY-MM-DD lower bound"),
    date_to: str | None = Query(None, description="ISO YYYY-MM-DD upper bound"),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Audit-intelligence Graph RAG queries. Returns 5 finding categories:
    counterparty_risk, concentration_risk, subscription_drift,
    category_anomaly, cross_period_continuity.

    `date_from` / `date_to` scope the audit to a specific period (Q1, FY,
    custom range). Findings are computed against docs whose extracted
    date falls in the window; when omitted, the full tenant history is
    scanned."""
    tenant_id = get_current_tenant()
    return graph_insights.all_insights(
        db, tenant_id, vendor_pk,
        date_from=date_from, date_to=date_to,
    )


@router.post("/reconcile/rerun")
def rerun_reconcile(
    vendor_pk: int | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, int]:
    """Manually re-run reconciliation. Useful when a reviewer thinks
    the worker missed a match — also wired up to the Re-extract button
    via the documents/reclassify path. Returns counts."""
    result = graph_reconcile.scan(db, vendor_pk=vendor_pk)
    db.commit()
    return result
