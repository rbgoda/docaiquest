# DocAIQ FAQ

This is the canonical FAQ. It powers the in-app Help drawer (the **?** icon in the top bar) — each `### Q:` heading becomes one collapsible entry, filtered by the user's role.

If you add a question, follow the format: `### Q: <question text>` with optional `**Role:** owner|admin|reviewer|vendor|all` immediately under the heading. If `Role:` is omitted the entry is shown to everyone.

---

## Getting started

### Q: What is DocAIQ?
**Role:** all

A multi-agentic, graph-backed, RAG-grounded auto-audit platform. AI agents do the first-pass evidence matching across hundreds of compliance requirements (SOC 2, ISO 27001, HIPAA, PCI-DSS, KYC, etc.), then surface uncertain cases to human auditors with reasoning traces, cited evidence spans, and side-by-side document comparisons.

### Q: Who are the four roles and what do they see?
**Role:** all

- **Owner** · superuser inside their tenant. Sees everything. Manages billing, team, framework packs, LLM routing.
- **Admin** · creates audits, manages vendors and frameworks, configures LLM routing. Can't manage billing.
- **Reviewer** · works through the Review screen. Approves/rejects evidence, opens RFIs, closes audits. Scoped to assigned audits.
- **Vendor** · external — sees ONLY their own portal. Uploads evidence, responds to RFIs. Cannot see other vendors or admin views.

### Q: How is each tenant isolated from other tenants?
**Role:** all

Three layers, each fail-closed: (1) each container has its own `DOCAIQ_JWT_SECRET` so foreign cookies don't decode, (2) `get_current_user` 401s when JWT `org_id` ≠ container `tenant_id`, (3) every repository query filters by tenant. Storage inherits this — each tenant gets its own MinIO bucket.

---

## For Owners and Admins

### Q: How do I onboard a new vendor?
**Role:** admin

Three paths:
1. **From the Dashboard wizard**: + New audit run → step 1 "New vendor" tab → name + primary reviewer email → continue. Creates the vendor row as part of audit creation.
2. **From the Vendors page**: + Add vendor (no audit yet).
3. **Invite vendor users**: Settings → Team → invite with `vendor` role + vendor binding. They get an email with temp password; on first login they only see the Vendor Portal scoped to their vendor.

### Q: What's a "subject" and when do I use one?
**Role:** admin

A subject is a named natural person an audit is for — director, UBO, beneficial owner, applicant. KYC-style audits use subjects to bind requirements to specific persons. The matcher rejects documents that don't pertain to a listed subject.

Add subjects via the violet `SUBJECTS` pill row on the audit detail header (Review tab) or during audit creation in the New Audit wizard.

### Q: What are aliases?
**Role:** admin

Alternative names for the same person. An Indian passport says `GODA RAJESH BALVANTRAI` (surname first), an Aadhaar says `Rajesh Balvantrai Goda`, and the person goes by `Rajesh Goda`. Add all three as aliases on one subject row — the matcher accepts any form when matching extracted holder names against the subject.

### Q: How do I add a framework that isn't built-in?
**Role:** admin

Settings → Audit Frameworks → + Upload pack. CSV format: `id, group, title, subtitle, required_docs, match_prompt, status`. See `public/samples/frameworks/` for examples. The pack is tenant-local once uploaded.

To customise a built-in pack (e.g. trim KYC to 4 of its 36 controls), click **Customize** on the pack card → uncheck the controls you don't want → Save as new pack. The original stays untouched; your fork is tenant-local.

### Q: How do I customise the AI prompt the matcher uses per requirement?
**Role:** admin

Open the requirement (Settings → Requirements drawer OR via the matcher view) → edit `match_prompt`. Empty = generic prompt. Set to something specific: *"Look for an unexpired ISO 27001:2022 certificate, must include the company name 'Acme Inc' and a Big 4 audit firm signature."* The matcher uses this verbatim in the validator call.

### Q: How do I add a new model to the LLM cascade?
**Role:** admin

Settings → LLM Routing. Each tier shows its models with weight + status. Add via the tier's `+ Model` button — paste a provider-prefixed model ID like `openrouter/anthropic/claude-haiku-4.5` or `google/gemini-2.5-flash` or `anthropic/claude-haiku-4.5`. Save.

### Q: Why is my Gemini quota hit so fast?
**Role:** admin

The "1500 RPM" figure on Google's marketing page isn't the binding constraint — the **20 requests per DAY per model** on the free tier is. A single matcher run on a 4-requirement audit with 3 docs fires 12 LLM calls; three runs in a day exhausts the free quota.

