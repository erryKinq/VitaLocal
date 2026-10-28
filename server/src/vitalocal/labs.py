# ruff: noqa: E501  (the analyte catalog is a table, one analyte per line)
"""Lab results: analyte catalog, unit conversion and parsing of printed values and ranges.

Pure functions only (no database), so the rules are easy to test:

- Units are parsed into a dimension and a scale (`mmol/l` → molar, 1e-3 mol/l). Values convert
  within a dimension; between mass and molar concentration via the analyte's molar mass
  (25-OH-vitamin D: 1 ng/ml = 2.496 nmol/l). HbA1c % ↔ mmol/mol is the one affine conversion.
- An analyte has a default (SI) unit and optionally a conventional one; display is switchable.
- Names are matched through normalized synonyms (case, umlauts, word order and punctuation do
  not matter). Where the specimen changes the meaning (magnesium in serum vs. whole blood), the
  material picks the analyte; where the unit does (lymphocytes in % vs. /nl), the unit does.
- A printed value ("<0,5", "2,90", "negativ") becomes comparator + Decimal (scale kept) or text;
  a printed range ("< 2,3", "1.3 - 1.8", "> 75") becomes lower/upper bounds.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# --- units -------------------------------------------------------------------------------------------------

_PREFIX = {"": 1.0, "k": 1e3, "d": 1e-1, "c": 1e-2, "m": 1e-3, "µ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
_VOLUME = {"l": 1.0, "dl": 1e-1, "ml": 1e-3, "µl": 1e-6, "nl": 1e-9, "pl": 1e-12}
# Cell counts as printed by German labs, per litre.
_COUNTS = {
    "/nl": 1e9, "10^9/l": 1e9, "10*9/l": 1e9, "tsd/µl": 1e9, "1000/µl": 1e9, "10^3/µl": 1e9,
    "t/l": 1e12, "/pl": 1e12, "10^12/l": 1e12, "10*12/l": 1e12, "mio/µl": 1e12, "10^6/µl": 1e12,
    "/µl": 1e6, "/l": 1.0,
}  # fmt: skip
_ALIASES = {
    "iu/l": "u/l", "ie/l": "u/l", "e/l": "u/l", "mie/l": "mu/l", "miu/l": "mu/l", "µiu/ml": "mu/l", "µie/ml": "mu/l",
    "µu/ml": "mu/l", "miu/ml": "u/l", "mu/ml": "u/l", "ng/ml": "µg/l", "pg/ml": "ng/l", "µg/ml": "mg/l",
    "mg/ml": "g/l", "fl": "fl", "pg": "pg", "l/l": "l/l", "mmol/mol": "mmol/mol", "prozent": "%",
}  # fmt: skip


def norm_unit(unit: str | None) -> str:
    """Canonical spelling for comparison: 'mmol/L' = 'mmol/l', 'ug/dl' = 'µg/dl', 'G/l' = '10^9/l'."""
    if not unit:
        return ""
    u = unicodedata.normalize("NFKC", unit).strip().replace("μ", "µ").replace(" ", "")
    u = u.replace("×", "*").replace("x10", "10").replace("hoch", "^")
    if u in ("G/l", "G/L"):
        return "10^9/l"
    if u in ("T/l", "T/L"):
        return "10^12/l"
    u = u.lower()
    u = re.sub(r"^u(?=(g|mol|l|iu|u)\b|g/|mol/|l$|iu/|u/)", "µ", u)  # ug/l, umol/l, uIU/ml
    u = u.replace("mcg", "µg").replace("/1000/µl", "tsd/µl")
    return _ALIASES.get(u, u)


@dataclass(frozen=True)
class Dim:
    kind: str  # mass | molar | activity | count | fraction | other
    scale: float  # in g/l, mol/l, U/l, 1/l or fraction (1 = 100 %)


def parse_unit(unit: str | None) -> Dim | None:
    u = norm_unit(unit)
    if not u:
        return None
    if u in _COUNTS:
        return Dim("count", _COUNTS[u])
    if u == "%":
        return Dim("fraction", 0.01)
    if u == "l/l":
        return Dim("fraction", 1.0)
    if "/" not in u:
        return None
    num, _, den = u.partition("/")
    if den not in _VOLUME:
        return None
    for base, kind in (("mol", "molar"), ("g", "mass"), ("u", "activity")):
        if num.endswith(base) and num[: -len(base)] in _PREFIX:
            return Dim(kind, _PREFIX[num[: -len(base)]] / _VOLUME[den])
    return None


# --- catalog -----------------------------------------------------------------------------------------------

GROUPS = (
    ("lipids", "Fettstoffwechsel"),
    ("glucose", "Zuckerstoffwechsel"),
    ("vitamins", "Vitamine"),
    ("minerals", "Mineralstoffe & Spurenelemente"),
    ("iron", "Eisenstoffwechsel"),
    ("blood_count", "Blutbild"),
    ("inflammation", "Entzündung"),
    ("liver", "Leber & Galle"),
    ("kidney", "Niere & Harnsäure"),
    ("electrolytes", "Elektrolyte"),
    ("thyroid", "Schilddrüse"),
    ("hormones", "Hormone"),
    ("other", "Sonstige"),
)
GROUP_LABELS = dict(GROUPS)
MATERIALS = ("serum", "plasma", "whole_blood", "urine")


@dataclass(frozen=True)
class Analyte:
    key: str
    label: str
    group: str
    unit: str  # default (SI) display unit
    conventional: str | None = None  # the other common unit, if any
    molar_mass: float | None = None  # g/mol, for mass ↔ molar concentration
    synonyms: tuple[str, ...] = ()
    material: str | None = None  # only set where the specimen changes the meaning
    ingredients: tuple[str, ...] = ()  # related supplement ingredient keys (api/supplements.INGREDIENTS)
    note: str | None = None
    custom: bool = False
    extra_units: tuple[str, ...] = field(default=())  # further accepted spellings for the unit picker

    @property
    def dim(self) -> Dim | None:
        return parse_unit(self.unit)


_CHOL = 386.65
_A = Analyte
ANALYTES: tuple[Analyte, ...] = (
    # lipids
    _A("cholesterol", "Cholesterin, gesamt", "lipids", "mmol/l", "mg/dl", _CHOL,
       ("Cholesterin", "Gesamtcholesterin", "Cholesterin gesamt", "Total cholesterol", "Chol")),
    _A("hdl", "HDL-Cholesterin", "lipids", "mmol/l", "mg/dl", _CHOL, ("HDL", "HDL-C", "Cholesterin HDL")),
    _A("ldl", "LDL-Cholesterin", "lipids", "mmol/l", "mg/dl", _CHOL,
       ("LDL", "LDL-C", "Cholesterin LDL", "LDL-Cholesterin direkt", "LDL berechnet")),
    _A("non_hdl", "Non-HDL-Cholesterin", "lipids", "mmol/l", "mg/dl", _CHOL,
       ("Non-HDL", "Non-HDL-C", "Cholesterin Non-HDL", "Nicht-HDL-Cholesterin")),
    _A("triglycerides", "Triglyceride", "lipids", "mmol/l", "mg/dl", 885.7, ("Triglyzeride", "TG", "Triglycerid")),
    _A("lpa", "Lipoprotein (a)", "lipids", "nmol/l", None, None, ("Lp(a)", "Lipoprotein a", "Lpa"),
       note="nmol/l und mg/dl sind nicht sicher ineinander umrechenbar"),
    _A("apob", "Apolipoprotein B", "lipids", "g/l", "mg/dl", None, ("ApoB", "Apo B", "Apolipoprotein B-100")),
    # glucose
    _A("glucose", "Glukose", "glucose", "mmol/l", "mg/dl", 180.16,
       ("Glucose", "Blutzucker", "Nüchternglukose", "Glukose nüchtern", "Glucose nüchtern", "Nüchternblutzucker")),
    _A("hba1c", "HbA1c", "glucose", "mmol/mol", "%", None, ("HbA1c IFCC", "HbA1c DCCT", "Glykohämoglobin", "HbA1")),
    _A("insulin", "Insulin", "glucose", "mU/l", "µU/ml", None, ("Insulin nüchtern",)),
    # vitamins
    _A("vitamin_d", "25-OH-Vitamin D", "vitamins", "nmol/l", "ng/ml", 400.64,
       ("25-Hydroxy-Vitamin D", "25-OH-Vitamin D3", "25-Hydroxyvitamin D", "Vitamin D", "Vitamin D3", "Calcidiol",
        "25-OH-D", "Vitamin D 25-OH", "25(OH)D"), ingredients=("vitamin_d",)),
    _A("vitamin_b12", "Vitamin B12", "vitamins", "pmol/l", "pg/ml", 1355.37, ("Cobalamin", "B12", "Vitamin B 12"),
       ingredients=("vitamin_b12",)),
    _A("holo_tc", "Holo-Transcobalamin", "vitamins", "pmol/l", None, None,
       ("Holotranscobalamin", "Holo-TC", "aktives Vitamin B12", "Holo TC"), ingredients=("vitamin_b12",)),
    _A("folate", "Folsäure", "vitamins", "nmol/l", "ng/ml", 441.4, ("Folat", "Folsäure im Serum", "Folic acid"),
       ingredients=("folic_acid",)),
    _A("homocysteine", "Homocystein", "vitamins", "µmol/l", None, 135.18, ("Homocystein", "Hcy"),
       ingredients=("vitamin_b12", "folic_acid", "vitamin_b6")),
    _A("omega3_index", "Omega-3-Index", "vitamins", "%", None, None, ("Omega-3 Index", "HS-Omega-3-Index"),
       ingredients=("omega3",)),
    # minerals & trace elements
    _A("magnesium", "Magnesium (Serum)", "minerals", "mmol/l", "mg/dl", 24.305, ("Magnesium", "Mg"),
       material="serum", ingredients=("magnesium",)),
    _A("magnesium_wb", "Magnesium (Vollblut)", "minerals", "mmol/l", "mg/dl", 24.305,
       ("Magnesium", "Mg", "Magnesium Vollblut", "Magnesium im Vollblut"), material="whole_blood",
       ingredients=("magnesium",), note="Vollblut-Referenz liegt höher als die im Serum"),
    _A("zinc", "Zink", "minerals", "µmol/l", "µg/dl", 65.38, ("Zink im Serum", "Zn", "Zinc"), material="serum",
       ingredients=("zinc",)),
    _A("zinc_wb", "Zink (Vollblut)", "minerals", "µmol/l", "µg/dl", 65.38, ("Zink", "Zn", "Zink Vollblut"),
       material="whole_blood", ingredients=("zinc",)),
    _A("selenium", "Selen", "minerals", "µg/l", "µmol/l", 78.97, ("Selen im Serum", "Se", "Selenium"),
       material="serum", ingredients=("selenium",)),
    _A("selenium_wb", "Selen (Vollblut)", "minerals", "µg/l", "µmol/l", 78.97, ("Selen", "Se", "Selen Vollblut"),
       material="whole_blood", ingredients=("selenium",)),
    _A("copper", "Kupfer", "minerals", "µmol/l", "µg/dl", 63.55, ("Cu", "Copper"), ingredients=("copper",)),
    # iron
    _A("ferritin", "Ferritin", "iron", "µg/l", None, None, ("Ferritin im Serum",), ingredients=("iron",),
       extra_units=("ng/ml",)),
    _A("iron", "Eisen", "iron", "µmol/l", "µg/dl", 55.845, ("Eisen im Serum", "Fe", "Iron", "Serumeisen"),
       ingredients=("iron",)),
    _A("transferrin", "Transferrin", "iron", "g/l", "mg/dl", None, ()),
    _A("transferrin_sat", "Transferrinsättigung", "iron", "%", None, None,
       ("Transferrin-Sättigung", "TSAT", "Transferrinsaettigung")),
    _A("stfr", "Löslicher Transferrinrezeptor", "iron", "mg/l", None, None, ("sTfR", "löslicher Transferrin-Rezeptor")),
    # blood count
    _A("hemoglobin", "Hämoglobin", "blood_count", "g/dl", "mmol/l", 16114.5, ("Hb", "Haemoglobin", "Hämoglobin (Hb)")),
    _A("hematocrit", "Hämatokrit", "blood_count", "%", "l/l", None, ("Hkt", "Hk", "HCT", "Haematokrit")),
    _A("erythrocytes", "Erythrozyten", "blood_count", "/pl", "Mio/µl", None, ("Ery", "RBC", "Erythrocyten"),
       extra_units=("T/l",)),
    _A("leukocytes", "Leukozyten", "blood_count", "/nl", "Tsd/µl", None, ("Leuko", "WBC", "Leukocyten"),
       extra_units=("G/l",)),
    _A("platelets", "Thrombozyten", "blood_count", "/nl", "Tsd/µl", None, ("Thrombo", "PLT", "Thrombocyten"),
       extra_units=("G/l",)),
    _A("mcv", "MCV", "blood_count", "fl", None, None, ("mittleres Erythrozyteneinzelvolumen",)),
    _A("mch", "MCH", "blood_count", "pg", None, None, ("HbE", "mittlerer Hämoglobingehalt",)),
    _A("mchc", "MCHC", "blood_count", "g/dl", "g/l", None, ("mittlere Hämoglobinkonzentration",)),
    _A("rdw", "RDW", "blood_count", "%", None, None, ("Ery-Verteilungsbreite", "RDW-CV", "EVB")),
    *(
        _A(f"{k}_{kind}", f"{label} ({'%' if kind == 'pct' else 'absolut'})", "blood_count",
           "%" if kind == "pct" else "/nl", None if kind == "pct" else "Tsd/µl", None, (label, *syn),
           extra_units=() if kind == "pct" else ("G/l",))
        for k, label, syn in (
            ("neutrophils", "Neutrophile", ("Neutrophile Granulozyten", "Neutro", "Segmentkernige")),
            ("lymphocytes", "Lymphozyten", ("Lympho", "Lymphocyten")),
            ("monocytes", "Monozyten", ("Mono", "Monocyten")),
            ("eosinophils", "Eosinophile", ("Eosinophile Granulozyten", "Eos")),
            ("basophils", "Basophile", ("Basophile Granulozyten", "Baso")),
        )
        for kind in ("pct", "abs")
    ),
    # inflammation
    _A("crp", "CRP", "inflammation", "mg/l", "mg/dl", None, ("C-reaktives Protein", "C-reaktives Protein (CRP)")),
    _A("hscrp", "CRP hochsensitiv", "inflammation", "mg/l", "mg/dl", None,
       ("hsCRP", "hs-CRP", "CRP hs", "hochsensitives CRP", "CRP (hochsensitiv)")),
    # liver
    _A("alt", "ALT (GPT)", "liver", "U/l", None, None, ("GPT", "ALAT", "ALT", "Alanin-Aminotransferase", "GPT (ALT)")),
    _A("ast", "AST (GOT)", "liver", "U/l", None, None, ("GOT", "ASAT", "AST", "Aspartat-Aminotransferase", "GOT (AST)")),
    _A("ggt", "Gamma-GT", "liver", "U/l", None, None, ("GGT", "γ-GT", "Gamma-GT (GGT)", "Gamma-Glutamyltransferase")),
    _A("alp", "Alkalische Phosphatase", "liver", "U/l", None, None, ("AP", "ALP", "alk. Phosphatase")),
    _A("bilirubin", "Bilirubin, gesamt", "liver", "µmol/l", "mg/dl", 584.66, ("Bilirubin", "Gesamtbilirubin")),
    _A("albumin", "Albumin", "liver", "g/l", "g/dl", None, ()),
    _A("total_protein", "Gesamteiweiß", "liver", "g/l", "g/dl", None, ("Eiweiß gesamt", "Gesamtprotein", "Total protein")),
    _A("ldh", "LDH", "liver", "U/l", None, None, ("Laktatdehydrogenase", "Lactatdehydrogenase")),
    _A("ck", "Creatinkinase (CK)", "other", "U/l", None, None, ("CK", "Kreatinkinase", "CK gesamt")),
    # kidney
    _A("creatinine", "Kreatinin", "kidney", "µmol/l", "mg/dl", 113.12, ("Creatinin", "Krea", "Kreatinin im Serum")),
    _A("egfr", "eGFR", "kidney", "ml/min/1.73m²", None, None,
       ("GFR", "eGFR CKD-EPI", "GFR (CKD-EPI)", "glomeruläre Filtrationsrate", "eGFR (CKD-EPI)")),
    _A("cystatin_c", "Cystatin C", "kidney", "mg/l", None, None, ("Cystatin",)),
    _A("urea", "Harnstoff", "kidney", "mmol/l", "mg/dl", 60.06, ("Urea",)),
    _A("uric_acid", "Harnsäure", "kidney", "µmol/l", "mg/dl", 168.11, ("Urat", "Uric acid")),
    # electrolytes
    _A("sodium", "Natrium", "electrolytes", "mmol/l", None, 22.99, ("Na",)),
    _A("potassium", "Kalium", "electrolytes", "mmol/l", None, 39.10, ("K",)),
    _A("chloride", "Chlorid", "electrolytes", "mmol/l", None, 35.45, ("Cl",)),
    _A("calcium", "Calcium", "electrolytes", "mmol/l", "mg/dl", 40.08, ("Kalzium", "Ca", "Calcium gesamt"),
       ingredients=("calcium",)),
    _A("phosphate", "Phosphat", "electrolytes", "mmol/l", "mg/dl", 30.97, ("Phosphor", "anorganisches Phosphat")),
    # thyroid
    _A("tsh", "TSH", "thyroid", "mU/l", "µU/ml", None, ("TSH basal", "Thyreotropin", "TSH basal (3. Gen.)")),
    _A("ft4", "fT4", "thyroid", "pmol/l", "ng/dl", 776.87, ("freies T4", "FT4", "freies Thyroxin")),
    _A("ft3", "fT3", "thyroid", "pmol/l", "pg/ml", 650.97, ("freies T3", "FT3", "freies Trijodthyronin")),
    # hormones
    _A("testosterone", "Testosteron", "hormones", "nmol/l", "ng/ml", 288.42, ("Testosteron gesamt", "Testosterone")),
    _A("shbg", "SHBG", "hormones", "nmol/l", None, None, ("Sexualhormon-bindendes Globulin",)),
    _A("cortisol", "Cortisol", "hormones", "nmol/l", "µg/dl", 362.46, ("Kortisol", "Cortisol im Serum")),
    _A("psa", "PSA", "hormones", "µg/l", None, None, ("PSA gesamt", "Prostataspezifisches Antigen"),
       extra_units=("ng/ml",)),
)  # fmt: skip
CATALOG: dict[str, Analyte] = {a.key: a for a in ANALYTES}
assert len(CATALOG) == len(ANALYTES), "duplicate analyte key"


def units_for(a: Analyte) -> list[str]:
    """Units the analyte can be shown in (default first)."""
    out = [a.unit] + [u for u in (a.conventional, *a.extra_units) if u]
    return list(dict.fromkeys(out))


# --- conversion --------------------------------------------------------------------------------------------


def _hba1c(value: float, src: str, dst: str) -> float | None:
    if src == "%" and dst == "mmol/mol":
        return (value - 2.15) * 10.929
    if src == "mmol/mol" and dst == "%":
        return value / 10.929 + 2.15
    return None


def convert(
    value: float | Decimal | None, src: str | None, dst: str | None, a: Analyte | None = None
) -> float | None:
    """`value` in unit `src` expressed in `dst`, or None when that is not possible."""
    if value is None:
        return None
    v = float(value)
    s, d = norm_unit(src), norm_unit(dst)
    if s == d:
        return v
    if a is not None and a.key == "hba1c":
        return _hba1c(v, s, d)
    ps, pd = parse_unit(s), parse_unit(d)
    if ps is None or pd is None:
        return None
    if ps.kind == pd.kind:
        return v * ps.scale / pd.scale
    mm = a.molar_mass if a else None
    if mm and ps.kind == "mass" and pd.kind == "molar":
        return v * ps.scale / mm / pd.scale
    if mm and ps.kind == "molar" and pd.kind == "mass":
        return v * ps.scale * mm / pd.scale
    return None


def round_sig(v: float | None, sig: int = 3) -> float | None:
    """Rounded for display after a conversion: three significant digits, at most 2 decimals over 10."""
    if v is None:
        return None
    if v == 0:
        return 0.0
    digits = max(0, sig - 1 - int(f"{abs(v):e}".split("e")[1]))
    return round(v, min(digits, 4))


# --- names -------------------------------------------------------------------------------------------------

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "γ": "gamma"})


def norm_name(name: str) -> str:
    """Matching key: lowercase, umlauts spelled out, punctuation dropped, words sorted.

    'Cholesterin, HDL-' and 'HDL-Cholesterin' give the same key.
    """
    n = unicodedata.normalize("NFKC", name).lower().translate(_UMLAUT)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(sorted(n.split()))


def norm_material(material: str | None) -> str | None:
    """'EDTA-Blut', 'Vollblut' → whole_blood; 'Serum' → serum; 'Li-Heparin-Plasma' → plasma; 'Urin' → urine."""
    if not material:
        return None
    m = material.lower().translate(_UMLAUT)
    if "urin" in m or "harn" in m:
        return "urine"
    if "plasma" in m:
        return "plasma"
    if "serum" in m:
        return "serum"
    if "blut" in m or "edta" in m or "blood" in m or "heparin" in m or "citrat" in m:
        return "whole_blood"
    return None


def synonym_index(analytes: dict[str, Analyte]) -> dict[str, list[str]]:
    """Normalized name -> analyte keys (label, key and synonyms)."""
    index: dict[str, list[str]] = {}
    for a in analytes.values():
        for n in (a.label, a.key.replace("_", " "), *a.synonyms):
            keys = index.setdefault(norm_name(n), [])
            if a.key not in keys:
                keys.append(a.key)
    return index


def match_analyte(
    name: str,
    material: str | None,
    unit: str | None,
    analytes: dict[str, Analyte],
    index: dict[str, list[str]],
    learned: dict[tuple[str, str], str] | None = None,
) -> tuple[str | None, str | None]:
    """(analyte key, warning). Learned synonyms (name, material) win over the built-in ones."""
    key = norm_name(name)
    mat = norm_material(material)
    if learned:
        hit = learned.get((key, mat or "")) or learned.get((key, ""))
        if hit and hit in analytes:
            return hit, None
    cands = [analytes[k] for k in index.get(key, []) if k in analytes]
    if not cands:
        return None, None
    if len(cands) == 1:
        return cands[0].key, None
    # The unit decides between e.g. % and absolute counts.
    if unit:
        u = norm_unit(unit)
        by_unit = [
            a
            for a in cands
            if u in {norm_unit(x) for x in units_for(a)} or convert(1.0, unit, a.unit, a) is not None
        ]
        if len(by_unit) == 1:
            return by_unit[0].key, None
        cands = by_unit or cands
    by_mat = [a for a in cands if a.material == mat] if mat else []
    if len(by_mat) == 1:
        return by_mat[0].key, None
    # Several specimens possible and none given: prefer serum and say so.
    serum = [a for a in cands if a.material in (None, "serum")]
    pick = (serum or cands)[0]
    return pick.key, f"material unclear, assumed {pick.label}"


# --- printed values ----------------------------------------------------------------------------------------

_NUM = r"(?:[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]+|[0-9]+(?:[.,][0-9]+)?)"
_CMP = {"<": "<", "<=": "<=", "≤": "<=", ">": ">", ">=": ">=", "≥": ">="}


def to_decimal(s: str) -> Decimal | None:
    s = s.strip().replace(" ", "")
    if re.fullmatch(r"[0-9]{1,3}(\.[0-9]{3})+,[0-9]+", s):  # 1.234,5
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


@dataclass
class Parsed:
    comparator: str | None = None
    value: Decimal | None = None
    text: str | None = None


def parse_value(raw: str | float | int | Decimal | None) -> Parsed:
    """'0,46' → 0.46; '< 0,5' → ('<', 0.5); 'negativ' → text. Scale is kept ('2,90' → 2.90)."""
    if raw is None:
        return Parsed()
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        return Parsed(value=Decimal(str(raw)))
    s = unicodedata.normalize("NFKC", str(raw)).strip()
    if not s:
        return Parsed()
    # A number, optionally with comparator and followed by a unit or flag ("0,46 mmol/l", "12 +").
    if m := re.fullmatch(rf"(<=|>=|<|>|≤|≥)?\s*({_NUM})(?:\s+\S.*|[a-zA-Zµ/%*].*)?", s):
        return Parsed(_CMP[m.group(1)] if m.group(1) else None, to_decimal(m.group(2)))
    return Parsed(text=s[:80])


def parse_reference(raw: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Lower and upper bound of a printed reference range; (None, None) when it is not numeric."""
    if not raw:
        return None, None
    s = unicodedata.normalize("NFKC", raw).strip().lower()
    s = s.replace("–", "-").replace("—", "-")
    if m := re.match(rf"^\s*({_NUM})\s*(?:-|bis)\s*({_NUM})", s):
        return to_decimal(m.group(1)), to_decimal(m.group(2))
    if m := re.match(rf"^\s*(<=|<|≤|bis|unter|kleiner als|kleiner|max\.?)\s*({_NUM})", s):
        return None, to_decimal(m.group(2))
    if m := re.match(rf"^\s*(>=|>|≥|ab|über|ueber|größer als|groesser als|größer|min\.?)\s*({_NUM})", s):
        return to_decimal(m.group(2)), None
    return None, None


