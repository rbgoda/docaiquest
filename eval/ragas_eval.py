"""DocAIQ eval CLI — run R4 (stdlib) and optional Ragas LLM-judge metrics on the
same golden set.

  python -m eval.ragas_eval --dry                         # R4 only, no deps/LLM
  python -m eval.ragas_eval --judge dashscope             # + Ragas (needs venv + key)
  python -m eval.ragas_eval --judge dashscope --json      # machine-readable
Flags: --dataset PATH · --dry · --judge dashscope|openai · --metrics a,b,c · --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from eval import scorer

_DEFAULT_DATASET = Path(__file__).parent / "dataset" / "ragas_qa.json"
_DEFAULT_METRICS = "faithfulness,answer_correctness"


def load_dataset(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["cases"] if isinstance(data, dict) and "cases" in data else data


def _judge_llm(judge: str):
    """A langchain chat model for the Ragas judge. dashscope = the credit-reliable
    OpenAI-compatible endpoint (default); openai = a real OpenAI key."""
    from langchain_openai import ChatOpenAI
    # Fail-fast: bound each request so a slow/misbehaving judge endpoint can't hang the
    # whole run (the DashScope OpenAI-compat judge is known-flaky — see EVAL_RAGAS.md).
    to = float(os.getenv("RAGAS_REQUEST_TIMEOUT", "45"))
    if judge == "openai":
        model = os.getenv("RAGAS_JUDGE_MODEL", "gpt-4o-mini")
        # OPENAI_BASE_URL lets this route through any OpenAI-compatible gateway (e.g.
        # OpenRouter → openai/gpt-4o-mini) when there's no direct OpenAI key.
        return ChatOpenAI(model=model, api_key=os.environ["OPENAI_API_KEY"], temperature=0,
                          timeout=to, max_retries=1, base_url=os.getenv("OPENAI_BASE_URL") or None)
    # dashscope (OpenAI-compatible)
    key = os.getenv("DOCAIQ_DASHSCOPE_API_KEY") or os.environ["DASHSCOPE_API_KEY"]
    base = os.getenv("DOCAIQ_DASHSCOPE_BASE_URL",
                     "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    model = os.getenv("RAGAS_JUDGE_MODEL", "qwen-max")
    return ChatOpenAI(model=model, api_key=key, base_url=base, temperature=0,
                      timeout=to, max_retries=1)


def _judge_embeddings(judge: str):
    from langchain_openai import OpenAIEmbeddings
    if judge == "openai":
        return OpenAIEmbeddings(model=os.getenv("RAGAS_EMBED_MODEL", "text-embedding-3-small"),
                                api_key=os.environ["OPENAI_API_KEY"])
    key = os.getenv("DOCAIQ_DASHSCOPE_API_KEY") or os.environ["DASHSCOPE_API_KEY"]
    base = os.getenv("DOCAIQ_DASHSCOPE_BASE_URL",
                     "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    return OpenAIEmbeddings(model=os.getenv("RAGAS_EMBED_MODEL", "text-embedding-v3"),
                            api_key=key, base_url=base)


def run_ragas(cases: list[dict], judge: str, metric_names: list[str]) -> dict:
    """Best-effort Ragas run. Returns {} (with a printed reason) if unavailable."""
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas import metrics as R
    except ImportError:
        print("  (ragas not installed — skipping LLM-judge metrics; "
              "pip install -r backend/requirements-eval.txt in a venv)", file=sys.stderr)
        return {}
    name_map = {"faithfulness": R.faithfulness, "answer_correctness": R.answer_correctness,
                "answer_relevancy": R.answer_relevancy, "context_precision": R.context_precision,
                "context_recall": R.context_recall}
    metrics = [name_map[m] for m in metric_names if m in name_map]
    # Ragas answerable metrics don't apply to abstention cases — exclude them.
    rows = [{"question": c["question"], "answer": c.get("answer", ""),
             "contexts": c.get("contexts") or [], "ground_truth": c.get("ground_truth", "")}
            for c in cases if not c.get("should_abstain")]
    if not rows:
        print("  (no answerable cases for Ragas)", file=sys.stderr)
        return {}
    # Ragas 0.1.x uses asyncio internally and calls get_event_loop(), which raises on
    # Python 3.12+ when no loop is set in the main thread. Ensure one exists.
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    # Only faithfulness/answer_relevancy-style metrics that need an embeddings model get
    # one — faithfulness itself is LLM-only, so a gateway without an embeddings endpoint
    # (e.g. OpenRouter) still works for --metrics faithfulness.
    _EMB_METRICS = {"answer_relevancy", "context_precision", "context_recall", "answer_correctness"}
    emb = _judge_embeddings(judge) if any(m in _EMB_METRICS for m in metric_names) else None
    # Serialize calls for rate-limited judges (free OpenRouter models 429 on Ragas's
    # concurrent burst). RAGAS_MAX_WORKERS=1 keeps calls under the limit (slower).
    from ragas.run_config import RunConfig
    rc = RunConfig(max_workers=int(os.getenv("RAGAS_MAX_WORKERS", "4")),
                   timeout=int(float(os.getenv("RAGAS_REQUEST_TIMEOUT", "45"))))
    try:
        result = evaluate(Dataset.from_list(rows), metrics=metrics,
                          llm=_judge_llm(judge), embeddings=emb, run_config=rc)
        return {k: round(float(v), 4) for k, v in result.items() if isinstance(v, (int, float))}
    except Exception as e:  # noqa: BLE001
        print(f"  (ragas run failed: {type(e).__name__}: {str(e)[:160]})", file=sys.stderr)
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval.ragas_eval")
    ap.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    ap.add_argument("--dry", action="store_true", help="R4 only — no LLM judge")
    ap.add_argument("--judge", choices=["dashscope", "openai"], default=None)
    ap.add_argument("--metrics", default=_DEFAULT_METRICS)
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args(argv)

    cases = load_dataset(a.dataset)
    r4 = scorer.score_dataset(cases)
    ragas_scores = {}
    if a.judge and not a.dry:
        ragas_scores = run_ragas(cases, a.judge, [m.strip() for m in a.metrics.split(",") if m.strip()])

    report = {"dataset": str(a.dataset), "n": r4["cases"], "r4": {k: v for k, v in r4.items() if not k.startswith("_")},
              "ragas": ragas_scores}
    if a.as_json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"\nDocAIQ eval · {a.dataset.name} · {r4['cases']} cases\n" + "─" * 48)
    print("R4 (deterministic, stdlib):")
    print(f"  faithfulness_proxy        {r4['faithfulness_proxy']}")
    print(f"  answer_correctness_proxy  {r4['answer_correctness_proxy']}")
    print(f"  citation_recall           {r4['citation_recall']}")
    print(f"  abstention_accuracy       {r4['abstention_accuracy']}  {r4['abstention_matrix']}")
    if ragas_scores:
        print("Ragas (LLM judge):")
        for k, v in ragas_scores.items():
            print(f"  {k:<26}{v}")
    elif a.judge and not a.dry:
        print("Ragas: skipped (see notes above)")
    print("─" * 48)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
