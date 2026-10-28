"""Lab catalog rules without a database: units, conversion, names, printed values and ranges."""

from decimal import Decimal

import pytest

from vitalocal import labs
from vitalocal.labs import CATALOG, convert, match_analyte, norm_name, norm_unit, parse_reference, parse_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mmol/L", "mmol/l"),
        ("umol/l", "µmol/l"),
        ("μg/dl", "µg/dl"),
        ("G/l", "10^9/l"),
        ("µIU/ml", "mu/l"),
        ("ng/mL", "µg/l"),
        (" U/L ", "u/l"),
    ],
)
def test_units_are_normalized(raw, expected):
    assert norm_unit(raw) == expected


@pytest.mark.parametrize(
    ("value", "src", "dst", "key", "expected"),
    [
        (30, "ng/ml", "nmol/l", "vitamin_d", 74.88),  # 1 ng/ml = 2.496 nmol/l
        (5.0, "mmol/l", "mg/dl", "cholesterol", 193.3),
        (1.0, "mmol/l", "mg/dl", "triglycerides", 88.57),
        (100, "mg/dl", "mmol/l", "glucose", 5.55),
        (1.0, "mg/dl", "µmol/l", "creatinine", 88.4),
        (2.0, "mg/dl", "mmol/l", "magnesium", 0.823),
        (7.0, "%", "mmol/mol", "hba1c", 53.0),
        (53.0, "mmol/mol", "%", "hba1c", 7.0),
        (14.5, "g/dl", "mmol/l", "hemoglobin", 9.0),
        (6.2, "G/l", "/nl", "leukocytes", 6.2),
        (6.2, "Tsd/µl", "/nl", "leukocytes", 6.2),
        (0.42, "l/l", "%", "hematocrit", 42.0),
        (2.1, "µU/ml", "mU/l", "tsh", 2.1),
        (0.8, "mg/dl", "mg/l", "crp", 8.0),
    ],
)
def test_conversion(value, src, dst, key, expected):
    assert labs.round_sig(convert(value, src, dst, CATALOG[key])) == pytest.approx(expected, rel=2e-3)


def test_conversion_needs_a_molar_mass_or_the_same_dimension():
    assert convert(10, "nmol/l", "mg/dl", CATALOG["lpa"]) is None  # Lp(a): no reliable factor
    assert convert(10, "%", "/nl", CATALOG["lymphocytes_pct"]) is None
    assert convert(10, "mmol/l", "mg/dl", None) is None
    assert convert(None, "mmol/l", "mmol/l") is None


def test_every_catalog_unit_converts_to_the_default():
    for a in CATALOG.values():
        for u in labs.units_for(a):
            assert convert(1.0, u, a.unit, a) is not None, (a.key, u)


def test_names_ignore_case_umlauts_punctuation_and_word_order():
    assert norm_name("Cholesterin, HDL-") == norm_name("HDL-Cholesterin")
    assert norm_name("Harnsäure") == norm_name("harnsaeure")


@pytest.mark.parametrize(
    ("name", "material", "unit", "key", "warn"),
    [
        ("Cholesterin, gesamt", "Serum", "mmol/L", "cholesterol", False),
        ("Cholesterin, Non-HDL-", None, "mmol/L", "non_hdl", False),
        ("25-Hydroxy-Vitamin D", "Serum", "nmol/L", "vitamin_d", False),
        ("CRP hochsensitiv", "Serum", "mg/L", "hscrp", False),
        ("Magnesium", "EDTA-Blut", "mmol/L", "magnesium_wb", False),  # whole blood ≠ serum
        ("Magnesium", "Serum", "mmol/L", "magnesium", False),
        ("Magnesium", None, "mmol/L", "magnesium", True),  # unclear: serum assumed, with a warning
        ("Lymphozyten", "EDTA-Blut", "%", "lymphocytes_pct", False),
        ("Lymphozyten", "EDTA-Blut", "/nl", "lymphocytes_abs", False),
        ("GPT", None, "U/l", "alt", False),
        ("Unbekannter Test", None, "U/l", None, False),
    ],
)
def test_matching(name, material, unit, key, warn):
    got, warning = match_analyte(name, material, unit, CATALOG, labs.synonym_index(CATALOG))
    assert got == key
    assert bool(warning) == warn


def test_learned_synonyms_win():
    idx = labs.synonym_index(CATALOG)
    learned = {(norm_name("Vit. D (25-OH)"), ""): "vitamin_d"}
    assert match_analyte("Vit. D (25-OH)", "Serum", "ng/ml", CATALOG, idx, learned)[0] == "vitamin_d"
    assert match_analyte("Vit. D (25-OH)", "Serum", "ng/ml", CATALOG, idx)[0] is None


@pytest.mark.parametrize(
    ("raw", "comparator", "value", "text"),
    [
        ("0,46", None, Decimal("0.46"), None),
        ("2,90", None, Decimal("2.90"), None),  # scale kept
        ("< 0,5", "<", Decimal("0.5"), None),
        (">=100", ">=", Decimal("100"), None),
        ("1,58 mmol/L", None, Decimal("1.58"), None),
        ("1.234,5", None, Decimal("1234.5"), None),
        (12, None, Decimal("12"), None),
        ("negativ", None, None, "negativ"),
        ("", None, None, None),
    ],
)
def test_printed_values(raw, comparator, value, text):
    p = parse_value(raw)
    assert (p.comparator, p.value, p.text) == (comparator, value, text)
    if value is not None:
        assert str(p.value) == str(value)


@pytest.mark.parametrize(
    ("raw", "low", "high"),
    [
        ("< 2,3", None, Decimal("2.3")),
        ("<= 5", None, Decimal("5")),
        ("> 0,9", Decimal("0.9"), None),
        ("1.3 - 1.8", Decimal("1.3"), Decimal("1.8")),
        ("0,70–1,05", Decimal("0.70"), Decimal("1.05")),
        ("3,5 bis 5,1 mmol/l", Decimal("3.5"), Decimal("5.1")),
        ("bis 40", None, Decimal("40")),
        ("negativ", None, None),
        (None, None, None),
    ],
)
def test_reference_ranges(raw, low, high):
    assert parse_reference(raw) == (low, high)


def test_status_against_range_and_flags():
    rs = labs.range_status
    assert rs(Decimal("0.46"), None, None, Decimal("2.3")) == "normal"
    assert rs(Decimal("1.2"), None, Decimal("1.3"), Decimal("1.8")) == "low"
    assert rs(Decimal("2.3"), None, None, Decimal("2.3")) == "normal"  # bound counts as normal
    assert rs(Decimal("6"), None, None, Decimal("5.2")) == "high"
    assert rs(Decimal("0.5"), "<", None, Decimal("5")) == "normal"
    assert rs(Decimal("3"), "<", None, Decimal("1")) is None  # "<3" against "< 1": unknown
    assert rs(Decimal("1"), "<", Decimal("1.3"), None) == "low"
    assert rs(Decimal("300"), ">", None, Decimal("250")) == "high"
    assert rs(Decimal("1"), None, None, None) is None
    assert (
        labs.flag_status("H") == "high" and labs.flag_status("↓") == "low" and labs.flag_status("x") is None
    )