Options: (1) spend $10+ on Gemini API → project upgrades to Tier 1 with 200 RPD, (2) stay on free + rely on the OpenRouter fallback Tier 3 — when Gemini hits RPD the matcher falls through automatically.

### Q: When I close an audit, what actually changes?
**Role:** admin

(1) `closed_at` is set. (2) An `audit_history` snapshot is written with the verdict tally and reviewer of record. (3) Open RFIs auto-resolve. (4) The audit moves from "active" to the History tab. The requirement attachments are preserved — closing doesn't detach docs.

### Q: How do I reassign a reviewer mid-audit?
**Role:** admin

Open **Reviewers** in the sidebar → expand the current reviewer's row → each audit has a *Reassign* button → pick the new reviewer. Same control on each "Unassigned audits" row at the top. Endpoint: `PATCH /api/audit-runs/{id}/lead-reviewer`.

### Q: New tenant setup — what's automated vs manual?

Automated on signup: random JWT secret, port assignment, container provisioning, DB migrations, framework pack seed, LLM key forwarding (OpenRouter / Anthropic / Google), nginx vhost regeneration, TLS, owner account + temp password email.

Manual: top up LLM quota when free tier runs out; load any framework packs the tenant wants beyond defaults.

---

## For Reviewers

### Q: Why didn't my document match this requirement?
**Role:** reviewer

Five possible reasons (in order of likelihood):
1. **Subject mismatch** — doc is for a person who isn't in the audit's `SUBJECTS` row.
2. **Family mismatch** — wrong doc type for this requirement (e.g. passport ≠ address proof).
3. **Country mismatch** — req mentions a specific country; doc is from another.
4. **LLM confidence below 0.85** — model wasn't sure. Check the "Why this match?" modal for the reasoning.
5. **No evidence retrieved** — BM25 + cosine both returned no chunks. Usually means the doc lacks relevant text or embeddings are off.

Click **"Why this match?"** on the requirement to see the actual rejection reason.

### Q: Can ONE document satisfy MULTIPLE requirements?
**Role:** reviewer

Yes — the EVIDENCE pill row on the requirement shows ALL docs backing it. For example, a passport backs both "Photo ID" (ID-01) AND "Date of birth on file" (ID-04). The "primary" doc has the gold pill; additional evidence has violet pills.

This works the other way too — one passport can land in the evidence list of multiple requirements without re-uploading.

### Q: When I chat with the AI, what documents does it see?
**Role:** reviewer

The chat is **scoped to the doc currently attached to the requirement you're on**. If you ask "what's the DOB on this Aadhaar?", the LLM ONLY sees chunks from that Aadhaar — not from any other doc in the tenant.

If no doc is attached, the chat falls back to all docs in this audit but the system prompt explicitly forbids cross-doc summaries. You'll never see "doc A says X but doc B says Y" — that's a deliberate guard against confused multi-source answers.

### Q: I asked about "Rajesh's DOB" on a doc for someone else, and the chat refused. Why?
**Role:** reviewer

That's the **identity guard** working as designed. When you ask about a specific named person and the document is for a different person, the chat refuses with "This document is for [actual holder], not [name you asked about]." This prevents the chat from leaking the wrong person's data as if it belonged to whom you asked about.

If you genuinely want to chat about the document's actual holder, phrase the question without naming a different person — e.g. "What's the DOB on this passport?" instead of "What's Rajesh's DOB?"

### Q: What does the confidence score actually mean?
**Role:** reviewer

`Confidence = P(this document satisfies this requirement)` — the LLM's own probability estimate. Not how grounded the answer is, not how sure the LLM feels.

- ≥ 0.85 → auto-attach
- 0.60 – 0.84 → close call, needs human review
- 0.40 – 0.59 → tangential, probably wrong
- < 0.40 → off-topic / contradicts

A confident "NO" gets `confidence = 0.05`, not 0.95 — the score is about requirement-met probability, not LLM certainty.

### Q: I rejected a match — what happens to the document?
**Role:** reviewer

Verdict = Reject sets `audit_run_requirements.verdict = 'reject'` so the audit report shows it as non-compliant. The document attachment stays for the audit trail — closing an audit later includes the rejected doc in the history snapshot.

To DETACH the doc and re-pin a different one, use the document picker on the requirement row (admin manual attach UI).

### Q: How do I attach a doc manually when the matcher missed it?
**Role:** reviewer

In the requirement's right panel (where "No matching document found" is shown), click **"Attach a document manually"**. Pick from the list of ready docs in the tenant. The attachment lands as `source: manual` in the evidence list — same shape as AI matches, just attributed to you.

