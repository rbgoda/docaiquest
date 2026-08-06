# DocAIQ — local POC / deep-testing setup

A reusable local test bed for running the pipeline across many real document types and
scoring it. **The fixtures (real docs + extracted metadata) contain PII and are NOT in
git** — they live under `eval/fixtures/` (gitignored) on the test machine.

## The persistent test user
An **enterprise** (unlimited-docs) user drives the tests so the trial 7-doc cap never
throttles a run:

```
email:    evaltest@example.com
password: Eval-passw0rd!
plan:     enterprise (set locally)
```
Create/promote it once (already done on this machine):
```bash
# register via the app, then:
docker exec docaiq-docmod-postgres-1 psql -U docaiq -d docaiq \
  -c "UPDATE users SET plan='enterprise' WHERE email='evaltest@example.com';"
```

## Fixtures (local-only, gitignored)
- `eval/fixtures/docs/` — the source test documents (copied from `~/Downloads/testdata`).
- `eval/fixtures/testset_meta.json` — per-doc extracted metadata (detected type, trust,
  fields, confidence) + a `typeCoverage` map. Regenerate any time from the running app.
- `eval/dataset/testdata_qa.json` — the generated Q&A eval dataset (also gitignored).

## Run the full test set
```bash
# 1) ingest all docs + chat a use-case question bank → dataset + coverage report
python -m eval.gen_from_testdata eval/fixtures/docs \
    --email evaltest@example.com --password 'Eval-passw0rd!' \
    --out eval/dataset/testdata_qa.json --limit 50 --ready-timeout 240

# 2) snapshot per-doc extracted metadata for future/regression testing
python -m eval.export_testset --email evaltest@example.com --password 'Eval-passw0rd!' \
    --out eval/fixtures/testset_meta.json

# 3) score
python -m eval.ragas_eval --dataset eval/dataset/testdata_qa.json --dry
python -m eval.ragas_eval --dataset eval/dataset/testdata_qa.json --judge openai   # real faithfulness
```

## Use cases exercised
Per document: **classification** (detected doc type), **extraction** (fields + per-field
confidence + trust score), **cited Q&A** (source, key dates/amounts/ids, summary), and an
**abstention probe** (an off-topic question the answer should refuse). See `EVAL_RAGAS.md`
for the metrics.

> Ingested docs also persist in the local Postgres under `evaltest@example.com`, so the
> UI (`http://localhost:8085`) can be used to inspect any case by hand.
