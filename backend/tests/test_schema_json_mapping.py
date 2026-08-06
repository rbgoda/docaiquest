"""schema_json._from_envelope — mapping the universal extraction to a schema's fields.
Regression for the lab-report gaps (patient_name/reporting_lab/patient_id all showed
'missing', date_of_birth wrongly = the test date)."""
from app.services.schema_json import _clean_fields, _from_envelope

_VALUES = {
    "subject_or_recipient": "Mr. Rajesh Goda",
    "issuer": "Dr. Jariwala Laboratory",
    "primary_date": "2024-06-24",
    "parties": [{"role": "patient", "name": "Mr. Rajesh Goda"}],
    "dates": [{"label": "collected", "value": "2024-06-24"}],   # NO birth date
    "identifiers": [{"label": "pid", "value": "373091"}],
    "records": [{"date": "2024-06-24", "description": "T. Cholesterol", "value": "143 mg/dl"}],
}


def test_person_name_maps_to_subject():
    assert _from_envelope("patient_name", _VALUES) == "Mr. Rajesh Goda"
    assert _from_envelope("full_name", _VALUES) == "Mr. Rajesh Goda"


def test_reporting_lab_maps_to_issuer():
    assert _from_envelope("reporting_lab", _VALUES) == "Dr. Jariwala Laboratory"


def test_patient_id_maps_to_identifier_not_name():
    # contains 'patient' but is an ID → must be the identifier, not the person's name
    assert _from_envelope("patient_id", _VALUES) == "373091"


def test_dob_does_not_fall_back_to_primary_date():
    # no birth-labelled date exists → None, NOT the doc's test/primary date
    assert _from_envelope("date_of_birth", _VALUES) is None


def test_dob_found_in_key_facts_labelled_dob():
    # extractors often put DOB in key_facts as 'dob' — which doesn't substring-match the
    # field name 'date_of_birth', so it must be resolved by the DOB branch, not left missing.
    vals = {"key_facts": [{"label": "sex", "value": "M"}, {"label": "dob", "value": "10-Oct-1968"}],
            "primary_date": "2021-04-26", "dates": None}
    assert _from_envelope("date_of_birth", vals) == "10-Oct-1968"


def test_dob_in_key_facts_still_beats_primary_date():
    # even with a primary_date present, DOB must resolve to the birth value, not the test date
    vals = {"key_facts": [{"label": "date_of_birth", "value": "1968-10-10"}],
            "primary_date": "2021-04-26"}
    assert _from_envelope("dob", vals) == "1968-10-10"


def test_test_results_maps_to_records():
    assert _from_envelope("test_results", _VALUES) == _VALUES["records"]


def test_generic_date_still_uses_primary():
    assert _from_envelope("test_date", _VALUES) == "2024-06-24"


def test_clean_fields_drops_leaked_metadata():
    # 8 prod schemas flattened a field-defn's own 'required'/'description'/'type' into
    # the top-level field map → phantom 'missing' rows. Drop them; keep real fields.
    dirty = {
        "id_number": {"type": "string", "required": True},
        "full_name": {"type": "string"},
        "required": False,          # leaked metadata (bool)
        "description": "The ID.",   # leaked metadata (str)
        "street_address_2": None,   # a real (if shorthand) field — keep it
    }
    clean = _clean_fields(dirty)
    assert set(clean) == {"id_number", "full_name", "street_address_2"}


def test_clean_fields_keeps_real_field_named_like_metadata():
    # a genuine field literally named 'type' carries a dict definition → not a leak
    fields = {"type": {"type": "string", "description": "document type"}, "id": {"type": "string"}}
    assert set(_clean_fields(fields)) == {"type", "id"}
