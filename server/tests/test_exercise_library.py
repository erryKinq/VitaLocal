"""Built-in exercise library: valid entries (watch exercises exist, muscles and equipment known) and
the filters."""

from vitalocal import exercise_library, training
from vitalocal import exercises as fit_exercises


def test_entries_are_valid():
    entries = exercise_library.entries()
    assert len(entries) >= 120
    keys = [e["key"] for e in entries]
    assert len(set(keys)) == len(keys)
    names = [e["name"].lower() for e in entries]
    assert len(set(names)) == len(names)
    for e in entries:
        assert 1 <= len(e["name"]) <= 80, e["key"]
        assert e["kind"] in ("reps", "time"), e["key"]
        assert e["muscles"], e["key"]
        assert set(e["muscles"]) | set(e["muscles_secondary"]) <= set(training.MUSCLES), e["key"]
        assert not set(e["muscles"]) & set(e["muscles_secondary"]), e["key"]
        assert set(e["equipment"]) <= set(training.EQUIPMENT), e["key"]
        assert 0 < e["increment_kg"] <= 50, e["key"]
        assert len(e.get("note") or "") <= 500, e["key"]
        if e["garmin_category"] is not None:
            assert fit_exercises.is_category(e["garmin_category"]), e["key"]
        if e["garmin_name"] is not None:
            assert fit_exercises.is_name(e["garmin_category"], e["garmin_name"]), e["key"]


def test_no_entry_uses_the_old_coarse_muscles():
    for e in exercise_library.entries():
        assert not {"chest", "shoulders", "back"} & set(e["muscles"] + e["muscles_secondary"]), e["key"]


def test_every_muscle_and_equipment_has_exercises():
    entries = exercise_library.entries()
    assert {m for e in entries for m in e["muscles"]} == set(training.MUSCLES)
    assert {x for e in entries for x in e["equipment"]} == set(training.EQUIPMENT)


def test_search_filters_by_muscle_equipment_and_name():
    home = exercise_library.search(equipment={"dumbbell", "bench"})
    assert home and all(set(e["equipment"]) <= {"dumbbell", "bench"} for e in home)
    assert any(e["key"] == "push_up" for e in home)  # nothing needed: always fits
    assert not any(e["key"] == "lat_pulldown" for e in home)
    bare = exercise_library.search(equipment=set())
    assert bare and all(not e["equipment"] for e in bare)
    shoulders = exercise_library.search(muscle="shoulders")  # a group: any of its parts
    assert {"dumbbell_lateral_raise", "face_pull", "band_external_rotation"} <= {e["key"] for e in shoulders}
    assert [e["key"] for e in exercise_library.search(muscle="neck")][:1] == ["neck_flexion_with_weight"]
    biceps = exercise_library.search(muscle="biceps")
    assert biceps and all("biceps" in e["muscles"] for e in biceps)
    assert [e["key"] for e in exercise_library.search(q="hammercurl")] == ["hammer_curl"]
