"""Retrieval + entities endpoints (M8).

`GET /api/retrieve?q=...&top_k=12&doc_id=...` runs the hybrid BM25+cosine
search and returns ranked chunks with scoring metadata.

`GET /api/entities?kind=...&doc_id=...` lists extracted entities.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant, get_current_vendor_pk, get_session
from app.orm import Entity
from app.retrieval import retrieve

router = APIRouter()


class HitDTO(BaseModel):
    chunkPk: int
    documentId: str
    documentName: str
    page: int
    text: str
    score: float
    bm25Rank: int | None = None
    cosineRank: int | None = None


class RetrieveResponse(BaseModel):
    query: str
    topK: int
    docFilter: str | None = None
    hits: list[HitDTO]


@router.get("/retrieve", response_model=RetrieveResponse)
def get_retrieve(
    q: str = Query(min_length=1, max_length=500),
    top_k: int = Query(12, ge=1, le=50, alias="top_k"),
    doc_id: str | None = Query(None, alias="doc_id"),
    db: Session = Depends(get_session),
) -> RetrieveResponse:
    hits = retrieve(db, q, top_k=top_k, doc_id_external=doc_id)
    return RetrieveResponse(
        query=q,
        topK=top_k,
        docFilter=doc_id,
        hits=[
            HitDTO(
                chunkPk=h.chunk_pk,
                documentId=h.document_id_external,
                documentName=h.document_name,
                page=h.page,
                text=h.text,
                score=h.score,
                bm25Rank=h.bm25_rank,
                cosineRank=h.cosine_rank,
            )
            for h in hits
        ],
    )


class EntityDTO(BaseModel):
    pk: int
    kind: str
    text: str
    page: int
    documentId: str
    documentName: str
    metadata: dict | None = Field(default=None)


@router.get("/entities", response_model=list[EntityDTO])
def list_entities(
    kind: str | None = Query(None),
    doc_id: str | None = Query(None, alias="doc_id"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> list[dict]:
    from app.orm import Document  # local import to avoid cyclic typing concerns
    tid = get_current_tenant()
    stmt = (
        select(Entity, Document.id_external, Document.name)
        .join(Document, Document.pk == Entity.document_pk)
        .where(Entity.tenant_id == tid)
    )
    if kind:
        stmt = stmt.where(Entity.kind == kind)
    if doc_id:
        stmt = stmt.where(Document.id_external == doc_id)
    # M17 · vendor-role isolation — a vendor-only user sees only their own
    # docs' entities, never the whole tenant.
    vpk = get_current_vendor_pk()
    if vpk is not None:
        stmt = stmt.where(Document.vendor_pk == vpk)
    stmt = stmt.order_by(Entity.pk.desc()).limit(limit)

    out: list[dict] = []
    for ent, doc_external, doc_name in db.execute(stmt).all():
        out.append({
            "pk": ent.pk,
            "kind": ent.kind,
            "text": ent.text,
            "page": ent.page,
            "documentId": doc_external,
            "documentName": doc_name,
            "metadata": ent.entity_metadata,
        })
    return out
