"""Categorizer agent — auto-assign an expense category to every transaction.

Receipts already carry a `category` field from the receipt fact-extraction
schema (the LLM reads the receipt and infers Meals/Travel/etc). But
transactions inside a bank or credit-card statement are just merchant
descriptions ("APPLE.COM/BILL", "VIRGIN ACTIVE SG", "PARKING.SG"), and
typical banks don't print a category column.

This agent fills that gap. Given a list of transaction descriptions, it
returns the category enum value for each. One LLM call per statement
(batch all unique merchants), cached at the tenant level so subsequent
uploads of the same merchant cost zero LLM calls.

Categories are a fixed enum so the Expenses tab can roll up reliably and
matches what the receipt schema already uses.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import gateway, ledger
from app.llm.prompts import get_prompt
from app.model_registry import REGISTRY as _AI_REGISTRY
from app.orm import MerchantCategoryCache

log = logging.getLogger("docaiq.agents.categorizer")


# Canonical category vocabularies. Keep these stable
# and additive — add new entries at the END so historical categorizations
# remain valid.
#
# Two parallel vocabularies: one for outgoing money (expenses) and one for
# incoming money (income / revenue). The categorizer picks the right set
# based on transaction direction or the source schema's intent.

EXPENSE_CATEGORIES = [
    "Meals",
    "Travel",
    "Transport",
    "Utilities",
    "Subscriptions",
    "Healthcare",
    "Fitness",
    "Shopping",
    "Office",
    "Entertainment",
    "Government Fees",
    "Banking Fees",
    "Cash / Payments",
    "Tax",
    "Other",
]

INCOME_CATEGORIES = [
    "Sales",
    "Service Revenue",
    "Consulting",
    "Subscription Revenue",
    "Rental",
    "Royalties",
    "Interest",
    "Dividends",
    "Tax Refund",
    "Reimbursement Received",
    "Grants",
    "Other Income",
]

# Back-compat alias — older code still imports CATEGORIES (expenses).
CATEGORIES = EXPENSE_CATEGORIES


_MODEL = "anthropic/claude-haiku-4.5"
_URL = "https://openrouter.ai/api/v1/chat/completions"


def _canon(s: str) -> str:
    """Normalize a merchant string for cache lookup. Strips digits, trailing
    location tokens, and IDs so 'APPLE.COM/BILL 8001861087 IE' and
    'APPLE.COM/BILL 8003331234 US' map to the same cache entry."""
    if not s:
        return ""
    out = s.upper().strip()
    out = re.sub(r"[*#].*$", "", out)        # drop everything after * or #
    out = re.sub(r"\b\d{4,}\b", "", out)     # drop long digit runs (txn ids)
    out = re.sub(r"\s+(SG|US|IE|UK|IN|MY|AU|CA|HK|JP|TH|PH)$", "", out)  # country tags
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _cache_get(db: Session, tenant_id: str, merchants: Iterable[str]) -> dict[str, str]:
    """Look up cached categories for the given merchants. Returns
    {canonical_merchant: category} for everything that's in the cache."""
    canons = list({_canon(m) for m in merchants if m})
    if not canons:
        return {}
    rows = db.scalars(
        select(MerchantCategoryCache).where(
            MerchantCategoryCache.tenant_id == tenant_id,
            MerchantCategoryCache.merchant_canon.in_(canons),
        )
    ).all()
    return {r.merchant_canon: r.category for r in rows}


def _cache_put(db: Session, tenant_id: str, mapping: dict[str, str]) -> None:
    """Upsert canonical merchant → category mappings."""
    for canon, category in mapping.items():
        if not canon or not category:
            continue
        existing = db.scalar(
            select(MerchantCategoryCache).where(
                MerchantCategoryCache.tenant_id == tenant_id,
                MerchantCategoryCache.merchant_canon == canon,
            )
        )
        if existing:
            existing.category = category
        else:
            db.add(MerchantCategoryCache(
                tenant_id=tenant_id,
                merchant_canon=canon,
                category=category,
            ))
    db.flush()


