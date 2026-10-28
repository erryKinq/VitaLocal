# ruff: noqa: E501 - export samples keep their original line layout
"""Exports of other apps: daily totals (MyFitnessPal, Cronometer, weight lists), workouts (Strong, Hevy).

The samples follow the apps' documented export layouts; real exports may differ in details
(column names in other languages, units) and should be added here when they turn up.
"""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from vitalocal import exercise_library
from vitalocal.ingest import daily_csv, workout_csv
from vitalocal.ingest.service import detect_source

AUTH = {"Authorization": "Bearer test-token"}

MFP = b"""Date,Meal,Calories,Fat (g),Saturated Fat,Polyunsaturated Fat,Monounsaturated Fat,Trans Fat,Cholesterol,Sodium (mg),Potassium,Carbohydrates (g),Fiber,Sugar,Protein (g),Vitamin A,Vitamin C,Calcium,Iron,Note
2026-09-20,Breakfast,450,12,4,1,2,0,30,400,300,60,8,20,25,0,0,0,0,
2026-09-20,Lunch,700,25,8,2,3,0,60,800,500,70,6,10,45,0,0,0,0,
2026-09-21,Dinner,600.5,20,5,1,2,0,50,1200,400,55,7,8,40,0,0,0,0,
"""
CRONOMETER = b"""Date,Energy (kcal),Alcohol (g),Caffeine (mg),Water (g),Fiber (g),Starch (g),Sugars (g),Net Carbs (g),Carbs (g),Fat (g),Monounsaturated (g),Polyunsaturated (g),Saturated (g),Trans-Fats (g),Protein (g),Sodium (mg),Completed
2026-09-20,2100,0,95,2000,30,100,60,200,230,70,25,15,22,0,140,2000,true
2026-09-21,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,false
"""
WEIGHTS = b"date;Weight (lb);Body Fat (%)\n20.09.2026;176,4;18,5\n21.09.2026;175;\n"
STRONG = b"""Date;Workout Name;Duration;Exercise Name;Set Order;Weight;Reps;Distance;Seconds;Notes;Workout Notes;RPE
2026-09-20 18:00:00;"Push";1h 5m;"Bench Press (Barbell)";W;40;10;0;0;"";"";
2026-09-20 18:00:00;"Push";1h 5m;"Bench Press (Barbell)";1;80;8;0;0;"";"";8
2026-09-20 18:00:00;"Push";1h 5m;"Lat Pulldown (Cable)";1;60;10;0;0;"";"";9
2026-09-20 18:00:00;"Push";1h 5m;"Plank";1;0;0;0;60;"";"";
2026-09-20 18:00:00;"Push";1h 5m;"Zercher Carry";1;50;0;0;40;"";"";
"""
HEVY = b""""title","start_time","end_time","description","exercise_title","superset_id","exercise_notes","set_index","set_type","weight_kg","reps","distance_km","duration_seconds","rpe"
"Legs","21 Sep 2026, 17:00","21 Sep 2026, 18:10","","Squat (Barbell)",,"",0,"warmup",60,8,,,
"Legs","21 Sep 2026, 17:00","21 Sep 2026, 18:10","","Squat (Barbell)",,"",1,"normal",100,5,,,8.5
"Legs","21 Sep 2026, 17:00","21 Sep 2026, 18:10","","Romanian Deadlift (Barbell)",,"",0,"normal",90,8,,,
"""


def test_detection():
    assert detect_source("a.csv", MFP[:4096]) == "daily_csv"
    assert detect_source("a.csv", CRONOMETER[:4096]) == "daily_csv"
    assert detect_source("a.csv", WEIGHTS) == "daily_csv"
    assert detect_source("a.csv", STRONG[:4096]) == "workout_csv"
    assert detect_source("a.csv", HEVY[:4096]) == "workout_csv"
    with pytest.raises(ValueError):
        detect_source("a.csv", b"name,phone\nx,1\n")


