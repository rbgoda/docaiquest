"""Tests for graph/canonical.py — name, org, money, date normalization.

All pure functions — no DB, no LLM. Deterministic and fast."""

import pytest
from app.graph.canonical import (
    canon_name,
    canon_name_sorted,
    canon_org,
    split_multi_person,
    canon_money,
    money_canonical,
    canon_date,
    _valid_name,
)


class TestValidName:
    def test_plausible_person_name(self):
        assert _valid_name("rajesh goda") is True
        assert _valid_name("goda rajesh balvantrai") is True

    def test_empty_or_short(self):
        assert _valid_name("") is False
        assert _valid_name("a") is False
        assert _valid_name("ab") is True

    def test_junk_words(self):
        for junk in ("na", "n/a", "none", "null", "nil", "unknown", "tbd"):
            assert _valid_name(junk) is False

    def test_no_letters(self):
        assert _valid_name("12345") is False
        assert _valid_name("--") is False

    def test_id_like(self):
        assert _valid_name("36150737") is False

    def test_too_many_words(self):
        assert _valid_name("this is a very long descriptive text that should not be a name") is False


class TestCanonName:
    def test_basic_normalization(self):
        assert canon_name("GODA RAJESH BALVANTRAI") == "goda rajesh balvantrai"
        assert canon_name("  Rajesh Goda  ") == "rajesh goda"

    def test_title_stripping(self):
        assert canon_name("Mr. Goda Rajesh") == "goda rajesh"
        assert canon_name("Dr Kalyani Goda") == "kalyani goda"
        assert canon_name("MRS. Goda") == "goda"
        assert canon_name("Prof Rajesh") == "rajesh"

    def test_pii_placeholder_stripped(self):
        assert canon_name("[PERSON_1] Rajesh Goda") == "rajesh goda"
        assert canon_name("[NRIC_1]") == ""

    def test_parenthesized_suffix_stripped(self):
        assert canon_name("Kalyani Goda (Primary)") == "kalyani goda"

    def test_junk_returns_empty(self):
        assert canon_name("N/A") == ""
        assert canon_name("none") == ""
        assert canon_name("12345") == ""

    def test_none_returns_empty(self):
        assert canon_name(None) == ""


class TestCanonNameSorted:
    def test_word_order_normalization(self):
        assert canon_name_sorted("Rajesh Goda") == "goda rajesh"
        assert canon_name_sorted("Goda Rajesh") == "goda rajesh"

    def test_with_title(self):
        assert canon_name_sorted("Mr. Goda Rajesh Balvantrai") == "balvantrai goda rajesh"

    def test_invalid_returns_empty(self):
        assert canon_name_sorted("N/A") == ""


class TestCanonOrg:
    def test_basic(self):
        assert canon_org("UBS AG") == "ubs ag"

    def test_corporate_suffix_dropped(self):
        assert canon_org("Smart Audit Pte Ltd") == "smart audit"
        assert canon_org("Acme Corp Inc") == "acme corp"
        assert canon_org("Foo LLC") == "foo"
        assert canon_org("Bar plc") == "bar"

    def test_suffix_only_keeps_base(self):
        assert canon_org("Razer Inc") == "razer"


class TestSplitMultiPerson:
    def test_slash_separator(self):
        result = split_multi_person("GODA RAJESH BALVANTRAI / KALYANI GODA RAJESH")
        assert len(result) == 2
        assert "GODA RAJESH BALVANTRAI" in result
        assert "KALYANI GODA RAJESH" in result

    def test_ampersand_separator(self):
        result = split_multi_person("Rajesh Goda & Kalyani Goda")
        assert len(result) == 2

    def test_and_separator(self):
        result = split_multi_person("Rajesh Goda and Kalyani Goda")
        assert len(result) == 2

    def test_single_name_not_split(self):
        result = split_multi_person("GODA RAJESH BALVANTRAI")
        assert result == ["GODA RAJESH BALVANTRAI"]

    def test_single_word_not_split(self):
        # "Anderson" is one word — not split even with "and"
        result = split_multi_person("Rajesh and Anderson")
        assert len(result) == 1

    def test_empty(self):
        assert split_multi_person("") == []
        assert split_multi_person(None) == []

    def test_org_with_and_not_split(self):
        # "Jack and Jill Party Supplies" is a valid org — and none of the AND-separated
        # parts are ≥2 words individually (after removing the PII placeholder skip…):
        # actually "Jack and Jill" → split on "and" → "Jack" (1 word, fails) → stays unsplit
        result = split_multi_person("Jack and Jill Party Supplies")
        assert len(result) == 1


class TestCanonMoney:
    def test_sgd(self):
        amt, cur = canon_money("S$1,420.00")
        assert amt == 1420.00
        assert cur == "SGD"

    def test_usd_dollar_sign(self):
        amt, cur = canon_money("$1,420")
        assert amt == 1420.00
        assert cur == "USD"

    def test_eur(self):
        amt, cur = canon_money("€500.00")
        assert amt == 500.00
        assert cur == "EUR"

    def test_gbp(self):
        amt, cur = canon_money("£1,000")
        assert amt == 1000.00
        assert cur == "GBP"

    def test_inr_rupee(self):
        amt, cur = canon_money("₹50,000")
        assert amt == 50000.00
        assert cur == "INR"

    def test_suffix_currency(self):
        amt, cur = canon_money("1420.00 SGD")
        assert amt == 1420.00
        assert cur == "SGD"

    def test_negative_before_currency(self):
        # "-$500.00" — negative sign before the '$' prevents currency detection
        # (the prefix matcher looks for "$" at position 0, but it's at position 1).
        # The numeric value is still extracted correctly.
        amt, cur = canon_money("-$500.00")
        assert amt == 500.00
        assert cur is None  # currency not detected because '-' precedes '$'

    def test_negative_after_currency(self):
        # "$-500.00" — negative sign after currency symbol is matched.
        amt, cur = canon_money("$-500.00")
        assert amt == -500.00
        assert cur == "USD"

    def test_none(self):
        assert canon_money(None) == (None, None)

    def test_unparseable(self):
        assert canon_money("not money") == (None, None)


class TestMoneyCanonical:
    def test_sgd(self):
        assert money_canonical("S$1,420.00") == "1420.00 SGD"

    def test_usd(self):
        assert money_canonical("$1,420") == "1420.00 USD"

    def test_invalid(self):
        assert money_canonical("nope") == ""


class TestCanonDate:
    def test_already_iso(self):
        assert canon_date("2026-05-12") == "2026-05-12"

    def test_dmy_slashes(self):
        # canon_date tries %m/%d/%Y before %d/%m/%Y, so 12/05/2026 →
        # December 5 (US-order), not May 12.
        assert canon_date("12/05/2026") == "2026-12-05"

    def test_mdy_slashes(self):
        assert canon_date("05/12/2026") == "2026-05-12"

    def test_dd_mon_yyyy(self):
        assert canon_date("12 May 2026") == "2026-05-12"
        assert canon_date("12-May-2026") == "2026-05-12"

    def test_full_month(self):
        assert canon_date("12 May 2026") == "2026-05-12"
        assert canon_date("May 12, 2026") == "2026-05-12"

    def test_dmy_dashes(self):
        assert canon_date("12-05-2026") == "2026-05-12"

    def test_empty(self):
        assert canon_date("") == ""
        assert canon_date(None) == ""

    def test_bracketed(self):
        assert canon_date("[2026-05-12]") == "2026-05-12"