_EXPENSE_GUIDANCE = (
    "- Restaurants, cafés, food delivery → Meals\n"
    "- Flights, hotels, ride-sharing for trips → Travel\n"
    "- Daily commute, parking, gas, ride-share local → Transport\n"
    "- Electricity, water, gas, internet, phone → Utilities\n"
    "- SaaS, Apple/Google subscriptions, Netflix → Subscriptions\n"
    "- Doctor, pharmacy, hospital, insurance → Healthcare\n"
    "- Gym, yoga, sports clubs → Fitness\n"
    "- Online + retail purchases that aren't food → Shopping\n"
    "- Office supplies, coworking, business services → Office\n"
    "- Movies, concerts, streaming-of-entertainment, events → Entertainment\n"
    "- Government registration / licence / permit fees → Government Fees\n"
    "- Bank charges, conversion fees, interest, late fees → Banking Fees\n"
    "- Payments TO the card (GIRO/payment received), cash withdrawals, "
    "settlements → Cash / Payments\n"
    "- VAT/GST/income tax payments → Tax\n"
    "- Genuinely unknown → Other\n"
)

_INCOME_GUIDANCE = (
    "- Product sales, retail revenue → Sales\n"
    "- Professional services, billable hours, fees-for-service → Service Revenue\n"
    "- Advisory, strategy, project consulting fees → Consulting\n"
    "- Monthly/annual recurring SaaS or membership revenue → Subscription Revenue\n"
    "- Property / equipment / vehicle rental income → Rental\n"
    "- IP licence fees, music/film/franchise royalties → Royalties\n"
    "- Bank interest, investment yield → Interest\n"
    "- Equity dividends received → Dividends\n"
    "- GST/VAT/income tax refunds from authority → Tax Refund\n"
    "- Insurance payouts, employer expense reimbursements → Reimbursement Received\n"
    "- Government grants, research grants, subsidies → Grants\n"
    "- Anything not fitting above → Other Income\n"
)


_CATEGORIZER_RATE_USD_PER_MTOK = 3.0


def _llm_categorize(merchants: list[str], *, mode: str = "expense",
                    extra_cats: list[str] | None = None,
                    db: Session | None = None,
                    tenant_id: str | None = None) -> dict[str, str]:
    """Single batch call: send all merchants, get back a category per merchant.

    `mode` picks the base vocabulary:
      - 'expense' (default) → EXPENSE_CATEGORIES (merchants the entity paid)
      - 'income'            → INCOME_CATEGORIES  (payers / revenue sources)

    `extra_cats` extends the enum the LLM picks from — M28.5 custom
    categories (vendor-local + global). They appear in the prompt with a
    [custom] tag so the model knows they're tenant-specific. The LLM is
    free to use them when appropriate; canonical labels remain the
    default for normal cases.

    Uses Anthropic vision-less haiku-4.5. Returns {original_merchant_string: category}.
    Defaults to 'Other' / 'Other Income' for anything the model couldn't categorize.
    """
    base = INCOME_CATEGORIES if mode == "income" else EXPENSE_CATEGORIES
    # Merge custom on top. Dedup by name (case-sensitive on purpose — the
    # canonical names are mixed-case; reviewers may add "Stripe Fees" not
    # "stripe fees"). Order: canonical first, custom appended so the LLM
    # sees the well-known names first.
    extras = [c for c in (extra_cats or []) if c and c not in base]
    cats = base + extras
    guidance = get_prompt("categorizer_income_guidance") if mode == "income" else get_prompt("categorizer_expense_guidance")
    default = "Other Income" if mode == "income" else "Other"

    settings = get_settings()
    if not merchants:
        return {}
    # Route through the LLM gateway so a `dashscope/…` model reaches a FUNDED
    # provider. The old direct OpenRouter call 402'd (depleted key) → every
    # merchant fell to 'Other', which is why card/bank transactions showed
    # "Uncategorized". Override with DOCAIQ_DOCUMENTS_CATEGORIZE_MODEL.
    model = settings.documents_categorize_model or _AI_REGISTRY["categorizer"].default_model

    target_noun = "income source / payer" if mode == "income" else "expense merchant"
    # Annotate custom entries so the model knows they're tenant-specific.
    base_set = set(base)
    cat_lines = [
        f"  - {c}{' [tenant custom]' if c not in base_set else ''}"
        for c in cats
    ]
    system = get_prompt("categorizer",
        target_noun=target_noun,
        cat_lines="\n".join(cat_lines),
        guidance=guidance,
    )
    user_block = f"{target_noun.title()}s to categorize:\n" + "\n".join(f"- {m}" for m in merchants)

    import time as _time
    t0 = _time.perf_counter()
    try:
        result = gateway.call(
            model=model,
            messages=[
                gateway.Message(role="system", content=system),
                gateway.Message(role="user", content=user_block),
            ],
            temperature=0.0, max_tokens=2048, structured=True,
            tenant_id=tenant_id, task_kind="categorize",
        )
        text = result.text or "{}"
        latency_ms = int((_time.perf_counter() - t0) * 1000)
        if db is not None:
            ledger.record_call(
                db, task="categorize", tier="t2", provider=result.provider,
                model=result.model or model,
                input_tokens=int(result.input_tokens or 0),
                output_tokens=int(result.output_tokens or 0),
                cost_per_input_mtok=1.0, cost_per_output_mtok=5.0,
                latency_ms=latency_ms, status="ok",
                tenant_id=tenant_id,
            )
    except Exception as e:  # noqa: BLE001 — provider / network
        log.warning("categorizer: LLM call failed: %s — defaulting to '%s'", e, default)
        if db is not None:
            ledger.record_call(
                db, task="categorize", tier="t2", provider="gateway",
                model=model, status="failed", error=str(e),
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                tenant_id=tenant_id,
            )
        return {m: default for m in merchants}

    # Tolerant parse — same json-repair fallback as the fact extractor.
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            out = json.loads(repair_json(text))
        except Exception:  # noqa: BLE001
            log.warning("categorizer: JSON parse failed; defaulting to 'Other'")
            return {m: "Other" for m in merchants}

    if not isinstance(out, dict):
        return {m: default for m in merchants}
    # Validate / coerce categories
    valid = set(cats)
    result: dict[str, str] = {}
    for m in merchants:
        cat = out.get(m) or out.get(m.strip()) or default
        if cat not in valid:
            ci = {c.lower(): c for c in cats}
            cat = ci.get(str(cat).lower(), default)
        result[m] = cat
    return result


