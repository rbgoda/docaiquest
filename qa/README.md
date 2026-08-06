# DocAIQuest QA Suite

A living chat-quality test suite for DocAIQuest — both AI and human keep testing against it.

- **doc_types.csv** — all 129 document types (domain, slug, label, schema fields, page support).
- **qa_data.json** — seeded question bank (15 cross-doc + per-type; 1088 total).
- **qa-tracker.html** — interactive tracker (published as an Artifact). Columns: Type · Question ·
  Status (Untested/Pass/Fail/Flaky) · Issue · Resolution. Inline-editable, saved in-browser
  (localStorage), add-question + export.

## Page support
Engine processes up to **50 pages/document**. Plan caps: **Free = 1 page/doc** (test tier),
**Trial/Pro/Enterprise = no per-doc cap** (up to 50).

## Workflow
1. Open the tracker → pick a type (or start with Cross-document).
2. Ask each question in the app chat; set the status pill; note the Issue if wrong.
3. Export the JSON → hand to the AI to fix the failures → re-test.