def test_daily_totals():
    p = daily_csv.parse(MFP, "Europe/Berlin")
    assert p.app == "myfitnesspal" and p.columns["Fat (g)"] == "nutrition.fat"
    assert p.columns["Saturated Fat"] == "nutrition.saturated_fat" and "Trans Fat" in p.ignored_columns
    day = {(s.ts.date().isoformat(), s.metric): s.value for s in p.samples}
    assert day[("2026-09-20", "nutrition.energy")] == 1150  # two meals summed
    assert day[("2026-09-20", "nutrition.salt")] == 3.0  # 1200 mg sodium × 2.5
    assert day[("2026-09-21", "nutrition.energy")] == 600.5

    c = daily_csv.parse(CRONOMETER, "UTC")
    assert c.app == "cronometer" and c.columns["Saturated (g)"] == "nutrition.saturated_fat"
    cday = {(s.ts.date().isoformat(), s.metric): s.value for s in c.samples}
    assert (
        cday[("2026-09-20", "nutrition.carbohydrates")] == 230 and cday[("2026-09-20", "nutrition.fat")] == 70
    )
    assert not any(d == "2026-09-21" for d, _ in cday)  # nothing logged that day: no intake of 0

    w = daily_csv.parse(WEIGHTS, "UTC")
    wday = {(s.ts.date().isoformat(), s.metric): s.value for s in w.samples}
    assert wday[("2026-09-20", "weight")] == pytest.approx(80.01, abs=0.01)
    assert wday[("2026-09-20", "body_fat")] == 18.5 and ("2026-09-21", "body_fat") not in wday


def test_workout_parse_and_names():
    s = workout_csv.parse(STRONG, "Europe/Berlin", "kg")
    assert s.app == "strong" and len(s.workouts) == 1
    w = s.workouts[0]
    assert w.seconds == 65 * 60 and w.title == "Push" and w.rpe == 8.5
    assert [x.warmup for x in w.sets[:2]] == [True, False] and w.sets[3].duration_s == 60
    lb = workout_csv.parse(STRONG, "UTC", "lb").workouts[0]
    assert lb.sets[1].weight_kg == pytest.approx(36.29, abs=0.01)

    h = workout_csv.parse(HEVY, "UTC").workouts[0]
    assert h.seconds == 70 * 60 and h.sets[0].warmup and h.sets[1].weight_kg == 100

    lib = list(exercise_library.entries())
    m = lambda n: workout_csv.library_match(n, lib)  # noqa: E731
    assert m("Bench Press (Barbell)") == "barbell_bench_press"
    assert m("Squat (Barbell)") == "barbell_back_squat"
    assert m("Bent Over Row (Barbell)") == "barbell_row"
    assert m("Lat Pulldown (Cable)") == "lat_pulldown"
    assert m("Bicep Curl (Dumbbell)") == "dumbbell_curl"
    assert m("Romanian Deadlift (Barbell)") == "romanian_deadlift"
    assert m("Triceps Pushdown (Cable)") == "triceps_pressdown"
    assert m("Incline Bench Press (Dumbbell)") == "incline_dumbbell_bench_press"
    assert m("Zercher Carry") is None


@pytest.fixture
def client(engine, store):
    from vitalocal.main import app

    return TestClient(app)


def _upload(client, name, data):
    r = client.post(
        "/api/v1/ingest/upload", headers=AUTH, files={"file": (name, io.BytesIO(data), "text/csv")}
    )
    assert r.status_code in (200, 202), r.text
    return r.json()


@pytest.mark.db
def test_imports_end_to_end(client, engine):
    _upload(client, "mfp.csv", MFP)
    _upload(client, "strong.csv", STRONG)
    _upload(client, "hevy.csv", HEVY)
    with engine.connect() as conn:
        kcal = conn.execute(
            text("SELECT value FROM sample WHERE metric = 'nutrition.energy' ORDER BY ts LIMIT 1")
        ).scalar()
        assert kcal == 1150
        acts = conn.execute(
            text("SELECT id, name, elapsed_s FROM activity WHERE manual ORDER BY start_ts")
        ).all()
        assert [a.name for a in acts] == ["Push", "Legs"]
        names = set(conn.execute(text("SELECT name FROM exercise")).scalars())
        assert "Zercher Carry" in names  # unknown: created as an own exercise
        rpe = conn.execute(text("SELECT value FROM checkin WHERE kind = 'rpe' AND activity_id = :a"),
                           {"a": acts[0].id}).scalar()  # fmt: skip
        assert rpe == 8.5
        reviewed = conn.execute(text("SELECT reviewed_at FROM training_session WHERE activity_id = :a"),
                                {"a": acts[0].id}).scalar()  # fmt: skip
        assert reviewed is not None  # imported history feeds the progression
    session = client.get(f"/api/v1/training/sessions/{acts[0].id}", headers=AUTH).json()
    assert len(session["sets"]) == 5

    # the same export again adds nothing
    again = _upload(client, "strong2.csv", STRONG + b"\n")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM activity WHERE manual")).scalar() == 2
    assert again["source"] == "workout_csv"


