"""Food tracking: catalog, diary entries, daily totals from entries, body entries, Waistline takeover
and the Open Food Facts client (mocked, no network)."""

import json
import uuid
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fixtures import waistline_export
from tests.test_waistline import DIARY_CSV
from vitalocal import off

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store):
    from vitalocal.main import app

    off.reset_limits()
    return TestClient(app)


def _upload(client, name: str, body: bytes, kind: str = "application/json"):
    return client.post("/api/v1/ingest/upload", headers=AUTH, files={"file": (name, body, kind)})


def _waistline(client, export=None):
    r = _upload(client, "waistline_export.json", json.dumps(export or waistline_export()).encode())
    assert r.status_code == 202, r.text
    batch = client.get(f"/api/v1/ingest/batches/{r.json()['batch_id']}", headers=AUTH).json()
    assert batch["status"] == "completed", batch


def _food(client, **kw) -> dict:
    body = {"id": str(uuid.uuid4()), "name": "Haferflocken", "nutrients": {"calories": 370, "proteins": 13.5}}
    r = client.post("/api/v1/foods", headers=AUTH, json=body | kw)
    assert r.status_code == 201, r.text
    return r.json()


def _entry(client, expect=201, **kw) -> dict:
    body = {"id": str(uuid.uuid4()), "logged_at": "2026-10-01T08:00:00+02:00", "meal_index": 0}
    r = client.post("/api/v1/nutrition/entries", headers=AUTH, json=body | kw)
    assert r.status_code == expect, r.text
    return r.json()


def _trend(client, metric: str, frm: str, to: str) -> dict:
    """{day: value}; for several comma-separated metrics {metric: {day: value}}."""
    r = client.get("/api/v1/trends", headers=AUTH, params={"metrics": metric, "from": frm, "to": to})
    out = {m: {d["day"]: d["value"] for d in v["days"]} for m, v in r.json()["metrics"].items()}
    return out if "," in metric else out[metric]


