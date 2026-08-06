"""schema_architect._sanitize_fields — the source-side guard that stops the LLM's `fields`
map from persisting leaked definition-metadata keys ('required'/'description'/'type') as
phantom fields (the root cause behind the render-time #286 fix)."""
from app.agents.schema_architect import _sanitize_fields


def test_drops_leaked_metadata_keys():
    dirty = {
        "id_number": {"type": "string", "required": True},
        "full_name": {"type": "string"},
        "required": False,          # leaked metadata (bool)
        "description": "The ID.",   # leaked metadata (str)
        "type": "object",           # leaked metadata (str)
    }
    clean = _sanitize_fields(dirty)
    assert set(clean) == {"id_number", "full_name"}


def test_keeps_real_field_named_like_metadata():
    # a genuine field literally named 'type' carries a dict definition → kept
    fields = {"type": {"type": "string", "description": "doc type"}, "id": {"type": "string"}}
    assert set(_sanitize_fields(fields)) == {"type", "id"}


def test_coerces_bare_shorthand_to_valid_definition():
    fields = {"street_address_2": None, "city": "string"}
    clean = _sanitize_fields(fields)
    assert clean["street_address_2"] == {"type": "string", "required": False}
    assert clean["city"] == {"type": "string", "required": False}


def test_normalizes_type_and_required():
    fields = {"amount": {"type": "FLOAT", "required": "yes"}, "note": {"type": "number"}}
    clean = _sanitize_fields(fields)
    assert clean["amount"]["type"] == "string"   # unknown type → string
    assert clean["amount"]["required"] is True    # truthy → bool True
    assert clean["note"]["type"] == "number"      # known type preserved
    assert clean["note"]["required"] is False     # defaulted


def test_skips_blank_keys():
    assert _sanitize_fields({"": {"type": "string"}, "  ": {}, "ok": {"type": "string"}}) == {
        "ok": {"type": "string", "required": False}
    }
