#!/usr/bin/env bash
# Local E2E test for the supercharged agentic workspace chat (power tools +
# step trace + CSV). Throwaway user, throwaway docs — never the real account.
set -euo pipefail
BASE="http://localhost:8085/api"
JAR="$(mktemp)"
RND="$(date +%s)$RANDOM"
EMAIL="t-${RND}@example.com"
PASS="Testpass123!"
say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$*"; }

say "register + login ($EMAIL)"
curl -s -c "$JAR" -X POST "$BASE/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"name\":\"Tester\",\"consent\":true}" >/dev/null
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" >/dev/null
curl -s -b "$JAR" "$BASE/me" | python3 -c 'import sys,json;u=json.load(sys.stdin);print("  user pk",u.get("id"),u.get("email"))'

say "record data consent"
for k in processing personal_data; do
  curl -s -b "$JAR" -X POST "$BASE/me/consent" -H 'Content-Type: application/json' -d "{\"kind\":\"$k\"}" >/dev/null
done

say "upload 2 throwaway invoice docs"
TMPD="$(mktemp -d)"
cat > "$TMPD/acme_invoice.txt" <<'EOF'
ACME CONSULTING — INVOICE
Invoice Number: INV-2026-001
Invoice Date: 2026-03-14
Bill To: Tester
Total Amount Due: USD 12,420.00
Currency: USD
EOF
cat > "$TMPD/globex_invoice.txt" <<'EOF'
GLOBEX LLC — INVOICE
Invoice Number: INV-7788
Invoice Date: 2026-04-02
Bill To: Tester
Total Amount Due: USD 3,150.50
Currency: USD
EOF
for f in acme_invoice globex_invoice; do
  curl -s -b "$JAR" -X POST "$BASE/documents" -F "file=@$TMPD/$f.txt;type=text/plain" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  uploaded",d.get("id") or d,d.get("name") if isinstance(d,dict) else "",d.get("ingestionStatus") if isinstance(d,dict) else "")'
done

say "wait for ingestion (ready + extracted fields)"
# GET /documents returns a dict keyed by doc id → values() are the documents.
for i in $(seq 1 40); do
  OUT="$(curl -s -b "$JAR" "$BASE/documents")"
  READY="$(echo "$OUT" | python3 -c 'import sys,json;d=json.load(sys.stdin);docs=list(d.values()) if isinstance(d,dict) else d;print(sum(1 for x in docs if x.get("ingestionStatus")=="ready"),len(docs))')"
  echo "  ready/total: $READY (poll $i)"
  [ "${READY%% *}" = "2" ] && break
  sleep 3
done

RESP="$(mktemp)"
ask() {
  say "ASK: $1"
  curl -s -b "$JAR" -X POST "$BASE/workspace-chat/messages" \
    -H 'Content-Type: application/json' \
    -d "{\"text\":$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")}" > "$RESP"
  RESP="$RESP" python3 - <<'PY'
import os,json
m=json.load(open(os.environ["RESP"]))
print("  meta:",m.get("meta"))
print("  answer:\n   ",(m.get("text") or "").replace("\n","\n    ")[:1200])
tr=m.get("trace") or []
print("  trace (%d steps):"%len(tr))
for s in tr: print("    [%s] %-18s %s (%sms)"%(s.get("status"),s.get("tool"),s.get("summary"),s.get("ms")))
for a in (m.get("artifacts") or []):
    print("  artifact:",a.get("type"),a.get("filename"),"(%d bytes)"%len(a.get("content") or ""))
    print("   ",(a.get("content") or "").replace("\n","\n    ")[:400])
PY
}

ask "Build a table of all my invoices with their invoice number and total amount. Give me a CSV."
ask "Compare my two invoices."
ask "Create a group called Receipts 2026"

echo
echo "(throwaway user $EMAIL left in local DB; harmless)"
rm -rf "$TMPD" "$JAR"
