"""Graph RAG layer (Layer 3 of the structured-facts → RAG → Graph stack).

Modules:
- bootstrap : derive entities + relations from documents.extracted_fields
              with no new LLM calls (the cheapest, highest-leverage pass)
- repo      : tenant- and vendor-scoped query helpers
- canonical : normalize raw text into dedup-able canonical forms
"""
