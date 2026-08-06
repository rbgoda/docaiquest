"""Shared document keyword search — the canonical "find documents matching a
keyword" implementation used by Content search, Chat name queries, and Entities tab.

Searches document name, doc type, extracted fields (JSONB), and chunk text with
Postgres full-text + trigram ranking. No embeddings, no semantic matching — only
literal keyword matches. Each caller formats results for its own surface.
"""
from __future__ import annotations

import json as _json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("docaiq.services.document_search")


def keyword_search_documents(
    db: Session,
    query: str,
    *,
    tenant_id: str,
    owner_user_id: int,
) -> list[dict]:
    """Search the owner's documents for `query` across four data sources.

    Returns a list of dicts ordered by relevance (highest score first):
        {pk, name, id_external, page, snippet, score}
    """
    if not query or not query.strip():
        return []

    q = query.strip()
    needle = f"%{q}%"

    # Collect matching doc PKs from each data source, each with a relevance
    # score.  Higher score = better match.
    scored: dict[int, tuple[float, str | None, str | None, int | None]] = {}
    #                           score  name         snippet      page

    # ── 1. Document name match (strongest signal, score 10.0) ────────────
    name_rows = db.execute(
        text("""SELECT pk, name, 10.0 AS score FROM documents
                WHERE tenant_id = :tid AND is_archived = false
                  AND owner_user_id = :uid AND name ILIKE :n
                LIMIT 50"""),
        {"tid": tenant_id, "uid": owner_user_id, "n": needle},
    ).fetchall()
    for pk, name, score in name_rows:
        scored[pk] = (float(score), name, f"Filename: {name}", None)

    # ── 2. Doc type match (score 8.0) ────────────────────────────────────
    type_rows = db.execute(
        text("""SELECT pk, name, doc_type, 8.0 AS score FROM documents
                WHERE tenant_id = :tid AND is_archived = false
                  AND owner_user_id = :uid AND doc_type ILIKE :n
                LIMIT 50"""),
        {"tid": tenant_id, "uid": owner_user_id, "n": needle},
    ).fetchall()
    for pk, name, doc_type, score in type_rows:
        cur = scored.get(pk)
        if cur is None or float(score) > cur[0]:
            scored[pk] = (float(score), name, f"Type: {doc_type}", None)

    # ── 3. Extracted fields match (data-rich signal, score 9.0) ──────────
    ef_rows = db.execute(
        text("""SELECT pk, name, extracted_fields, 9.0 AS score FROM documents
                WHERE tenant_id = :tid AND is_archived = false
                  AND owner_user_id = :uid
                  AND extracted_fields::text ILIKE :n
                LIMIT 100"""),
        {"tid": tenant_id, "uid": owner_user_id, "n": needle},
    ).fetchall()
    for pk, name, ef, score in ef_rows:
        if pk in scored:
            continue  # already found via name or type (higher score)
        snippet = None
        try:
            flat = _json.dumps(ef or {}, ensure_ascii=False)
            idx = flat.lower().find(q.lower())
            if idx >= 0:
                start = max(0, idx - 30)
                snippet = "…" + flat[start:idx + len(q) + 80] + "…"
        except Exception:
            pass
        scored[pk] = (float(score), name, snippet, None)

    # ── 4. Chunk text match (full-text + trigram, score < 1.0) ───────────
    # ts_rank rewards English full-text relevance; pg_trgm similarity is the
    # language-agnostic fallback for non-English queries (e.g. Hindi names
    # like "kalyani").  We pick the best chunk per document.
    chunk_rows = db.execute(
        text("""SELECT DISTINCT ON (d.pk)
                     d.pk, d.name, c.text, c.page,
                     (COALESCE(ts_rank(c.tsv, websearch_to_tsquery('english', :q)), 0)
                      + COALESCE(similarity(c.text, :q), 0)) AS score
                FROM document_chunks c
                JOIN documents d ON d.pk = c.document_pk
                WHERE d.tenant_id = :tid AND d.is_archived = false
                  AND d.owner_user_id = :uid
                  AND (c.tsv @@ websearch_to_tsquery('english', :q)
                       OR c.text ILIKE :n)
                ORDER BY d.pk, score DESC
                LIMIT 200"""),
        {"tid": tenant_id, "uid": owner_user_id, "q": q, "n": needle},
    ).fetchall()
    for pk, name, chunk_text, page, score in chunk_rows:
        cur = scored.get(pk)
        # Chunk scores are typically < 1.0; metadata scores are >= 8.0.
        # Only add chunk matches for docs not already found via metadata.
        if cur is None:
            snippet = " ".join((chunk_text or "").split())[:240]
            scored[pk] = (float(score), name, snippet, int(page) if page else None)

    # ── Resolve id_external for each doc ─────────────────────────────────
    from app.orm import Document as _Doc
    from sqlalchemy import select as _sel

    results: list[dict] = []
    for pk, (score, name, snippet, page) in sorted(scored.items(),
                                                    key=lambda x: -x[1][0]):
        id_ext = db.scalar(_sel(_Doc.id_external).where(_Doc.pk == pk))
        results.append({
            "pk": pk,
            "name": name,
            "id_external": id_ext,
            "page": page,
            "snippet": snippet or "",
            "score": round(score, 4),
        })

    return results