def test_catalog_entries_and_daily_totals(client):
    oats = _food(client, portions=[{"label": "1 Portion", "amount": 50}], barcode="4 000000 000001")
    assert oats["barcode"] == "4000000000001" and oats["usage"]["count"] == 0
    # same id again: unchanged, 200
    r = client.post("/api/v1/foods", headers=AUTH, json={"id": oats["id"], "name": "anders"})
    assert r.status_code == 200 and r.json()["name"] == "Haferflocken"

    e = _entry(client, food_id=oats["id"], amount=50, quantity=2, entered_via="android")
    assert (e["nutrients"], e["day"], e["meal"], e["source"]) == (
        {"calories": 370.0, "proteins": 13.5}, "2026-10-01", "Frühstück", "vitalocal",
    )  # fmt: skip
    assert e["editable"] and e["entered_via"] == "android" and e["unit"] == "g"
    # the outbox replays: same entry, 200, nothing doubled
    again = _entry(client, expect=200, id=e["id"], food_id=oats["id"], amount=500)
    assert again["nutrients"] == e["nutrients"]
    _entry(
        client,
        name="Kuchen",
        nutrients={"calories": 300},
        meal_index=3,
        logged_at="2026-10-01T15:00:00+02:00",
    )

    assert _trend(client, "nutrition.energy", "2026-09-30", "2026-10-02") == {"2026-10-01": 670}
    # the cake declares no protein: it counts as 0
    assert _trend(client, "nutrition.protein", "2026-09-30", "2026-10-02") == {"2026-10-01": 13.5}
    day = client.get("/api/v1/nutrition/2026-10-01", headers=AUTH).json()
    assert [m["name"] for m in day["meals"]] == ["Frühstück", "Mittagessen", "Abendessen", "Snacks"]
    assert day["meals"][3]["totals"]["calories"] == 300 and day["totals"]["proteins"]["missing"] == 1

    # a food change applies to later entries only
    upd = dict(oats, nutrients={"calories": 400, "proteins": 14}, favorite=True)
    upd = {k: upd[k] for k in ("name", "base_amount", "base_unit", "portions", "nutrients", "favorite")}
    assert client.put(f"/api/v1/foods/{oats['id']}", headers=AUTH, json=upd).status_code == 200
    # correcting the amount scales the snapshot (370 kcal/100 g), not the changed food
    r = client.put(
        f"/api/v1/nutrition/entries/{e['id']}",
        headers=AUTH,
        json={"logged_at": "2026-10-01T09:00:00+02:00", "meal_index": 1, "amount": 50, "quantity": 1},
    )
    assert (
        r.status_code == 200
        and r.json()["nutrients"]["calories"] == 185
        and r.json()["meal"] == "Mittagessen"
    )

    listed = client.get("/api/v1/foods", headers=AUTH).json()
    assert listed[0]["id"] == oats["id"] and listed[0]["usage"]["last_amount"] == 50
    assert client.get("/api/v1/foods", headers=AUTH, params={"q": "hafer"}).json()[0]["favorite"]

    # copy "like yesterday": idempotent through the new ids
    new_id = str(uuid.uuid4())
    body = {"day": "2026-10-02", "items": [{"from": e["id"], "id": new_id}]}
    for created in (1, 0):
        r = client.post("/api/v1/nutrition/copy", headers=AUTH, json=body)
        assert r.status_code == 200 and r.json()["created"] == created
    # same time of day (compared as an instant: the offset follows the database's time zone)
    copied = datetime.fromisoformat(r.json()["entries"][0]["logged_at"])
    assert copied == datetime.fromisoformat("2026-10-02T09:00:00+02:00")
    assert _trend(client, "nutrition.energy", "2026-10-02", "2026-10-02") == {"2026-10-02": 185}

    # used foods are archived, unused ones deleted
    unused = _food(client, name="Nie benutzt")
    for f in (oats, unused):
        assert client.delete(f"/api/v1/foods/{f['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/api/v1/foods/{oats['id']}", headers=AUTH).json()["archived"] is True
    assert client.get(f"/api/v1/foods/{unused['id']}", headers=AUTH).status_code == 404
    assert client.delete(f"/api/v1/nutrition/entries/{new_id}", headers=AUTH).status_code == 204
    assert _trend(client, "nutrition.energy", "2026-10-02", "2026-10-02") == {}


def test_dish_from_components(client):
    pasta = _food(client, name="Nudeln", nutrients={"calories": 350, "proteins": 12})
    sauce = _food(client, name="Soße", nutrients={"calories": 80, "salt": 1})
    dish = _food(
        client,
        name="Nudeln mit Soße",
        base_amount=2,
        base_unit="portion",
        components=[{"food_id": pasta["id"], "amount": 200}, {"food_id": sauce["id"], "amount": 300}],
    )
    assert dish["nutrients"] == {"calories": 940, "proteins": 24, "salt": 3}
    assert [c["name"] for c in dish["components"]] == ["Nudeln", "Soße"]
    e = _entry(client, food_id=dish["id"], amount=1)
    assert e["item_type"] == "recipe" and e["nutrients"]["calories"] == 470
    cyc = dict(name="Soße", components=[{"food_id": dish["id"], "amount": 1}])
    assert client.put(f"/api/v1/foods/{sauce['id']}", headers=AUTH, json=cyc).status_code == 422