_HIGH = {"h", "+", "++", "+++", "hh", "↑", "↑↑", "hoch", "high", "erhöht", "erhoeht", ">"}
_LOW = {"l", "-", "--", "ll", "↓", "↓↓", "niedrig", "low", "erniedrigt", "<"}


def flag_status(flag: str | None) -> str | None:
    if not flag:
        return None
    f = flag.strip().lower().replace(" ", "")
    if f in _HIGH or f.startswith("+"):
        return "high"
    if f in _LOW:
        return "low"
    return None


def range_status(
    value: float | Decimal | None,
    comparator: str | None,
    low: float | Decimal | None,
    high: float | Decimal | None,
) -> str | None:
    """'low' | 'normal' | 'high' against [low, high] (bounds count as normal); None when unknown.

    '<0,5' against '< 5' is normal; '<3' against '< 1' cannot be judged.
    """
    if value is None or (low is None and high is None):
        return None
    v = float(value)
    lo = float(low) if low is not None else None
    hi = float(high) if high is not None else None
    if comparator in ("<", "<="):  # true value is at most v
        if lo is not None and v <= lo:
            return "low"
        return "normal" if hi is None or v <= hi else None
    if comparator in (">", ">="):  # true value is at least v
        if hi is not None and v >= hi:
            return "high"
        return "normal" if lo is None or v >= lo else None
    if lo is not None and v < lo:
        return "low"
    if hi is not None and v > hi:
        return "high"
    return "normal"
