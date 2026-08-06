"""Phase-1 · Ragas-vs-R4 QA eval (RAGHub-style standardized comparison).

Runs DocAIQ's deterministic R4 metrics AND ecosystem-standard Ragas LLM-judge
metrics on the SAME golden set, side by side, and reports the correlation between
R4's cheap faithfulness PROXY and Ragas's LLM-judge faithfulness — so we can keep
the proxy in CI with evidence that it tracks the standard metric.

Kept OUT of the backend image: Ragas is a heavy opt-in dep (see requirements-eval.txt).
R4 (eval/scorer.py) stays pure-stdlib. This module degrades gracefully:

  python -m eval.ragas_eval --dry              # R4 only; no LLM, no ragas needed
  python -m eval.ragas_eval --judge dashscope  # + Ragas (needs requirements-eval.txt + key)

Dataset: eval/dataset/ragas_qa.json (each case carries everything both need).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from eval import scorer

_DATASET = Path(__file__).parent / "dataset" / "ragas_qa.json"


# ── data ─────────────────────────────────────────────────────────────────────

def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text())["cases"]


# ── R4 (deterministic) ───────────────────────────────────────────────────────

def r4_scores(case: dict) -> dict:
    """R4 metrics for one case (pure-stdlib, reuses eval.scorer)."""
    answer = case.get("answer", "")
    evidence = "\n".join(case.get("contexts", []))  # cited evidence ≈ retrieved contexts
    out: dict = {}
    if case.get("should_abstain"):
        out["abstention"] = scorer.abstention_outcome(True, scorer.is_abstention(answer))
        return out
    out["answer_correctness"] = scorer.answer_correctness(answer, case.get("expected")).get("ratio")
    out["citation_recall"] = scorer.citation_recall(case.get("citations"), case.get("must_cite")).get("recall")
    fp = scorer.faithfulness_proxy(evidence, case.get("expected"))
    out["faithfulness_proxy"] = fp.get("supported")
    out["abstention"] = scorer.abstention_outcome(False, scorer.is_abstention(answer))
    return out


# ── Ragas (LLM judge) ────────────────────────────────────────────────────────

def _build_judge(judge: str):
    """Return (llm, embeddings) Ragas wrappers for the chosen provider. DashScope
    is the validated path (OpenAI-compatible). Reads keys from env."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    # Bounded timeout + low retries so a judge that doesn't follow Ragas's strict
    # output format surfaces FAST instead of hanging on tenacity backoff storms
    # (notably weaker open judges like qwen via DashScope). Override with
    # RAGAS_JUDGE_TIMEOUT / RAGAS_JUDGE_RETRIES.
    _timeout = int(os.environ.get("RAGAS_JUDGE_TIMEOUT", "30"))
    _retries = int(os.environ.get("RAGAS_JUDGE_RETRIES", "1"))
    if judge == "dashscope":
        base = os.environ.get("DOCAIQ_DASHSCOPE_BASE_URL",
                              "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        key = os.environ.get("DOCAIQ_DASHSCOPE_API_KEY", "")
        chat_model = os.environ.get("RAGAS_JUDGE_MODEL", "qwen-max")
        emb_model = os.environ.get("RAGAS_EMBED_MODEL", "text-embedding-v4")
        llm = ChatOpenAI(model=chat_model, base_url=base, api_key=key, temperature=0,
                         timeout=_timeout, max_retries=_retries)
        emb = OpenAIEmbeddings(model=emb_model, base_url=base, api_key=key)
    elif judge == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        llm = ChatOpenAI(model=os.environ.get("RAGAS_JUDGE_MODEL", "gpt-4o-mini"),
                         api_key=key, temperature=0, timeout=_timeout, max_retries=_retries)
        emb = OpenAIEmbeddings(model=os.environ.get("RAGAS_EMBED_MODEL", "text-embedding-3-small"),
                               api_key=key)
    else:
        raise SystemExit(f"unknown --judge {judge!r} (use dashscope|openai)")
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(emb)


def ragas_scores(cases: list[dict], judge: str, metrics_arg: str) -> dict:
    """Run Ragas over the ANSWERABLE cases. Returns {case_id: {metric: score}}.
    Abstention cases are excluded (a refusal has no faithful 'answer' to judge —
    R4's abstention matrix covers them)."""
    import asyncio

    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        answer_correctness, answer_relevancy, context_precision,
        context_recall, faithfulness,
    )

    # Ragas 0.1.x calls asyncio.get_event_loop() internally; on Python 3.11+/3.14
    # there's no implicit loop in a fresh thread → ensure one exists first.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    all_metrics = {
        "faithfulness": faithfulness,
        "answer_correctness": answer_correctness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    chosen = [m.strip() for m in metrics_arg.split(",") if m.strip()]
    metrics = [all_metrics[m] for m in chosen if m in all_metrics]

    answerable = [c for c in cases if not c.get("should_abstain")]
    rows = [{
        "question": c["question"],
        "answer": c.get("answer", ""),
        "contexts": list(c.get("contexts", [])),
        "ground_truth": c.get("ground_truth", ""),
    } for c in answerable]

    ds = Dataset.from_list(rows)
    llm, emb = _build_judge(judge)
    result = evaluate(ds, metrics=metrics, llm=llm, embeddings=emb, raise_exceptions=False)
    df = result.to_pandas()
    out: dict = {}
    for i, c in enumerate(answerable):
        row = df.iloc[i]
        out[c["id"]] = {m: (float(row[m]) if m in row and row[m] == row[m] else None)
                        for m in chosen if m in df.columns}
    return out