def test_entries_replace_imported_totals_and_feed_habits(client):
    # Waistline diary CSV: daily totals as samples (2026-09-21 and -23)
    assert _upload(client, "diary_export.csv", DIARY_CSV, "text/csv").status_code == 202
    assert _trend(client, "nutrition.protein", "2026-09-21", "2026-09-23") == {
        "2026-09-21": 174.24, "2026-09-23": 150,
    }  # fmt: skip
    # entries on the 23rd replace that day's imported total; the 21st keeps its CSV total
    food = _food(client, name="Quark", nutrients={"calories": 70, "proteins": 12})
    _entry(client, food_id=food["id"], amount=1000, logged_at="2026-09-23T12:00:00+02:00")
    assert _trend(client, "nutrition.protein", "2026-09-21", "2026-09-23") == {
        "2026-09-21": 174.24, "2026-09-23": 120,
    }  # fmt: skip

    habit = {"name": "Eiweiß", "metric": "nutrition.protein", "min_value": 120, "max_value": 180}
    assert client.post("/api/v1/habits", headers=AUTH, json=habit).status_code == 201
    rows = client.get(
        "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-21", "to": "2026-09-23"}
    )
    days = next(h for h in rows.json() if h["name"] == "Eiweiß")["days"]
    assert days["2026-09-23"]["done"] is True and days["2026-09-21"]["done"] is True

    today = client.get("/api/v1/today", headers=AUTH, params={"day": "2026-09-23"}).json()
    assert today["nutrition"] == {"entries": 1, "totals": today["nutrition"]["totals"]}
    assert today["nutrition"]["totals"]["proteins"] == 120


def test_body_entries_feed_weight_and_drink_goal(client):
    for i, (ts, v) in enumerate((("2026-09-20T07:00:00+02:00", 70.0), ("2026-09-22T07:00:00+02:00", 69.2))):
        body = {"id": str(uuid.UUID(int=i + 1)), "ts": ts, "value": v, "source": "android"}
        assert client.post("/api/v1/body", headers=AUTH, json=body).status_code == 201
    assert client.post("/api/v1/body", headers=AUTH, json=body).status_code == 200
    assert _trend(client, "weight", "2026-09-19", "2026-09-23") == {"2026-09-20": 70.0, "2026-09-22": 69.2}
    goal = client.get("/api/v1/goals", headers=AUTH, params={"day": "2026-09-21"}).json()["goals"]
    assert next(g for g in goal if g["metric"] == "nutrition.drinks")["formula"]["weight_kg"] == 70.0
    listed = client.get(
        "/api/v1/body", headers=AUTH, params={"from": "2026-09-01", "to": "2026-09-30"}
    ).json()
    assert [x["value"] for x in listed] == [69.2, 70.0] and all(x["editable"] for x in listed)
    assert client.delete(f"/api/v1/body/{uuid.UUID(int=2)}", headers=AUTH).status_code == 204
    assert _trend(client, "weight", "2026-09-19", "2026-09-23") == {"2026-09-20": 70.0}


def test_waistline_takeover(client):
    _waistline(client)
    entries = client.get("/api/v1/nutrition/2026-09-21", headers=AUTH).json()
    before = (
        _trend(client, "nutrition.energy,nutrition.protein", "2026-09-21", "2026-09-21"),
        client.get("/api/v1/goals", headers=AUTH, params={"day": "2026-09-21"}).json()["goals"],
    )
    wl = next(e for m in entries["meals"] for e in m["entries"])
    assert wl["source"] == "waistline" and not wl["editable"]
    upd = {"logged_at": wl["logged_at"], "meal_index": 0, "amount": 100}
    assert client.put(f"/api/v1/nutrition/entries/{wl['id']}", headers=AUTH, json=upd).status_code == 409
    status = client.get("/api/v1/nutrition/waistline", headers=AUTH).json()
    assert status["retired_at"] is None and status["latest_export"]["status"] == "completed"

    r = client.post("/api/v1/nutrition/waistline-takeover", headers=AUTH)
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["foods"], t["recipes"], t["linked_entries"]) == (2, 1, 3)
    assert t["meals"] == ["Frühstück", "Mittagessen", "Abendessen", "Snacks"]
    assert client.post("/api/v1/nutrition/waistline-takeover", headers=AUTH).status_code == 409

    # same totals and goals as before; now from linked, editable entries
    after = (
        _trend(client, "nutrition.energy,nutrition.protein", "2026-09-21", "2026-09-21"),
        client.get("/api/v1/goals", headers=AUTH, params={"day": "2026-09-21"}).json()["goals"],
    )
    assert after == before
    foods = {f["name"]: f for f in client.get("/api/v1/foods", headers=AUTH, params={"q": "o"}).json()}
    bolo = foods["Bolognese"]
    assert (bolo["base_amount"], bolo["base_unit"], bolo["source"]) == (4, "portion", "waistline")
    apple = client.get("/api/v1/foods", headers=AUTH, params={"q": "apple"}).json()[0]
    assert apple["base_unit"] == "portion" and apple["usage"]["count"] == 1

    day = client.get("/api/v1/nutrition/2026-09-21", headers=AUTH).json()
    wl = next(e for m in day["meals"] for e in m["entries"] if e["name"] == "Apple")
    assert wl["editable"] and wl["food_id"] == apple["id"]
    upd = {"logged_at": wl["logged_at"], "meal_index": 3, "amount": 1, "quantity": 1}
    r = client.put(f"/api/v1/nutrition/entries/{wl['id']}", headers=AUTH, json=upd)
    assert r.status_code == 200 and r.json()["nutrients"]["calories"] == 80  # was 2 × 80

    # Waistline exports are refused from now on
    export = waistline_export()
    export["diary"][1]["stats"] = {"weight": 60}
    r = _upload(client, "waistline_export.json", json.dumps(export).encode())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "waistline_retired"
    assert _upload(client, "diary_export.csv", DIARY_CSV, "text/csv").status_code == 409


