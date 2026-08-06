"""extraction_coverage.analyze — the deterministic per-doc coverage audit. Pure over
(source_text, envelope), so no DB needed. Cases mirror the real lipid-panel lab report
(doc-up-5981fa3613-4f35de): patient results captured, reference thresholds excluded,
dates matched across format normalization."""
from app.services.extraction_coverage import _norm_date, analyze

# a lab report page: 4 patient results, each followed by a DESIRABLE/OPTIMAL threshold,
# plus a collection date printed as 26-Apr-2021.
_SRC = """SingHealth Polyclinics  DOB: 10-Oct-1968
26-Apr-2021 07:52  Lipid Panel
Cholesterol Total, serum 3.20 [MMOL/L]   DESIRABLE LEVEL < 5.20 MMOL/L
Cholesterol HDL, serum 1.25 [MMOL/L]     DESIRABLE LEVEL = > 1.00 MMOL/L
Triglycerides, serum 1.18 [MMOL/L]       OPTIMAL LEVEL < 1.70 MMOL/L
Cholesterol LDL, Calc 1.41 [MMOL/L]      OPTIMAL LEVEL < 2.60 MMOL/L
"""
# envelope stores the 4 results + the date NORMALIZED to ISO (as extractors do).
_FIELDS = {
    "records": [{"attributes": [
        {"label": "cholesterol_total_serum", "value": "3.20 [MMOL/L]"},
        {"label": "cholesterol_hdl_serum", "value": "1.25 [MMOL/L]"},
        {"label": "triglycerides_serum", "value": "1.18 [MMOL/L]"},
        {"label": "cholesterol_ldl_calc", "value": "1.41 [MMOL/L]"}]}],
    "key_facts": [{"label": "dob", "value": "10-Oct-1968"}],
    "primary_date": "2021-04-26",
}


def test_patient_results_all_captured():
    r = analyze(_SRC, _FIELDS)
    assert r["structured"]["captured"] == r["structured"]["considered"]  # nothing important missed
    assert r["grade"] == "green"
    assert r["unstructured"] == []


def test_reference_thresholds_are_excluded_not_counted_as_missing():
    r = analyze(_SRC, _FIELDS)
    # the four DESIRABLE/OPTIMAL thresholds (5.20/1.00/1.70/2.60) must not be 'considered'
    assert r["referenceExcluded"] >= 4
    vals = {u["value"] for u in r["unstructured"]}
    assert not ({"5.20", "1.00", "1.70", "2.60"} & vals)


def test_date_matched_across_format_normalization():
    # source prints 26-Apr-2021; envelope stores 2021-04-26 → must count as captured
    r = analyze("Collected 26-Apr-2021", {"primary_date": "2021-04-26"})
    assert r["structured"]["captured"] == 1 and r["structured"]["considered"] == 1


def test_missing_value_is_listed():
    # 9.99 is on the page but not in the envelope → flagged as unstructured
    r = analyze("Result A 3.20  Result B 9.99", {"records": [{"value": "3.20"}]})
    miss = {u["value"] for u in r["unstructured"]}
    assert "9.99" in miss and "3.20" not in miss


def test_empty_doc_grades_na():
    r = analyze("", {})
    assert r["grade"] == "na" and r["structured"]["pct"] is None


def test_norm_date_formats():
    assert _norm_date("dmy_name", ("26", "Apr", "2021")) == "2021-04-26"
    assert _norm_date("iso", ("2021", "4", "26")) == "2021-04-26"
    assert _norm_date("dmy_num", ("26", "04", "21")) == "2021-04-26"
    assert _norm_date("mdy_name", ("April", "26", "2021")) == "2021-04-26"
    assert _norm_date("iso", ("2021", "13", "40")) is None  # out of range
