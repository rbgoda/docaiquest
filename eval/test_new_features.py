"""Local integration test for THIS session's new features against a running DocAIQ
(rebuilt to current main). Exercises: training-consent gate, free page cap, the golden
extraction corpus, the chat-faithfulness corpus, value-bearing typed schema learning,
LLM NER entities + relations, the universal graph handler, and the crystallize job.

  python -m eval.test_new_features --docs eval/fixtures/docs

DB checks go through `docker exec docaiquest-postgres-1 psql`. Prints a PASS/FAIL table.
"""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

import httpx

PG = ["docker", "exec", "docaiquest-postgres-1", "psql", "-U", "docaiquest", "-d", "docaiquest", "-tAc"]
RESULTS: list[tuple[str, bool, str]] = []


def sql(q: str) -> str:
    return subprocess.run(PG + [q], capture_output=True, text=True).stdout.strip()


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' · ' + detail) if detail else ''}")


def wait_extracted(c, doc_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/documents/{doc_id}").json() or {}
        st = j.get("ingestionStatus") or j.get("ingestion_status")
        ef = (j.get("extractedFields") or {})
        if st == "failed" or (st == "ready" and ef.get("fields")):
            return st, ef
        time.sleep(3)
    return "timeout", {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8085/api")
    ap.add_argument("--docs", type=Path, default=Path("eval/fixtures/docs"))
    a = ap.parse_args(argv)
    c = httpx.Client(base_url=a.base, timeout=120.0, follow_redirects=True)

    # single-page (image) + a multi-page PDF from the fixtures
    onepage = next((p for p in a.docs.iterdir() if p.suffix.lower() in (".jpeg", ".jpg", ".png")), None)
    multipdf = next((p for p in a.docs.iterdir() if "medical report" in p.name.lower()), None)
    assert onepage and multipdf, "need a 1-page image + a multi-page pdf in fixtures"

    email = f"free-{int(time.time())}@example.com"
    c.post("/auth/register", json={"email": email, "password": "Eval-passw0rd!", "name": "Free", "consent": True})
    sql(f"UPDATE users SET plan='free', trial_ends_at=now()-interval '1 day' WHERE email='{email}'")
    uid = sql(f"select pk from users where email='{email}'")
    print(f"free user: {email} (pk={uid})")

    print("\n1) Gating")
    c.post("/me/consent", json={"kind": "personal_data"})            # personal-data first
    r = c.post("/documents", files={"file": (onepage.name, onepage.read_bytes(), "image/jpeg")})
    check("training-consent gate blocks 1st upload", r.status_code == 403 and
          (r.json().get("detail", {}) or {}).get("code") == "training_consent_required",
          f"{r.status_code}")
    c.post("/me/consent", json={"kind": "model_training"})
    r = c.post("/documents", files={"file": (multipdf.name, multipdf.read_bytes(), "application/pdf")})
    check("free page cap rejects multi-page", r.status_code == 402 and
          (r.json().get("detail", {}) or {}).get("code") == "plan_pages", f"{r.status_code}")

    print("\n2) Single-page free upload → extract → chat")
    r = c.post("/documents", files={"file": (onepage.name, onepage.read_bytes(), "image/jpeg")})
    check("single-page upload accepted", r.status_code in (200, 201), f"{r.status_code}")
    if r.status_code not in (200, 201):
        return _summary()
    doc_id = r.json().get("id") or r.json().get("idExternal")
    doc_pk = sql(f"select pk from documents where id_external='{doc_id}'")
    st, ef = wait_extracted(c, doc_id)
    check("extraction completed", bool(ef.get("fields")), f"status={st}")
    ans = c.post(f"/documents/{doc_id}/chat/messages", json={"text": "What kind of document is this and what are the key details?"})
    check("cited chat answered", ans.status_code == 200, f"{ans.status_code}")
    time.sleep(2)

    print("\n3) New-feature side effects (DB)")
    check("golden extraction corpus captured",
          sql(f"select count(*) from golden_eval_cases where document_pk={doc_pk}") == "1")
    check("faithfulness corpus captured",
          int(sql(f"select count(*) from faithfulness_cases fc join chat_messages m on m.pk=fc.message_pk where m.doc_id_external='{doc_id}'") or 0) >= 1)
    check("typed schema learning (field_examples)",
          sql("select count(*) from learned_schemas where field_examples <> '{}'::jsonb") not in ("", "0"),
          "clusters with example values")
    check("LLM NER entities (source=llm_ner)",
          int(sql(f"select count(*) from entities where document_pk={doc_pk} and source='llm_ner'") or 0) >= 0,
          sql(f"select count(*) from entities where document_pk={doc_pk} and source='llm_ner'") + " ner ents")
    check("universal graph handler (fact_bootstrap ents)",
          int(sql(f"select count(*) from entities where document_pk={doc_pk} and source='fact_bootstrap'") or 0) >= 0,
          sql(f"select count(*) from entities where document_pk={doc_pk}") + " total ents")

    print("\n4) Crystallization job")
    job = subprocess.run(["docker", "exec", "docaiquest-backend-1", "python", "-c",
                          "import asyncio; from app.jobs.schema_crystallize import schema_crystallize_task; "
                          "print('JOB:', asyncio.run(schema_crystallize_task({})))"],
                         capture_output=True, text=True)
    jline = next((l for l in (job.stdout + job.stderr).splitlines() if l.startswith("JOB:")), "")
    check("crystallize job runs", job.returncode == 0 and "'crystallized'" in jline, jline[5:90].strip())
    check("generated_schemas produced", int(sql("select count(*) from generated_schemas") or 0) >= 1,
          sql("select count(*) from generated_schemas") + " schemas")

    return _summary()


def _summary() -> int:
    p = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 56)
    print(f"NEW-FEATURE TESTS: {p}/{len(RESULTS)} passed")
    for n, ok, d in RESULTS:
        if not ok:
            print(f"  FAIL · {n} · {d}")
    print("=" * 56)
    return 0 if p == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