# --- Open Food Facts (mocked) --------------------------------------------------------------------------

PRODUCT = {
    "code": "4012345678901",
    "product_name": "Skyr",
    "brands": "Milchhof, Other",
    "product_quantity": 450,
    "product_quantity_unit": "g",
    "serving_size": "150 g",
    "serving_quantity": 150,
    "nutriments": {
        "energy_100g": 263,
        "energy_unit": "kJ",
        "proteins_100g": 11,
        "fat_100g": 0.2,
        "sodium_100g": 0.04,
        "calcium_100g": 0.12,
    },
}


@pytest.fixture
def off_mock(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.startswith("/api/v2/product/4012345678901"):
            return httpx.Response(200, json={"status": 1, "product": PRODUCT})
        if request.url.path.startswith("/api/v2/product/5"):
            raise httpx.ConnectTimeout("down")
        if request.url.path == "/cgi/search.pl" and request.url.params.get("search_terms") == "busy":
            return httpx.Response(200, text="<!DOCTYPE html><title>Page temporarily unavailable</title>")
        if request.url.path == "/cgi/search.pl":
            assert request.url.params["tag_0"] == "germany"
            nutriments = {"energy-kcal_100g": 43, "alcohol_100g": 5, "alcohol_unit": "% vol"}
            beer = {"code": "400000000002", "product_name": "Bier", "product_quantity_unit": "ml",
                    "nutriments": nutriments}  # fmt: skip
            return httpx.Response(200, json={"products": [PRODUCT, beer, {"code": "1"}]})
        return httpx.Response(404, json={"status": 0})

    monkeypatch.setattr(off, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("VITALOCAL_OFF_CONTACT", "test@example.org")
    from vitalocal import config

    config.get_settings.cache_clear()
    return calls


def test_off_barcode_lookup_is_stored_once(client, off_mock):
    r = client.get("/api/v1/foods/barcode/4012345678901", headers=AUTH)
    assert r.status_code == 200, r.text
    f = r.json()
    assert (f["found"], f["source"], f["name"], f["brand"], f["base_unit"]) == (
        "off",
        "off",
        "Skyr",
        "Milchhof",
        "g",
    )
    assert f["nutrients"] == {"calories": 62.86, "kilojoules": 263, "proteins": 11, "fat": 0.2,
                              "sodium": 40, "calcium": 120}  # fmt: skip
    assert [p["amount"] for p in f["portions"]] == [150, 450]
    assert "test@example.org" in off_mock[0].headers["user-agent"]
    # second scan: from the catalog, no request
    assert client.get("/api/v1/foods/barcode/04012345678901", headers=AUTH).json()["found"] == "catalog"
    assert len(off_mock) == 1
    assert client.get("/api/v1/foods/barcode/4099999999999", headers=AUTH).status_code == 404
    assert client.get("/api/v1/foods/barcode/5000000000000", headers=AUTH).status_code == 503
    assert client.get("/api/v1/foods/barcode/12", headers=AUTH).status_code == 422


def test_off_search_and_take_over(client, off_mock):
    hits = client.get("/api/v1/foods/online", headers=AUTH, params={"q": "skyr"}).json()
    assert [h["name"] for h in hits] == ["Skyr", "Bier"]  # no name: left out
    beer = hits[1]
    assert beer["base_unit"] == "ml" and beer["nutrients"] == {"calories": 43, "alcohol": 3.945}
    assert hits[0]["food_id"] is None
    assert client.get("/api/v1/foods/online", headers=AUTH, params={"q": "busy"}).status_code == 503
    r = client.post("/api/v1/foods/online/4012345678901", headers=AUTH)
    assert r.status_code == 201
    assert client.post("/api/v1/foods/online/4012345678901", headers=AUTH).status_code == 200
    assert (
        client.get("/api/v1/foods/online", headers=AUTH, params={"q": "skyr"}).json()[0]["food_id"]
        == r.json()["id"]
    )


def test_off_rate_limit_and_switch(client, off_mock, monkeypatch):
    for _ in range(off.SEARCH_PER_MINUTE):
        assert client.get("/api/v1/foods/online", headers=AUTH, params={"q": "x y"}).status_code == 200
    assert client.get("/api/v1/foods/online", headers=AUTH, params={"q": "x y"}).status_code == 429
    from vitalocal import config

    monkeypatch.setenv("VITALOCAL_OFF_ENABLED", "false")
    config.get_settings.cache_clear()
    assert client.get("/api/v1/foods/barcode/4012345678901", headers=AUTH).status_code == 503


def test_meal_templates(client):
    skyr = _food(client, name="Skyr", nutrients={"calories": 63, "proteins": 11})
    oats = _food(client, name="Haferflocken", nutrients={"calories": 370, "proteins": 13.5})
    body = {"name": "Skyr Bowl", "items": [{"food_id": skyr["id"], "amount": 250},
                                            {"food_id": oats["id"], "amount": 40}]}  # fmt: skip
    r = client.post("/api/v1/templates", headers=AUTH, json=body | {"id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    t = r.json()
    assert [i["name"] for i in t["items"]] == ["Skyr", "Haferflocken"]
    assert t["nutrients"]["calories"] == 63 * 2.5 + 370 * 0.4

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    log = {"logged_at": "2026-10-04T08:00:00+02:00", "meal_index": 0, "ids": ids}
    for created in (2, 0):  # a replay logs nothing twice
        r = client.post(f"/api/v1/templates/{t['id']}/log", headers=AUTH, json=log)
        assert r.status_code == 200 and r.json()["created"] == created
    entries = r.json()["entries"]
    assert {e["name"] for e in entries} == {"Skyr", "Haferflocken"}
    assert all(e["template_id"] == t["id"] for e in entries)
    # each entry stays editable on its own
    upd = {"logged_at": "2026-10-04T08:00:00+02:00", "meal_index": 0, "amount": 20, "quantity": 1}
    oat_entry = next(e for e in entries if e["name"] == "Haferflocken")
    assert (
        client.put(f"/api/v1/nutrition/entries/{oat_entry['id']}", headers=AUTH, json=upd).status_code == 200
    )
    assert _trend(client, "nutrition.energy", "2026-10-04", "2026-10-04") == {"2026-10-04": 157.5 + 74}
    assert client.get("/api/v1/templates", headers=AUTH).json()[0]["uses"] == 1

    # foods in a template are archived, not deleted; deleting the template keeps the entries
    unused = _food(client, name="Nur in Vorlage")
    x = {"name": "X", "items": [{"food_id": unused["id"], "amount": 10}]}
    t2 = client.post("/api/v1/templates", headers=AUTH, json=x).json()
    client.delete(f"/api/v1/foods/{unused['id']}", headers=AUTH)
    assert client.get(f"/api/v1/foods/{unused['id']}", headers=AUTH).json()["archived"] is True
    assert client.delete(f"/api/v1/templates/{t2['id']}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/v1/templates/{t['id']}", headers=AUTH).status_code == 204
    day = client.get("/api/v1/nutrition/2026-10-04", headers=AUTH).json()
    assert day["entries"] == 2 and all(e["template_id"] is None for m in day["meals"] for e in m["entries"])
    bad = {"name": "Y", "items": [{"food_id": str(uuid.uuid4()), "amount": 1}]}
    assert client.post("/api/v1/templates", headers=AUTH, json=bad).status_code == 422


def test_waistline_meals_become_templates(client):
    export = waistline_export()
    export["meals"] = [{"id": 3, "name": "Frühstück klassisch", "items": [
        {"id": 2, "type": "food", "portion": "1", "quantity": "2"},
        {"id": 7, "type": "recipe", "portion": "1", "quantity": "1"}]}]  # fmt: skip
    _waistline(client, export)
    r = client.post("/api/v1/nutrition/waistline-takeover", headers=AUTH)
    assert r.status_code == 200 and r.json()["templates"] == 1
    t = client.get("/api/v1/templates", headers=AUTH).json()
    assert [(i["name"], i["amount"], i["quantity"]) for i in t[0]["items"]] == [
        ("Apple", 1, 2),
        ("Bolognese", 1, 1),
    ]
    assert t[0]["source"] == "waistline"
    # running the separate import again changes nothing
    assert client.post("/api/v1/templates/waistline-import", headers=AUTH).json()["templates"] == 0


def test_meal_order_last_amount_and_nutrient_form(client):
    oats = _food(client)
    milk = _food(client, name="Milch", base_unit="ml")
    # added later but logged earlier in the day: the meal lists entries in the order they were added
    first = _entry(client, food_id=milk["id"], amount=200, logged_at="2026-10-01T09:00:00+02:00")
    second = _entry(client, food_id=oats["id"], amount=60, logged_at="2026-10-01T07:00:00+02:00")
    day = client.get("/api/v1/nutrition/2026-10-01", headers=AUTH).json()
    assert [e["id"] for e in day["meals"][0]["entries"]] == [first["id"], second["id"]]

    # the next entry suggests the amount of the newest one, also for foods not in "recent"
    _entry(client, food_id=oats["id"], amount=45, quantity=2, logged_at="2026-10-02T07:00:00+02:00")
    usage = client.get("/api/v1/foods/usage", headers=AUTH).json()
    assert usage[oats["id"]]["last_amount"] == 45 and usage[oats["id"]]["last_quantity"] == 2
    assert usage[oats["id"]]["count"] == 2 and usage[milk["id"]]["last_amount"] == 200

    cat = client.get("/api/v1/nutrients", headers=AUTH).json()
    assert [n["key"] for n in cat if n["form"]] == [
        "calories", "fat", "saturated-fat", "carbohydrates", "sugars", "fiber", "proteins", "salt",
    ]  # fmt: skip
    assert [n["key"] for n in cat[:8]] == [n["key"] for n in cat if n["form"]]

    # the food was wrong: corrected, this entry takes the new values, others keep theirs
    fixed = {k: oats[k] for k in ("name", "base_amount", "base_unit", "portions")} | {
        "nutrients": {"calories": 400}
    }
    assert client.put(f"/api/v1/foods/{oats['id']}", headers=AUTH, json=fixed).status_code == 200
    r = client.put(
        f"/api/v1/nutrition/entries/{second['id']}",
        headers=AUTH,
        json={"logged_at": "2026-10-01T07:00:00+02:00", "meal_index": 0, "quantity": 1, "recompute": True},
    )
    assert r.status_code == 200 and r.json()["nutrients"] == {"calories": 240.0} and r.json()["amount"] == 60
