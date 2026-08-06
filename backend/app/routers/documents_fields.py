from __future__ import annotations

import logging
from typing import Any  # FieldEditPayload.value: Any (was imported mid-file in documents.py)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel  # field payloads (FieldEditPayload etc.)
from sqlalchemy.orm import Session

from app.db import get_current_tenant, get_session
from app.models.documents import Document
from app.queue import enqueue_rematch
from app.repositories import documents as repo
from app.security import CurrentUser, require_role
from app.services import documents as docs_service

# Re-exports — kept so any caller still using the underscore-prefixed
# helper names continues to work (TODO #25 conservative extraction).
_link_doc_to_requirement = docs_service.link_doc_to_requirement
_human_size = docs_service.human_size

log = logging.getLogger(__name__)

router = APIRouter()


class FieldEditPayload(BaseModel):
    field_path: str  # e.g. "fields.total", "fields.top_transactions.0.category"
    value: Any
    reason: str | None = None
    # Phase 2b · draw-to-correct — when the reviewer drew a box for this field,
    # move the field's bbox to that region (normalized 0..1 [x0,y0,x1,y1] + page).
    page: int | None = None
    bbox: list[float] | None = None


@router.patch("/{doc_id}/fields", response_model=Document)
def edit_document_field(
    doc_id: str,
    payload: FieldEditPayload,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """HITL override of a single field on a document's extracted_fields.

    The path is dotted into the JSONB:
        fields.total
        fields.vendor.name
        fields.top_transactions.0.category
        fields.line_items.3.amount

    Records a field_edits audit row (before/after/who/when/why) and
    re-runs the downstream pipeline (categorizer + graph bootstrap +
    reconcile) so changes propagate. Returns the fresh document."""
    import copy
    import json
    from sqlalchemy.orm.attributes import flag_modified
    from app.orm import FieldEdit

    doc = repo.get_row(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if not doc.extracted_fields:
        raise HTTPException(status_code=409, detail="Document has no extracted fields to edit yet")

    # Walk the dotted path to the target slot. Returns (parent, key) so
    # we can mutate parent[key]. Numeric segments index arrays.
    ef = copy.deepcopy(doc.extracted_fields)
    parts = payload.field_path.split(".")
    if not parts:
        raise HTTPException(status_code=400, detail="Empty field_path")
    cursor: Any = ef
    parent: Any = None
    last_key: Any = None
    try:
        for p in parts:
            parent = cursor
            last_key = int(p) if p.isdigit() else p
            cursor = cursor[last_key]
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=404, detail=f"field_path {payload.field_path!r} not found")

    original_value = cursor
    parent[last_key] = payload.value

    # G7 · a human-corrected scalar field is now verified → mark its per-field
    # confidence 1.0 so the review UI stops flagging it "⚠ check" and the
    # needs-review count drops. Only for top-level scalar fields (fields.<name>).
    if len(parts) == 2 and parts[0] == "fields" and isinstance(ef.get("field_confidence"), dict):
        ef["field_confidence"][parts[1]] = 1.0

    # Phase 2b · spatial correction. When the reviewer drew a box for this field,
    # MOVE its bbox to the drawn region so the colored field box (and any future
    # citation) points at the right place. Normalized coords with page_w/page_h=1
    # → FieldBoxes treats x0..y1 as page fractions (renders + filters by page).
    if (len(parts) == 2 and parts[0] == "fields"
            and payload.bbox and len(payload.bbox) == 4):
        bx = [float(v) for v in payload.bbox]
        if not isinstance(ef.get("field_bboxes"), dict):
            ef["field_bboxes"] = {}
        ef["field_bboxes"][parts[1]] = {
            "page": int(payload.page or 1),
            "x0": bx[0], "y0": bx[1], "x1": bx[2], "y1": bx[3],
            "page_w": 1, "page_h": 1,
        }

    # Audit row first (so even if downstream fails we have the trail).
    db.add(FieldEdit(
        tenant_id=user.org_id,
        document_pk=doc.pk,
        field_path=payload.field_path,
        original_value=json.dumps(original_value, default=str)[:4000] if not isinstance(original_value, str) else original_value[:4000],
        new_value=json.dumps(payload.value, default=str)[:4000] if not isinstance(payload.value, str) else str(payload.value)[:4000],
        edited_by=user.email,
        reason=(payload.reason or "")[:2000] or None,
    ))
    doc.extracted_fields = ef
    flag_modified(doc, "extracted_fields")
    db.commit()

    # Golden eval corpus — a human correction is ground truth: mark this doc's
    # eval case verified (no-op when there's no case, e.g. a paid doc). Best-effort.
    try:
        from app.services import eval_corpus
        eval_corpus.mark_verified(db, doc.pk)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    # Downstream propagation — graph bootstrap reads the live facts, so
    # we re-emit graph entities to keep the audit graph in sync with the
    # reviewer's manual edits. Reconcile fires afterwards so duplicate /
    # payment matches reflect the corrected values.
    try:
        from app.graph import bootstrap as graph_bootstrap, reconcile as graph_reconcile
        graph_bootstrap.run(db, doc.pk)
        db.commit()
        graph_reconcile.scan(db, vendor_pk=doc.vendor_pk)
        db.commit()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("docaiq.routers.documents").warning(
            "post-edit pipeline failed for doc pk=%s: %s", doc.pk, e,
        )

    # M28 · re-check auto-approve. Filling a previously-missing field may
    # have unblocked the doc. No-op when threshold is None.
    try:
        from app.document_review import (
            get_document_threshold, get_duplicate_doc_ids, try_auto_approve,
        )
        threshold = get_document_threshold(db)
        if threshold is not None:
            if try_auto_approve(db, doc, threshold=threshold,
                                duplicate_doc_ids=get_duplicate_doc_ids(db)):
                db.commit()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("docaiq.routers.documents").warning(
            "post-edit auto-approve failed for doc pk=%s: %s", doc.pk, e,
        )

    # M28.6 · re-fire matcher after every field edit. The reviewer may
    # have changed the vendor name, date, or category — all of which
    # affect requirement match scoring. Fire-and-forget; the response
    # returns immediately with the current doc, and matches refresh
    # asynchronously (visible on next /api/audit-runs/{id} fetch).
    background.add_task(enqueue_rematch, doc.pk, user.org_id)

    fresh = repo.get(db, doc_id)
    return fresh


def _extract_region_text(doc, page_num: int | None, bbox) -> str:
    """Extract the text under a normalized [x0,y0,x1,y1] region (0..1 page fractions)
    on `page_num`. PDF → PyMuPDF get_textbox; image → OCR words whose centre falls
    inside the box, joined in reading order. Best-effort ('' on any failure)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return ""
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if not getattr(doc, "s3_key", None):
        return ""
    try:
        from app import storage as app_storage
        buf = b"".join(app_storage.stream_object(doc.s3_key))
        mime = (doc.mime_type or "").lower()
        if mime.startswith("application/pdf"):
            # Use the SAME robust capture the annotation endpoint uses: PDF text
            # layer first, then OCR fallback for scanned/image PDFs (which have no
            # text layer). Without this, add-field/line-item returned "No text found"
            # on scanned docs (e.g. photographed IDs) where highlights worked fine.
            from app.services.region_capture import capture_region_text
            return (capture_region_text(buf, page_num or 1, x0, y0, x1, y1,
                                        tenant_id=get_current_tenant()) or "").strip()
        if mime.startswith("image/"):
            from app.agents import ocr as ocr_mod
            words, iw, ih = ocr_mod.extract_words(buf)
            if not words or not iw or not ih:
                return ""
            px0, py0, px1, py1 = x0 * iw, y0 * ih, x1 * iw, y1 * ih
            inside = [w for w in words
                      if px0 <= (w.x0 + w.x1) / 2 <= px1 and py0 <= (w.y0 + w.y1) / 2 <= py1]
            inside.sort(key=lambda w: (round(w.y0 / 10), w.x0))
            return " ".join(w.text for w in inside).strip()
    except Exception:  # noqa: BLE001 — best-effort
        return ""
    return ""


class FromRegionPayload(BaseModel):
    label: str                       # the new field name (snake_cased server-side)
    bbox: list[float]                # normalized [x0, y0, x1, y1] (0..1 page fractions)
    page: int | None = 1
    reason: str | None = None


def _norm_value(s) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _find_matching_field(fields: dict, text: str) -> str | None:
    """Return the name/label of an EXISTING extracted value that the boxed `text` matches
    (normalized exact, or a known value fully contained in the box), so a manual region→field
    add can dedupe instead of duplicating. Checks scalar fields + labeled-array item values."""
    tn = _norm_value(text)
    if len(tn) < 3:
        return None
    for k, v in (fields or {}).items():
        if isinstance(v, str):
            vn = _norm_value(v)
            if vn and (vn == tn or (len(vn) >= 5 and vn in tn)):
                return k
    for arr in ("key_facts", "identifiers", "parties", "amounts", "dates", "records"):
        for it in (fields.get(arr) or []):
            if not isinstance(it, dict):
                continue
            vn = _norm_value(it.get("value") or it.get("name") or "")
            if vn and (vn == tn or (len(vn) >= 5 and vn in tn)):
                return it.get("label") or it.get("name") or arr
    return None


_LABELED_ARRAYS = ("key_facts", "identifiers", "amounts", "dates", "parties", "records")


def _find_labeled_array_key(fields: dict, label: str) -> str | None:
    """If `label` names an existing entry-label in one of the labeled arrays (key_facts,
    identifiers, …), return that array's key — so a manual add can APPEND another entry
    (e.g. a second `seat`) instead of creating a suffix field."""
    ln = (label or "").strip().lower()
    if not ln:
        return None
    for arr in _LABELED_ARRAYS:
        for it in (fields.get(arr) or []):
            if isinstance(it, dict) and str(it.get("label") or it.get("name") or "").strip().lower() == ln:
                return arr
    return None


def _add_mention(ef: dict, key: str, box: dict) -> None:
    if not isinstance(ef.get("field_mentions"), dict):
        ef["field_mentions"] = {}
    ef["field_mentions"].setdefault(key, []).append(box)


_CCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY"}


def _infer_doc_currency(fields: dict) -> str:
    """Best-guess the document currency from its money fields (for pre-filling a line item)."""
    import re as _re
    candidates = [fields.get("primary_amount"), fields.get("total_due")]
    candidates += [it.get("value") for it in (fields.get("amounts") or []) if isinstance(it, dict)]
    for v in candidates:
        if not isinstance(v, str):
            continue
        m = _re.search(r"\b([A-Z]{3})\b", v)
        if m:
            return m.group(1)
        for sym, code in _CCY_SYMBOLS.items():
            if sym in v:
                return code
    return ""


def _parse_money(text: str, default_ccy: str) -> tuple[str, str]:
    """(amount, currency) from a boxed money string; currency falls back to the doc's."""
    import re as _re
    ccy = default_ccy
    m = _re.search(r"\b([A-Z]{3})\b", text)
    if m:
        ccy = m.group(1)
    else:
        for sym, code in _CCY_SYMBOLS.items():
            if sym in text:
                ccy = code
                break
    num = _re.search(r"[\d][\d,]*\.?\d*", text)
    amount = num.group(0).replace(",", "") if num else text.strip()
    return amount, ccy


@router.post("/{doc_id}/fields/from-region")
def add_field_from_region(
    doc_id: str,
    payload: FromRegionPayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Region → field. Draw a box on the document, name it, and it becomes a new
    extracted field — the value is pulled from the text under the box (PDF text or
    image OCR), the drawn box becomes the field's bbox, and the label feeds
    learned_schemas so the document TYPE learns this field for next time. Mirrors the
    field-edit audit + downstream (graph, eval-verify)."""
    import copy
    import re as _re
    from sqlalchemy.orm.attributes import flag_modified
    from app.orm import FieldEdit

    doc = repo.get_row(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    label = _re.sub(r"[^a-z0-9_]+", "_", (payload.label or "").strip().lower()).strip("_")[:64]
    if not label:
        raise HTTPException(status_code=400, detail="A field label is required")
    if not payload.bbox or len(payload.bbox) != 4:
        raise HTTPException(status_code=400, detail="bbox must be [x0, y0, x1, y1]")

    text = _extract_region_text(doc, payload.page, payload.bbox)
    if not text:
        raise HTTPException(status_code=422, detail="No text found in the selected region")

    ef = copy.deepcopy(doc.extracted_fields) if isinstance(doc.extracted_fields, dict) else {}
    for key in ("fields", "field_confidence", "field_bboxes"):
        if not isinstance(ef.get(key), dict):
            ef[key] = {}
    bx = [float(v) for v in payload.bbox]
    box = {"page": int(payload.page or 1), "x0": bx[0], "y0": bx[1], "x1": bx[2], "y1": bx[3],
           "page_w": 1, "page_h": 1}

    # Phase 1 · entity-aware dedupe: if the boxed value already exists, record this box as an
    # ADDITIONAL mention (field_mentions) instead of creating a duplicate field.
    fields = ef["fields"]
    matched = _find_matching_field(fields, text)
    arr_key = None if matched else _find_labeled_array_key(fields, label)
    scalar_exists = bool(not matched and not arr_key and isinstance(fields.get(label), (str, list)))
    appended = False
    if matched:
        # (a) value already exists anywhere → record an additional mention (dedupe).
        _add_mention(ef, matched, box)
        field_name, merged = matched, True
        reason = "manual region · additional mention"
    elif arr_key:
        # (b) label matches a repeated field (e.g. `seat`) → append another entry to that array.
        norm = _norm_value(text)
        ln = label.strip().lower()
        exists = any(isinstance(it, dict)
                     and _norm_value(it.get("value") or it.get("name") or "") == norm
                     and str(it.get("label") or it.get("name") or "").strip().lower() == ln
                     for it in fields[arr_key])
        if not exists:
            fields[arr_key].append({"label": label, "value": text})
        _add_mention(ef, label, box)
        field_name, merged, appended = label, False, True
        reason = "manual region · appended to list"
    elif scalar_exists:
        # (c) label matches a top-level scalar → promote to a list and append (dedupe).
        cur = fields.get(label)
        vals = list(cur) if isinstance(cur, list) else [cur]
        if text not in vals:
            vals.append(text)
        fields[label] = vals
        _add_mention(ef, label, box)
        field_name, merged, appended = label, False, True
        reason = "manual region · appended to list"
    else:
        # (d) brand-new field.
        fields[label] = text
        ef["field_confidence"][label] = 1.0        # human-placed → fully trusted
        ef["field_bboxes"][label] = box
        field_name, merged = label, False
        reason = (payload.reason or "manual region")

    db.add(FieldEdit(tenant_id=user.org_id, document_pk=doc.pk, field_path=f"fields.{field_name}",
                     original_value=None, new_value=text[:4000], edited_by=user.email,
                     reason=reason[:2000]))
    doc.extracted_fields = ef
    flag_modified(doc, "extracted_fields")
    db.commit()

    # Learning tie-in — only for a genuinely NEW field label (merge/append add no new vocabulary).
    if not merged and not appended:
        try:
            from app.repositories import learned_schemas as _ls
            _ls.record(db, doc.doc_type, [label], [])
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        # Adaptive Schema Loop — propagate the new field into the doc's APPROVED schema so every
        # future doc of this type captures it (bidirectional learning: one correction → all docs).
        try:
            from app.agents import schema_autopilot as _ap
            if _ap.learn_field(db, doc, label,
                               description=f"{label} (added from a document region during review)"):
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    # Human placement = ground truth (mirror the field-edit endpoint).
    try:
        from app.services import eval_corpus
        eval_corpus.mark_verified(db, doc.pk)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    try:
        from app.graph import bootstrap as graph_bootstrap, reconcile as graph_reconcile
        graph_bootstrap.run(db, doc.pk)
        db.commit()
        graph_reconcile.scan(db, vendor_pk=doc.vendor_pk)
        db.commit()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("docaiq.routers.documents").warning(
            "post-region-add pipeline failed for doc pk=%s: %s", doc.pk, e)

    return {"merged": merged, "appended": appended, "field": field_name, "value": text,
            "document": repo.get(db, doc_id)}


class AddFieldPayload(BaseModel):
    label: str                        # new field name (snake_cased server-side)
    value: str = ""                   # the value, typed by the reviewer (no OCR)
    reason: str | None = None


@router.post("/{doc_id}/fields/add")
def add_field(
    doc_id: str,
    payload: AddFieldPayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Add (or append to) an extracted field by typing its NAME + VALUE directly — no
    box-drawing, so name and value are separate by construction. Mirrors the
    add-field-from-region write/audit/learning path, minus the OCR region."""
    import copy
    import re as _re
    from sqlalchemy.orm.attributes import flag_modified
    from app.orm import FieldEdit

    doc = repo.get_row(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    label = _re.sub(r"[^a-z0-9_]+", "_", (payload.label or "").strip().lower()).strip("_")[:64]
    if not label:
        raise HTTPException(status_code=400, detail="A field label is required")
    value = (payload.value or "").strip()

    ef = copy.deepcopy(doc.extracted_fields) if isinstance(doc.extracted_fields, dict) else {}
    for key in ("fields", "field_confidence", "field_bboxes"):
        if not isinstance(ef.get(key), dict):
            ef[key] = {}
    fields = ef["fields"]
    existing = fields.get(label)
    appended = False
    if isinstance(existing, list) and (not existing or not isinstance(existing[0], dict)):
        if value and value not in existing:
            existing.append(value)
        appended = True
    elif isinstance(existing, str):
        vals = [existing] if existing else []
        if value and value not in vals:
            vals.append(value)
        fields[label] = vals
        appended = True
    elif isinstance(existing, (list, dict)):
        # existing holds STRUCTURED rows (e.g. line_items / transactions) — never overwrite that
        # table with a single scalar. The reviewer should edit the rows, not add a same-named field.
        raise HTTPException(
            status_code=409,
            detail=f"'{label}' already holds structured rows — edit them in the table, not as a single field")
    else:
        fields[label] = value
        ef["field_confidence"][label] = 1.0     # human-typed → trusted

    db.add(FieldEdit(tenant_id=user.org_id, document_pk=doc.pk, field_path=f"fields.{label}",
                     original_value=None, new_value=value[:4000], edited_by=user.email,
                     reason=(payload.reason or "manual add-field")[:2000]))
    doc.extracted_fields = ef
    flag_modified(doc, "extracted_fields")
    db.commit()

    if not appended:
        try:
            from app.repositories import learned_schemas as _ls
            _ls.record(db, doc.doc_type, [label], [])
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        try:
            from app.agents import schema_autopilot as _ap
            if _ap.learn_field(db, doc, label, description=f"{label} (added manually during review)"):
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    try:
        from app.services import eval_corpus
        eval_corpus.mark_verified(db, doc.pk)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"appended": appended, "field": label, "value": value, "document": repo.get(db, doc_id)}


@router.delete("/{doc_id}/fields/{field_key}")
def delete_field(
    doc_id: str,
    field_key: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Remove a top-level extracted field (e.g. a mis-added or wrong one). Audited."""
    import copy
    from sqlalchemy.orm.attributes import flag_modified
    from app.orm import FieldEdit

    doc = repo.get_row(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    ef = copy.deepcopy(doc.extracted_fields) if isinstance(doc.extracted_fields, dict) else {}
    fields = ef.get("fields")
    if not isinstance(fields, dict) or field_key not in fields:
        raise HTTPException(status_code=404, detail=f"field {field_key!r} not found")
    old = fields.pop(field_key, None)
    for sub in ("field_confidence", "field_bboxes", "field_mentions"):
        if isinstance(ef.get(sub), dict):
            ef[sub].pop(field_key, None)
    db.add(FieldEdit(tenant_id=user.org_id, document_pk=doc.pk, field_path=f"fields.{field_key}",
                     original_value=str(old)[:4000] if old is not None else None,
                     new_value=None, edited_by=user.email, reason="field deleted"))
    doc.extracted_fields = ef
    flag_modified(doc, "extracted_fields")
    db.commit()
    return {"deleted": field_key, "document": repo.get(db, doc_id)}


class LineItemPayload(BaseModel):
    bbox: list[float]                 # normalized [x0, y0, x1, y1]
    page: int | None = 1
    description: str | None = None    # optional; edit the row after to fill it


@router.post("/{doc_id}/line-items/from-region")
def add_line_item_from_region(
    doc_id: str,
    payload: LineItemPayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Invoice line item from a region. Box a line's amount → append a row
    {description, amount, currency} to fields.line_items. Amount + currency are parsed from
    the box (currency falls back to the doc's); description comes from the payload or is filled
    by editing the row afterwards. Each row keeps the drawn box as a mention."""
    import copy
    import json as _json
    from sqlalchemy.orm.attributes import flag_modified
    from app.orm import FieldEdit
    from app.rate_limit import rate_limit as _rate_limit
    _rate_limit(user.email, action="field_edit")

    doc = repo.get_row(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if not payload.bbox or len(payload.bbox) != 4:
        raise HTTPException(status_code=400, detail="bbox must be [x0, y0, x1, y1]")
    text = _extract_region_text(doc, payload.page, payload.bbox)
    if not text:
        raise HTTPException(status_code=422, detail="No text found in the selected region")

    ef = copy.deepcopy(doc.extracted_fields) if isinstance(doc.extracted_fields, dict) else {}
    fields = ef.setdefault("fields", {})
    amount, currency = _parse_money(text, _infer_doc_currency(fields))
    if not isinstance(fields.get("line_items"), list):
        fields["line_items"] = []
    row = {"description": (payload.description or "").strip(), "amount": amount, "currency": currency}
    fields["line_items"].append(row)
    idx = len(fields["line_items"]) - 1
    bx = [float(v) for v in payload.bbox]
    _add_mention(ef, "line_items", {"page": int(payload.page or 1), "x0": bx[0], "y0": bx[1],
                                    "x1": bx[2], "y1": bx[3], "page_w": 1, "page_h": 1})

    db.add(FieldEdit(tenant_id=user.org_id, document_pk=doc.pk,
                     field_path=f"fields.line_items.{idx}", original_value=None,
                     new_value=_json.dumps(row)[:4000], edited_by=user.email,
                     reason="manual region · line item"))
    doc.extracted_fields = ef
    flag_modified(doc, "extracted_fields")
    db.commit()
    try:
        from app.repositories import learned_schemas as _ls
        _ls.record(db, doc.doc_type, ["line_items"], [])
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    try:
        from app.services import eval_corpus
        eval_corpus.mark_verified(db, doc.pk)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"lineItem": row, "count": len(fields["line_items"]), "document": repo.get(db, doc_id)}