### Q: How do I close an audit?
**Role:** reviewer

Top-right of the Review screen → **Close audit** button (visible to admin + lead reviewer). Pre-flight checks: every requirement must have a verdict (approve / reject / needs-info). Reasons for blocks surface inline. Confirm → audit moves to History tab + `audit_history` snapshot is written.

### Q: What's an RFI?
**Role:** reviewer

Request For Information — a question to the vendor about a specific requirement. Raise from the Review screen (the RFI chip near each requirement) or the Requests-to-vendor tab. The vendor sees it in their portal with a chip on the affected requirement; they reply inline. Lifecycle: `open → responded → resolved`.

---

## For Vendors

### Q: How do I upload evidence?
**Role:** vendor

Go to your Vendor Portal (the only top-level view you see). Each audit row has an **Upload** button per requirement. Drag-and-drop or click to pick files. Supported formats: PDF, JPEG, PNG, WebP, HEIC, CSV, Word (.docx), Excel (.xlsx), plain text, email (.eml).

### Q: Can I upload a whole folder?
**Role:** vendor

Yes — drag a folder onto the upload zone. Up to 4 files process in parallel. Large folders (50+ files) take 3-15 minutes total; you'll see progress per file.

### Q: What happens after I upload a PDF?
**Role:** vendor

Backend pipeline runs in ~30-60 seconds:
1. **Stored** in tenant blob storage (encrypted at rest).
2. **Parsed** — text extracted (with vision OCR for scanned PDFs).
3. **Chunked + embedded** for retrieval.
4. **Classified** — AI guesses doc type (certificate, policy, ID, etc).
5. **Fields extracted** — typed data like expiry dates, party names, signatures.
6. **Matched** — for each requirement on your audit, AI asks "does this doc satisfy you?". Auto-attaches at high confidence.

You see status flip from `pending → processing → ready` in the Evidence tab. Matches appear as attachment chips on the requirements.

### Q: Can I see all requirements I'm being audited against?
**Role:** vendor

The Evidence tab shows the full requirement list for each of your audits. Expand each framework to see every requirement plus its current evidence + status (todo / warn / miss / OK). You see only YOUR vendor's audits — never other vendors in the same tenant.

### Q: The AI gave a wrong answer about my document — what do I do?
**Role:** vendor

Two options:
1. **Raise an RFI back to your auditor** describing the wrong answer. They can adjust the matcher's prompt for that requirement, or manually re-attach the right doc.
2. **Re-upload a clearer version** of the document. The matcher re-runs on every upload and may correct itself with better OCR input.

### Q: Why does my passport scan show the wrong name?
**Role:** vendor

The vision OCR (the AI reading your image) is imperfect on low-quality scans. If the holder name field is showing as garbled, the image is likely too low-resolution or angled. Re-take the photo: flat lighting, full frame, no shadows over text — should resolve.

---

## When things break

### Q: I see "LLM cascade failed" — what now?

All configured providers failed for that call. Three likely causes: (1) free-tier daily quota hit, (2) invalid model ID in routing config, (3) API key expired. Settings → LLM Routing → check the tier model IDs are valid. Wait 60s and retry — most 429s clear within a minute (daily quotas reset ~midnight in the provider's TZ).

### Q: Upload says "Unsupported file type"

The platform accepts: PDF, JPEG, PNG, WebP, GIF, HEIC, AVIF, CSV, DOCX, XLSX, TXT, MD, EML. Legacy `.doc` and `.xls` are NOT supported — re-save as `.docx` or `.xlsx` and re-upload.

### Q: My document is stuck on "pending" for 10+ minutes

Worker likely crash-looped on a stale Redis connection. Admin: SSH to the host, restart the worker container (`docker restart docaiq-<tenant>-worker-1`). The doc will resume processing on the next poll.

### Q: I can't log in — keeps redirecting to login

Cookie is being rejected. Try: (1) clear cookies for this domain, (2) check you're on HTTPS (Secure cookie won't send on HTTP), (3) check your tenant's URL is `<slug>.docaiq.jicama.tech` not the bare `docaiq.jicama.tech`.

---

## Where to find more

- **Architecture overview**: `docs/ARCHITECTURE.md`
- **Full handbook (this is the source of truth)**: `docs/HANDBOOK.md`
- **Reviewer workflow guide**: `docs/REVIEWER_GUIDE.md`
- **Data model details**: `docs/DATA_MODEL.md`
- **LLM provider config**: `docs/LLM_PROVIDERS.md`
