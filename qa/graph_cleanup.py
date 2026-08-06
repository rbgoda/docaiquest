"""Entity-graph cleanup — re-apply the hardened canon_name/canon_org to existing entities:
delete the ones that are now invalid (PII placeholders, pure numbers/ids, OCR crumbs, junk),
re-canonicalise the rest, drop their dangling relations, then rebuild durable identities.

Idempotent. Run in the backend container AFTER deploying the canonical.py hardening:
    docker exec -e PYTHONPATH=/app -w /app <backend> python /app/qa/graph_cleanup.py <owner>
"""
import sys

from sqlalchemy import select, delete
from app.db import SessionLocal
from app.orm import Entity, EntityRelation, Document
from app.graph.canonical import canon_name, canon_org
from app.graph import identity_resolver

NAME_KINDS = ("person", "org", "location", "product", "standard")

if __name__ == "__main__":
    owner = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    tid = "documents"
    db = SessionLocal()
    ents = db.scalars(
        select(Entity).join(Document, Document.pk == Entity.document_pk).where(
            Document.tenant_id == tid, Document.owner_user_id == owner,
            Entity.kind.in_(NAME_KINDS))).all()
    invalid, recanon, samples = [], 0, []
    for e in ents:
        new = canon_org(e.text) if e.kind in ("org", "location") else canon_name(e.text)
        if not new:
            invalid.append(e.pk)
            if len(samples) < 15:
                samples.append(f"{e.kind}:{(e.text or '')[:40]!r}")
        elif new != (e.canonical or ""):
            e.canonical = new[:256]
            recanon += 1
    if invalid:
        db.execute(delete(EntityRelation).where(
            (EntityRelation.src_entity_pk.in_(invalid)) | (EntityRelation.dst_entity_pk.in_(invalid))))
        db.execute(delete(Entity).where(Entity.pk.in_(invalid)))
    db.commit()
    n_ident = identity_resolver.rebuild_for_owner(db, tid, owner)
    print(f"scanned={len(ents)} · deleted(invalid)={len(invalid)} · re-canonicalised={recanon} · "
          f"identities_rebuilt={n_ident}", flush=True)
    print("deleted samples: " + " | ".join(samples), flush=True)
    print("DONE", flush=True)
    db.close()