@dataclass
class CategorizeResult:
    categorized: int   # number of items that got a category
    cached_hits: int   # how many came from the merchant cache (no LLM cost)
    llm_called: bool   # did we hit the LLM this run?


def categorize_transactions(
    db: Session, tenant_id: str, transactions: list[dict], *,
    mode: str = "auto", vendor_pk: int | None = None,
) -> CategorizeResult:
    """Categorize a list of transaction dicts IN PLACE. Each dict gets a
    `category` key added (or overwritten if it was empty / 'Other').

    `mode` selects which vocabulary to use:
      - 'auto'    → per-transaction: direction='credit' → income side,
                    everything else → expense side. Best for mixed bank
                    statements where credits are payments received.
      - 'expense' → always EXPENSE_CATEGORIES (receipt items, expense docs).
      - 'income'  → always INCOME_CATEGORIES (revenue invoices, customer
                    payments — every entry is incoming).

    `vendor_pk` enables M28.5 custom categories — when set, vendor-local
    + global custom rows for this tenant are merged into the LLM's enum.
    When custom categories ARE in play, the merchant cache is bypassed
    so an old "Meals" cache hit doesn't pre-empt a better "Coffee" custom.
    Without custom categories, the cache works as before.

    Uses the merchant cache (free) and only calls the LLM for merchants
    not yet seen on this tenant. Cache keys include the mode so the same
    merchant string can map to different categories on each side.
    """
    if not transactions:
        return CategorizeResult(0, 0, False)

    # Pre-fetch custom categories for this tenant + vendor. Empty when
    # the feature isn't in use → falls back to canonical enum + cache.
    extra_expense: list[str] = []
    extra_income: list[str] = []
    try:
        from app.repositories import categories as cat_repo
        all_exp = cat_repo.list_custom_names(db, mode="expense", vendor_pk=vendor_pk)
        all_inc = cat_repo.list_custom_names(db, mode="income", vendor_pk=vendor_pk)
        # list_custom_names returns canonical+custom merged; strip canonical
        # for the extra list (we only want the *added* names).
        canon_exp = set(EXPENSE_CATEGORIES)
        canon_inc = set(INCOME_CATEGORIES)
        extra_expense = [n for n in all_exp if n not in canon_exp]
        extra_income = [n for n in all_inc if n not in canon_inc]
    except Exception as e:  # noqa: BLE001
        log.warning("categorizer: custom-category lookup failed (vendor_pk=%s) · %s", vendor_pk, e)
    has_custom = bool(extra_expense or extra_income)

    def _txn_mode(txn: dict) -> str:
        if mode != "auto":
            return mode
        return "income" if (txn.get("direction") == "credit") else "expense"

    def _other_for(m: str) -> str:
        return "Other Income" if m == "income" else "Other"

    # Collect items that need categorization. Skip ones that already have
    # a non-empty, non-default category — receipts arrive pre-categorized.
    needs: list[tuple[int, str, str]] = []  # (idx, merchant, txn_mode)
    for i, txn in enumerate(transactions):
        if not isinstance(txn, dict):
            continue
        existing_cat = (txn.get("category") or "").strip()
        if existing_cat and existing_cat not in {"Other", "Other Income"}:
            continue
        merchant = (
            txn.get("merchant") or txn.get("description")
            or txn.get("vendor_name") or txn.get("payer_name") or ""
        ).strip()
        if not merchant:
            continue
        needs.append((i, merchant, _txn_mode(txn)))

    if not needs:
        return CategorizeResult(0, 0, False)

    # Cache lookup is mode-aware via prefix on the canonical key.
    # Same merchant on expense side vs income side → different cache rows.
    # M28.5: when custom categories are in play, skip the cache so a
    # stale "Other" hit doesn't pre-empt a fresh custom label.
    unique_pairs = list({(m, mm) for _, m, mm in needs})
    canon_map = {(m, mm): f"{mm}:{_canon(m)}" for m, mm in unique_pairs}
    cached = (
        {} if has_custom
        else _cache_get(db, tenant_id, list({c for c in canon_map.values()}))
    )

    # What still needs the LLM, grouped by mode
    expense_uncached = [m for (m, mm) in unique_pairs if mm == "expense" and cached.get(canon_map[(m, mm)]) is None]
    income_uncached = [m for (m, mm) in unique_pairs if mm == "income" and cached.get(canon_map[(m, mm)]) is None]
    new_cats: dict[tuple[str, str], str] = {}  # keyed by (merchant, mode)
    llm_called = False
    if expense_uncached:
        result = _llm_categorize(
            expense_uncached, mode="expense", extra_cats=extra_expense,
            db=db, tenant_id=tenant_id,
        )
        for m, c in result.items():
            new_cats[(m, "expense")] = c
        llm_called = True
    if income_uncached:
        result = _llm_categorize(
            income_uncached, mode="income", extra_cats=extra_income,
            db=db, tenant_id=tenant_id,
        )
        for m, c in result.items():
            new_cats[(m, "income")] = c
        llm_called = True
    # Skip cache writes when custom categories were used — keeps the cache
    # canonical-only so it stays valid for tenants that don't use custom.
    # Also skip "Other"/"Other Income" (LLM default on rate-limit/parse-failure)
    # so a bad batch never poisons the cache permanently.
    if new_cats and not has_custom:
        cacheable = {
            canon_map[(m, mm)]: c
            for (m, mm), c in new_cats.items()
            if c not in ("Other", "Other Income")
        }
        if cacheable:
            _cache_put(db, tenant_id, cacheable)

    # Apply
    cached_hits = 0
    categorized = 0
    for i, merchant, mm in needs:
        key = canon_map[(merchant, mm)]
        cat = cached.get(key) or new_cats.get((merchant, mm)) or _other_for(mm)
        if cached.get(key):
            cached_hits += 1
        transactions[i]["category"] = cat
        categorized += 1

    log.info(
        "categorizer: %d/%d items categorized (mode=%s, cache hits=%d, llm_called=%s)",
        categorized, len(transactions), mode, cached_hits, llm_called,
    )
    return CategorizeResult(categorized=categorized, cached_hits=cached_hits, llm_called=llm_called)