@pytest.mark.db
def test_export_zip(client, engine):
    import json as _json
    import zipfile

    _upload(client, "mfp.csv", MFP)
    _upload(client, "strong.csv", STRONG)
    client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "https://api.openai.com/v1", "model": "m", "api_key": "sk-secret"},
    )
    r = client.get("/api/v1/export", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"manifest.json", "food_diary.csv", "body.csv", "strength_sets.csv", "daily_values.csv"} <= names
    assert "tables/sample.csv" in names and "tables/raw_artifact.csv" not in names
    manifest = _json.loads(z.read("manifest.json"))
    assert manifest["rows"]["activity"] == 1
    assert b"sk-secret" not in z.read("tables/app_setting.csv") and b"fernet:" not in z.read(
        "tables/app_setting.csv"
    )
    sets = z.read("strength_sets.csv").decode().splitlines()
    assert sets[0].startswith("start_ts,workout,exercise") and len(sets) == 6
    daily = z.read("daily_values.csv").decode().splitlines()
    assert any(line.startswith("2026-09-20,") for line in daily)


HC = {
    "range": {"from": "2026-09-20T00:00:00+02:00", "to": "2026-09-21T23:59:00+02:00"},
    "source_apps": ["com.sec.android.app.shealth"],
    "steps": [{"start": "2026-09-20T08:00:00+02:00", "end": "2026-09-20T09:00:00+02:00", "count": 1200},
              {"start": "2026-09-20T17:00:00+02:00", "end": "2026-09-20T18:00:00+02:00", "count": 3000}],
    "active_calories": [{"start": "2026-09-20T17:00:00+02:00", "end": "2026-09-20T18:00:00+02:00", "kcal": 350}],
    "total_calories": [{"start": "2026-09-20T00:00:00+02:00", "end": "2026-09-20T23:59:00+02:00", "kcal": 2350}],
    "weight": [{"time": "2026-09-20T07:00:00+02:00", "kg": 71.2}],
    "resting_heart_rate": [{"time": "2026-09-20T07:00:00+02:00", "bpm": 54}],
    "sleep": [{"start": "2026-09-19T23:00:00+02:00", "end": "2026-09-20T06:30:00+02:00", "stages": [
        {"start": "2026-09-19T23:00:00+02:00", "end": "2026-09-20T01:00:00+02:00", "stage": 4},
        {"start": "2026-09-20T01:00:00+02:00", "end": "2026-09-20T02:30:00+02:00", "stage": 5},
        {"start": "2026-09-20T02:30:00+02:00", "end": "2026-09-20T03:00:00+02:00", "stage": 1},
        {"start": "2026-09-20T03:00:00+02:00", "end": "2026-09-20T06:30:00+02:00", "stage": 6}]}],
    "exercise": [{"id": "abc", "start": "2026-09-20T17:00:00+02:00", "end": "2026-09-20T17:45:00+02:00",
                  "type": 56, "title": "Evening run", "kcal": 420}],
}  # fmt: skip


@pytest.mark.db
def test_health_connect_batch(client, engine):
    import json as _json

    r = client.post("/api/v1/ingest/health-connect", headers=AUTH, content=_json.dumps(HC))
    assert r.status_code == 202, r.text
    day = client.get("/api/v1/days/2026-09-20", headers=AUTH).json()
    assert day["summary"]["steps"]["value"] == 4200
    assert day["summary"]["active_energy"]["value"] == 350
    assert day["summary"]["resting_metabolic_rate"]["value"] == 2000  # total − active
    assert day["energy"] == {"rest": 2000, "active": 350, "total": 2350, "source": "watch"}
    assert day["sleep"]["asleep_minutes"] == 420  # 2 h light + 1.5 h deep + 3.5 h REM
    run = [a for a in day["activities"] if a["sport"] == "running"]
    assert run and run[0]["energy_kcal"] == 420

    # a newer batch of the same window without the weight: it is retracted; the run stays one
    HC2 = {**HC, "weight": [], "steps": HC["steps"][:1]}
    client.post("/api/v1/ingest/health-connect", headers=AUTH, content=_json.dumps(HC2))
    day = client.get("/api/v1/days/2026-09-20", headers=AUTH).json()
    assert day["summary"]["steps"]["value"] == 1200 and "weight" not in day["summary"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM activity WHERE sport = 'running'")).scalar() == 1
