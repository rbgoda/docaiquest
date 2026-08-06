"""Regression guard for the _BODY_MD_CACHE orphan bug.

`_structure_body_md` was extracted from doc_chat.py into doc_chat_export.py but its
module-global cache was left behind, so every reference in the moved function was an
undefined name → a latent NameError on the Markdown export for artifact-less docs
(ruff F821 caught it during the lint-debt cleanup). This test fails if the cache the
function depends on ever goes missing from its own module again."""
import app.routers.doc_chat_export as dce


def test_body_md_cache_defined_in_export_module():
    assert isinstance(dce._BODY_MD_CACHE, dict), "_structure_body_md needs its cache in-module"


def test_structure_body_md_names_resolve():
    # The function references _BODY_MD_CACHE at module scope; if it were undefined
    # this attribute lookup on the function's globals would fail.
    assert "_BODY_MD_CACHE" in dce._structure_body_md.__globals__
