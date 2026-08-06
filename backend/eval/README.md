# DocAIQ eval harness (Reducto-parity G2)

The **ruler** for parsing / extraction / retrieval quality. Build it before
changing any parsing so every later change (G1, G3–G11) is measured, not guessed.
See `docs/REDUCTO_PARITY_ROADMAP.md`.

Pure stdlib — no DB, no LLM, no third-party deps — so it runs in CI and offline.

## Run

```bash
cd backend
python -m eval.run            # human report over eval/dataset/manifest.json
python -m eval.run --k 5      # hit@k cutoff
python -m eval.run --json     # machine-readable (for CI gating / trend tracking)
```

## Metrics (`eval/scorer.py`)

| Metric | What | Reducto-parity item |
|---|---|---|
| `field_prf` | field-level precision / recall / F1 (lenient normalized match) | extraction |
| `cer` | OCR character error rate vs reference text | G3, G6, G11 |
| `table_cell_f1` | multiset cell F1 (order-insensitive) | G8 |
| `hit_at_k`, `reciprocal_rank` | retrieval hit@k / MRR | G1, G5 |

String comparison is lenient (lowercase, strip punctuation, collapse whitespace)
— but digit-grouping differences in money *are* caught (`12,420.00` ≠ `12420.00`).

## Dataset format

`dataset/manifest.json` lists cases. Each case names an `expected` and (offline)
a `predicted` JSON. A case provides any subset of:

- `fields`     — `{field_name: value}` the extractor should produce
- `reference_text` (expected) / `ocr_text` (predicted) — for CER
- `table_cells` — `[[row cells], ...]`
- `retrieval`  — expected: `[{query, relevant: [chunk_id]}]`; predicted: `[{query, ranked: [chunk_id]}]`
- `qa` (R4) — faithfulness / abstention. expected: `{question, expected:[key-facts],
  must_cite:[chunk_id], should_abstain}`; predicted: `{answer, citations:[chunk_id],
  evidence, abstained?}`. Scores answer-correctness, faithfulness (expected key-facts present
  in the cited evidence), citation recall, and an **abstention confusion matrix**
  (answered / correct_abstain / missed_abstain / false_abstain). `abstained` auto-detects
  from an `INSUFFICIENT_EVIDENCE` answer when omitted.

Only metrics present in **both** expected and predicted are scored.

## QA / faithfulness cases (R4) — pairs with your `questions.txt`

To validate chat trust, add a `qa` case per question. Include some
`should_abstain: true` questions the docs *can't* answer — the harness checks the chat both
**answers the answerable** and **refuses the unanswerable**, catching hallucinations as
`missed_abstain` and over-refusals as `false_abstain`.

## Growing the set

Add real labeled docs — prioritise variety that stresses the open gaps:
- **scanned / JPG / handwritten** → OCR CER (G3/G6/G11)
- **table-heavy** (bank/CC statements, multi-page tables) → table F1 (G8)
- **multi-language** → OCR + extraction
- **long structured docs** → retrieval hit@k (G5 chunking)

## Live mode (TODO — G2 follow-up)

`--live` will run the real `ingestion` + `fact_extractor` + `retrieval` pipeline
on each source doc and produce `predicted` automatically (needs a DB + provider
keys). Today it returns exit code 2. Offline mode is the CI-safe contract.
