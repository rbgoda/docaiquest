"""#3 real-LLM A/B — legacy all-rules prompt vs the fragment-assembled prompt, on
the SAME questions + a fixed multi-doc fixture. Scores format-compliance, grounding,
tokens and latency for both, so we can confirm the leaner prompt is same-or-better.

Run:  cd backend && python -m eval.answer_ab [--n 40] [--model dashscope/qwen-plus]
Loads DOCAIQ_DASHSCOPE_API_KEY from ../.env. ~2*N LLM calls.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# load ../.env into the environment so config/gateway pick up the provider key
for line in (_ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, str(_ROOT / "backend"))

from app.llm import gateway  # noqa: E402
from app.services.answer_fragments import build_rules_block, expected_format  # noqa: E402

_INTRO = ("You are DocAIQ — a document audit assistant answering across a SET of "
          "documents. Be precise. No filler.\n\n")
_LEGACY_RULES = (
    "RULES:\n"
    "  · Use the STRUCTURED FIELDS and evidence excerpts below. If neither contains "
    "the answer, reply: 'Not found in the retrieved evidence.'\n"
    "  · For attribute/value questions (who is the applicant, the total, a date), "
    "PREFER the STRUCTURED FIELDS — they carry ROLE labels, so use them to pick the "
    "RIGHT value. Use evidence excerpts to confirm.\n"
    "  · ALWAYS name the source document when you state a fact. Keep facts attributed.\n"
    "  · Comparison / 'across all' questions → a short markdown table, one row per document.\n"
    "  · Single-value question → one line with the value + its source doc.\n"
    "  · When the question asks for documents OF a kind, include ONLY documents whose "
    "TYPE matches. A document that merely MENTIONS an identifier is NOT that kind.\n"
    "  · Never invent. Never explain what you're doing.")

# --- fixed fixture: a small realistic library --------------------------------
FIELDS_BLOCK = """STRUCTURED FIELDS (per document):
- Invoice_ACME.pdf (type=invoice): vendor=ACME Corp; total=$4,200.00; date=2026-06-12; due=2026-07-12
- BankStmt_Northwind.pdf (type=bank_statement): bank=Northwind Bank; closing_balance=$12,480.55; period=Jun 2026
- Passport_AM.pdf (type=passport): holder=Alex Morgan; number=X1234567; expires=2031-03-04
- Resume_2023.pdf (type=resume): name=Alex Morgan; skills=Python, SQL, Excel; title=Data Analyst
- Resume_2024.pdf (type=resume): name=Alex Morgan; skills=Python, SQL, Rust, Kubernetes; title=Senior Data Engineer
- InsuranceCert.pdf (type=motor_insurance_certificate): holder=Alex Morgan; NRIC=S1234567A; policy_expires=2026-12-01"""

EVIDENCE_BLOCK = """Evidence excerpts:
[E1 · Invoice_ACME.pdf · type=invoice · page 1] Invoice from ACME Corp. Amount due $4,200.00. Invoice date 12 Jun 2026, payment due 12 Jul 2026.
[E2 · BankStmt_Northwind.pdf · type=bank_statement · page 1] Northwind Bank statement, June 2026. Closing balance: $12,480.55.
[E3 · Passport_AM.pdf · type=passport · page 1] Passport. Holder: Alex Morgan. Passport No X1234567. Date of expiry 04 MAR 2031.
[E4 · Resume_2023.pdf · type=resume · page 1] Alex Morgan — Data Analyst. Skills: Python, SQL, Excel.
[E5 · Resume_2024.pdf · type=resume · page 1] Alex Morgan — Senior Data Engineer. Skills: Python, SQL, Rust, Kubernetes.
[E6 · InsuranceCert.pdf · type=motor_insurance_certificate · page 1] Motor insurance certificate. Holder Alex Morgan, NRIC S1234567A. Policy valid until 01 Dec 2026."""

# --- questions: (question, shape, must_contain[], must_not_contain[]) ----------
Q = [
    ("What is the balance in my bank statement?", "single", ["12,480.55"], []),
    ("What is the total on the ACME invoice?", "single", ["4,200"], []),
    ("When does my passport expire?", "single", ["2031"], []),
    ("When is the ACME invoice due?", "single", ["12 Jul 2026", "2026-07-12", "Jul"], []),
    ("What is my passport number?", "single", ["X1234567"], []),
    ("Who is the passport holder?", "attribute", ["Alex Morgan"], []),
    ("Whose name is on the invoice's vendor field?", "attribute", ["ACME"], []),
    ("Who is the insurance certificate holder?", "attribute", ["Alex Morgan"], []),
    ("Compare my two resumes — what's different?", "compare", ["Rust", "Kubernetes"], []),
    ("Compare the invoice and the bank statement amounts.", "compare", ["4,200", "12,480"], []),
    ("Which documents are national IDs?", "of_kind", ["Passport"], ["Invoice", "Insurance", "Resume", "Bank"]),
    ("List all my invoices with their amounts.", "of_kind", ["ACME", "4,200"], ["Passport", "Resume"]),
    ("Which documents belong to Alex Morgan?", "of_kind", ["Passport", "Resume"], []),
    ("How much do I owe in total across my invoices?", "single", ["4,200"], []),
    ("Summarize my bank statement in 3 bullet points.", "free", ["12,480.55"], []),
    ("What is my blood type?", "abstain", ["not found", "no ", "isn't", "not available", "no information"], []),
    ("What is my credit score?", "abstain", ["not found", "no ", "isn't", "not available", "no information"], []),
    ("What skills are on my 2024 resume?", "single", ["Rust", "Kubernetes"], []),
    ("Which is my newest resume?", "compare", ["2024"], []),
    ("What bank is my statement from?", "attribute", ["Northwind"], []),
]


def build_user_block(question: str) -> str:
    return f"{FIELDS_BLOCK}\n\n{EVIDENCE_BLOCK}\n\nQuestion: {question}"


def score(ans: str, shape: str, must: list[str], mustnot: list[str]) -> dict:
    a = ans.lower()
    grounded = any(m.lower() in a for m in must) if must else True
    clean = not any(mn.lower() in a for mn in mustnot)
    if shape == "compare":
        fmt = "|" in ans and ans.count("|") >= 3            # a markdown table
    elif shape == "single":
        fmt = len([l for l in ans.splitlines() if l.strip()]) <= 3
    elif shape == "of_kind":
        fmt = clean                                          # excludes the trap docs
    elif shape == "abstain":
        fmt = grounded                                       # said "not found"
    else:
        fmt = True
    return {"grounded": grounded, "clean": clean, "format_ok": bool(fmt)}


def run_variant(system: str, question: str, model: str) -> dict:
    r = gateway.call(model, [gateway.Message(role="system", content=system),
                             gateway.Message(role="user", content=build_user_block(question))],
                     temperature=0.0, max_tokens=400, task_kind="eval_ab")
    return {"text": r.text or "", "out_tokens": r.output_tokens, "in_tokens": r.input_tokens,
            "latency_ms": r.latency_ms, "answer_found": None}


def run_typed(system: str, question: str, model: str) -> dict:
    """#4 · typed-answer variant — structured {answer, answer_found, format, caveats}."""
    from app.services import typed_answer as ta
    r = gateway.call(model, [gateway.Message(role="system", content=system + ta._SCHEMA_HINT),
                             gateway.Message(role="user", content=build_user_block(question))],
                     temperature=0.0, max_tokens=400, structured=True, task_kind="eval_ab")
    obj = getattr(r, "structured", None)
    if obj is None:
        try:
            txt = (r.text or "").strip()
            if txt.startswith("```"):
                txt = txt.strip("`"); txt = txt[txt.find("{"):txt.rfind("}") + 1]
            obj = json.loads(txt)
        except Exception:  # noqa: BLE001
            obj = None
    tobj = ta._coerce(obj) if obj else None
    return {"text": tobj.rendered() if tobj else (r.text or ""),
            "out_tokens": r.output_tokens, "in_tokens": r.input_tokens, "latency_ms": r.latency_ms,
            "answer_found": (tobj.answer_found if tobj else None), "parsed": tobj is not None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval.answer_ab")
    ap.add_argument("--model", default="dashscope/qwen-plus")
    ap.add_argument("--n", type=int, default=len(Q))
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    qs = Q[:args.n]
    agg = {"legacy": [], "frag": [], "typed": [], "hybrid": []}
    detail = []
    abstain_hit = {"legacy": 0, "typed": 0, "abstain_total": 0}
    for i, (q, shape, must, mustnot) in enumerate(qs, 1):
        leg_sys = _INTRO + _LEGACY_RULES
        frag_block, picks = build_rules_block(q)
        frag_sys = _INTRO + frag_block
        try:
            leg = run_variant(leg_sys, q, args.model)
            frg = run_variant(frag_sys, q, args.model)
            typ = run_typed(frag_sys, q, args.model)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}] LLM error on {q[:40]!r}: {e}")
            continue
        ls = score(leg["text"], shape, must, mustnot)
        fs = score(frg["text"], shape, must, mustnot)
        ts = score(typ["text"], shape, must, mustnot)
        # HYBRID: table/comparison → free-text (frag); everything else → typed.
        hyb = frg if expected_format(q) == "table" else typ
        hs = score(hyb["text"], shape, must, mustnot)
        agg["legacy"].append({**ls, **leg}); agg["frag"].append({**fs, **frg})
        agg["typed"].append({**ts, **typ}); agg["hybrid"].append({**hs, **hyb})
        if shape == "abstain":
            abstain_hit["abstain_total"] += 1
            if ls["grounded"]:
                abstain_hit["legacy"] += 1
            if typ.get("answer_found") is False:   # typed contract flags it explicitly
                abstain_hit["typed"] += 1
        detail.append({"q": q, "shape": shape, "picks": picks,
                       "legacy": {**ls, "tok": leg["out_tokens"], "ms": leg["latency_ms"]},
                       "frag": {**fs, "tok": frg["out_tokens"], "ms": frg["latency_ms"]},
                       "typed": {**ts, "tok": typ["out_tokens"], "ms": typ["latency_ms"], "answer_found": typ.get("answer_found"), "parsed": typ.get("parsed")}})
        print(f"  [{i:>2}/{len(qs)}] {shape:<9} L(fmt{int(ls['format_ok'])}grd{int(ls['grounded'])}) "
              f"F(fmt{int(fs['format_ok'])}grd{int(fs['grounded'])}) "
              f"T(fmt{int(ts['format_ok'])}grd{int(ts['grounded'])}af={typ.get('answer_found')}) {q[:40]!r}")

    def pct(rows, k): return 100.0 * sum(1 for r in rows if r[k]) / max(1, len(rows))
    def avg(rows, k): return sum(r[k] for r in rows) / max(1, len(rows))
    print(f"\n=== A/B over {len(agg['frag'])} questions · model={args.model} ===")
    for name, rows in (("LEGACY (all rules)", agg["legacy"]), ("#3 FRAGMENTS", agg["frag"]),
                       ("#4 TYPED", agg["typed"]), ("#4 HYBRID", agg["hybrid"])):
        print(f"{name:<22} format {pct(rows,'format_ok'):5.1f}%  grounded {pct(rows,'grounded'):5.1f}%  "
              f"clean {pct(rows,'clean'):5.1f}%  out_tok {avg(rows,'out_tokens'):5.0f}  in_tok {avg(rows,'in_tokens'):5.0f}  "
              f"lat {avg(rows,'latency_ms'):5.0f}ms")
    at = abstain_hit["abstain_total"] or 1
    print(f"\nUnanswerable handling ({abstain_hit['abstain_total']} qs): legacy said-not-found {abstain_hit['legacy']}/{at}  ·  "
          f"#4 typed answer_found=false {abstain_hit['typed']}/{at}")
    tp = sum(1 for r in agg['typed'] if r.get('parsed'))
    print(f"#4 typed: valid JSON parsed on {tp}/{len(agg['typed'])} answers")
    if args.json:
        args.json.write_text(json.dumps({"model": args.model, "detail": detail}, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