# ── correlation (stdlib Pearson) ─────────────────────────────────────────────

def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 2:
        return None
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    mx, my = sx / n, sy / n
    cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    vx = sum((p[0] - mx) ** 2 for p in pairs)
    vy = sum((p[1] - my) ** 2 for p in pairs)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


# ── report ───────────────────────────────────────────────────────────────────

def _mean(xs):
    vals = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(vals) / len(vals), 4) if vals else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ragas-vs-R4 QA eval (Phase 1)")
    ap.add_argument("--dataset", default=str(_DATASET))
    ap.add_argument("--dry", action="store_true", help="R4 only; skip Ragas/LLM")
    ap.add_argument("--judge", default="dashscope", help="dashscope|openai")
    ap.add_argument("--metrics", default="faithfulness,answer_correctness",
                    help="comma list: faithfulness,answer_correctness,answer_relevancy,context_precision,context_recall")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.dataset))
    r4 = {c["id"]: r4_scores(c) for c in cases}

    rg: dict = {}
    if not args.dry:
        try:
            rg = ragas_scores(cases, args.judge, args.metrics)
        except ImportError as e:
            print(f"[ragas unavailable: {e}] — install requirements-eval.txt or use --dry")
            args.dry = True
        except Exception as e:  # noqa: BLE001
            print(f"[ragas run failed: {type(e).__name__}: {e}] — showing R4 only")
            args.dry = True

    # proxy-vs-judge correlation (the headline)
    ids = [c["id"] for c in cases if not c.get("should_abstain")]
    corr = None
    if rg:
        xs = [r4[i].get("faithfulness_proxy") for i in ids]
        ys = [rg.get(i, {}).get("faithfulness") for i in ids]
        corr = pearson(xs, ys)

    report = {
        "n_cases": len(cases),
        "n_answerable": len(ids),
        "judge": None if args.dry else args.judge,
        "r4_means": {
            "answer_correctness": _mean([r4[i].get("answer_correctness") for i in ids]),
            "citation_recall": _mean([r4[i].get("citation_recall") for i in ids]),
            "faithfulness_proxy": _mean([r4[i].get("faithfulness_proxy") for i in ids]),
        },
        "ragas_means": ({m: _mean([rg.get(i, {}).get(m) for i in ids])
                         for m in args.metrics.split(",")} if rg else None),
        "abstention": {
            "outcomes": {o: sum(1 for c in cases if r4[c["id"]].get("abstention") == o)
                         for o in ("correct_abstain", "missed_abstain", "false_abstain", "answered")},
        },
        "faithfulness_proxy_vs_ragas_pearson": (round(corr, 3) if corr is not None else None),
    }

    if args.json:
        print(json.dumps({"summary": report, "r4": r4, "ragas": rg}, indent=2))
        return 0

    print("=" * 64)
    print(f"DocAIQ R4 ⟷ Ragas — Phase 1  ({report['n_cases']} cases, "
          f"{report['n_answerable']} answerable, judge={report['judge']})")
    print("=" * 64)
    print("\nR4 (deterministic) means:")
    for k, v in report["r4_means"].items():
        print(f"  {k:22} {v}")
    if report["ragas_means"]:
        print("\nRagas (LLM judge) means:")
        for k, v in report["ragas_means"].items():
            print(f"  {k:22} {v}")
        print(f"\nfaithfulness  proxy⟷judge Pearson r = {report['faithfulness_proxy_vs_ragas_pearson']}")
    print("\nAbstention (R4):")
    for k, v in report["abstention"]["outcomes"].items():
        print(f"  {k:22} {v}")
    if not report["ragas_means"]:
        print("\n(Ragas skipped — run without --dry and with requirements-eval.txt + a key for LLM-judge metrics.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
