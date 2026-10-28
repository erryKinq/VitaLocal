import json

import pytest

from tests.fixtures import waistline_export
from vitalocal.ingest.service import SOURCE_GADGETBRIDGE, SOURCE_WAISTLINE, detect_source
from vitalocal.ingest.waistline import parse


def _parse(data=None):
    return parse(json.dumps(data or waistline_export()).encode(), "Europe/Berlin")


def test_consumed_nutrients_scale_with_portion_and_quantity():
    p = _parse()
    skyr, apples, bolo = p.entries
    assert skyr.nutrients["calories"] == 150  # 250 g of a per-100 g food
    assert apples.nutrients["calories"] == 160  # 1 portion x quantity 2
    assert bolo.nutrients["calories"] == 500  # 1 of 4 recipe portions
    assert (skyr.meal, apples.meal, bolo.meal) == ("Breakfast", "Snacks", "Abendessen")
    assert skyr.day.isoformat() == "2026-09-21"


def test_daily_totals_count_undeclared_nutrients_as_zero():
    p = _parse()
    day = {s.metric: s.value for s in p.samples if s.ts.date().isoformat() == "2026-09-21"}
    assert day["nutrition.energy"] == 810
    assert day["weight"] == 64.6
    # fiber is undeclared for some entries: they count as 0, the declared fiber is summed
    fiber = sum(e.nutrients.get("fiber", 0) for e in p.entries if e.day.isoformat() == "2026-09-21")
    assert fiber > 0 and day["nutrition.fiber"] == round(fiber, 3)
    assert day["nutrition.salt"] == 0  # declared by no entry
    assert p.missing_nutrients["2026-09-21"]["fiber"] >= 1
    # comma decimal in body stats, day without food entries has no nutrition totals
    later = {s.metric: s.value for s in p.samples if s.ts.date().isoformat() == "2026-09-22"}
    assert later == {"weight": 64.4}


def test_settings_credentials_never_reach_parsed_output():
    p = _parse()
    assert "secret-pw" not in repr(p.entries) + repr(p.samples) + json.dumps(p.stats(), default=str)


def test_rejects_other_json_and_detects_sources():
    with pytest.raises(ValueError):
        parse(b'{"foo": 1}')
    assert detect_source("x.zip", b"PK\x03\x04") == SOURCE_GADGETBRIDGE
    assert detect_source("w.json", b'  {"version"') == SOURCE_WAISTLINE
    with pytest.raises(ValueError):
        detect_source("a.png", b"\x89PNG")


DIARY_CSV = (
    "Datum;Kalorien (kcal);Fett (g);Gesättigte Fettsäuren (g);Kohlenhydrate (g);Zucker (g);"
    "Ballaststoffe (g);Eiweiß (g);Salz (g);Gewicht (kg)\n"
    "21.09.2026;2186,29;70,31;18,72;193,31;114,2;36,43;174,24;1,29;\n"
    "22.09.2026;0;;;;;;;;64,4\n"
    "23.09.2026;1900;60;20;180;50;25;150;5;64,6\n"
).encode()


def test_diary_csv_daily_totals_and_weight():
    from vitalocal.ingest.waistline_csv import looks_like_diary_csv
    from vitalocal.ingest.waistline_csv import parse as parse_csv

    assert looks_like_diary_csv(DIARY_CSV[:4096])  # detection reads the first 4 KiB
    p = parse_csv(DIARY_CSV, "Europe/Berlin")
    by_day = {}
    for s in p.samples:
        by_day.setdefault(s.ts.date().isoformat(), {})[s.metric] = s.value
    assert by_day["2026-09-21"]["nutrition.energy"] == 2186.29
    assert by_day["2026-09-21"]["nutrition.saturated_fat"] == 18.72  # not mistaken for "Fett"
    assert by_day["2026-09-21"]["nutrition.protein"] == 174.24
    assert "weight" not in by_day["2026-09-21"]  # empty cell: not measured
    # 0 kcal with empty nutrients = nothing logged: weight only, never an intake of 0
    assert by_day["2026-09-22"] == {"weight": 64.4}
    assert p.counts["days_without_food"] == 1 and p.ignored_columns == []
    assert detect_source("diary_export.csv", DIARY_CSV[:4096]) == "waistline_diary_csv"


def test_diary_csv_english_headers():
    from vitalocal.ingest.waistline_csv import parse as parse_csv

    raw = (
        b"Date,Calories (kcal),Saturated Fat (g),Fat (g),Proteins (g),Weight (kg)\n"
        b"2026-09-21,2000,10,60,150,70.5\n"
    )
    p = parse_csv(raw)
    assert {s.metric: s.value for s in p.samples} == {
        "nutrition.energy": 2000,
        "nutrition.saturated_fat": 10,
        "nutrition.fat": 60,
        "nutrition.protein": 150,
        "weight": 70.5,
    }
    with pytest.raises(ValueError):
        parse_csv(b"foo;bar\n1;2\n")


def test_goals_are_read_as_bounds():
    p = _parse()
    assert p.goals == {
        "nutrition.energy": (2000, None),  # minimum-goal
        "nutrition.protein": (128, None),
        "nutrition.salt": (None, 6),  # otherwise Waistline's "stay below"
    }  # kilojoules duplicate calories, weight is no daily bound
    assert any("fat: goals per weekday" in w for w in p.warnings)  # not guessed
    assert p.stats()["goals"]["nutrition.salt"] == {"min": None, "max": 6}
