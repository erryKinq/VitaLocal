#!/usr/bin/env python3
"""Fill an EMPTY VitaLocal server with four weeks of invented demo data (screenshots, trying it out).

Everything is made up: a synthetic Gadgetbridge export (heart rate, steps, sleep, stress, HRV, …)
goes through the normal import, the rest (food, drinks, weight, habits, supplements, training,
journal, lab results) through the API. Run it against a fresh instance only; it does not clean up.

    python3 tools/demo_data.py --url http://localhost:8000 --token "$VITALOCAL_API_TOKEN"

Python stdlib only. The same seed gives the same data (relative to today).
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DAYS = 28
NS = uuid.UUID("5a1d2c39-7d0e-4b8e-9a64-3f6c1b2e0d11")  # stable ids: re-running changes nothing


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


class Api:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, ctype: str | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "vitalocal-demo-data/1.0")
        if data is not None:
            req.add_header("Content-Type", ctype or "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out = r.read()
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path}: {e.code} {e.read().decode()[:400]}")
        return json.loads(out) if out else None

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def put(self, path, body):
        return self.call("PUT", path, body)

    def upload(self, path: str, name: str, content: bytes):
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.call("POST", path, raw=body, ctype=f"multipart/form-data; boundary={boundary}")


# --- watch data: a synthetic Gadgetbridge export ---------------------------------------------------


def _table(con, name, cols, key=None):
    pk = f', "{key}"' if key else ""
    keycol = f'"{key}" INTEGER, ' if key else ""
    con.execute(
        f'CREATE TABLE "{name}" ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL, '
        f'"USER_ID" INTEGER NOT NULL, {keycol}' + ", ".join(f'"{c}" REAL' for c in cols)
        + f', PRIMARY KEY ("TIMESTAMP", "DEVICE_ID"{pk}))'
    )


def nights(days: list[date], tz, rnd) -> dict[date, tuple[datetime, datetime]]:
    """Sleep that ends on each day: (bedtime, wake time)."""
    out = {}
    for d in days:
        weekend = d.weekday() >= 5
        bed = datetime.combine(d - timedelta(days=1), datetime.min.time(), tz) + timedelta(
            hours=22, minutes=rnd.randint(35, 75) + (40 if weekend else 0)
        )
        wake = datetime.combine(d, datetime.min.time(), tz) + timedelta(
            hours=6, minutes=rnd.randint(35, 60) + (70 if weekend else 0)
        )
        out[d] = (bed, wake)
    return out


def sleep_stages(bed: datetime, wake: datetime, rnd) -> list[tuple[datetime, int]]:
    """(end of stage, code) with FIT codes 1 awake, 2 light, 3 deep, 4 REM; cycles of ~90 min."""
    out, t, cycle = [], bed, 0
    while t < wake:
        plan = [(2, 20), (3, max(5, 35 - cycle * 8)), (2, 25), (4, 12 + cycle * 5)]
        if cycle and rnd.random() < 0.4:
            plan.insert(0, (1, rnd.randint(2, 6)))
        for code, minutes in plan:
            t = min(wake, t + timedelta(minutes=minutes + rnd.randint(-4, 4)))
            if out and out[-1][0] >= t:
                break
            out.append((t, code))
        cycle += 1
    out.append((wake + timedelta(minutes=3), 1))
    return out


def make_gadgetbridge_export(today: date, now: datetime, tz, workouts: dict, rnd) -> bytes:
    days = [today - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]
    sleep = nights(days + [today + timedelta(days=1)], tz, rnd)
    path = Path(tempfile.mkdtemp()) / "Gadgetbridge"
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 140")
    con.execute(
        'CREATE TABLE "DEVICE" ("_id" INTEGER PRIMARY KEY, "NAME" TEXT, "MANUFACTURER" TEXT, '
        '"IDENTIFIER" TEXT, "TYPE" INTEGER, "TYPE_NAME" TEXT, "MODEL" TEXT, "ALIAS" TEXT, '
        '"PARENT_FOLDER" TEXT)'
    )
    con.execute("INSERT INTO DEVICE VALUES (1, 'Venu 4', 'Garmin', '00:11:22:33:44:55', 0, 'GARMIN_VENU_4', '', NULL, NULL)")
    _table(con, "GARMIN_ACTIVITY_SAMPLE", ["RAW_INTENSITY", "STEPS", "RAW_KIND", "HEART_RATE", "DISTANCE_CM", "ACTIVE_CALORIES"])
    _table(con, "GARMIN_STRESS_SAMPLE", ["STRESS"])
    _table(con, "GARMIN_BODY_ENERGY_SAMPLE", ["ENERGY"])
    _table(con, "GARMIN_SPO2_SAMPLE", ["SPO2", "TYPE_NUM"])
    _table(con, "GARMIN_RESPIRATORY_RATE_SAMPLE", ["RESPIRATORY_RATE"])
    _table(con, "GARMIN_HEART_RATE_RESTING_SAMPLE", ["HEART_RATE"])
    _table(con, "GARMIN_HRV_VALUE_SAMPLE", ["VALUE"])
    _table(con, "GARMIN_HRV_SUMMARY_SAMPLE", [
        "WEEKLY_AVERAGE", "LAST_NIGHT_AVERAGE", "LAST_NIGHT5_MIN_HIGH", "BASELINE_LOW_UPPER",
        "BASELINE_BALANCED_LOWER", "BASELINE_BALANCED_UPPER", "STATUS_NUM"])  # fmt: skip
    _table(con, "GARMIN_SLEEP_STAGE_SAMPLE", ["STAGE"])
    _table(con, "GARMIN_SLEEP_STATS_SAMPLE", ["SLEEP_SCORE"])
    _table(con, "GARMIN_INTENSITY_MINUTES_SAMPLE", ["MODERATE", "VIGOROUS"])
    _table(con, "GARMIN_RESTING_METABOLIC_RATE_SAMPLE", ["RESTING_METABOLIC_RATE"])
    _table(con, "GENERIC_METRIC_SAMPLE", ["METRIC_SCORE", "METRIC_EXTRA"], key="METRIC_TYPE")
    con.execute(
        'CREATE TABLE "BASE_ACTIVITY_SUMMARY" ("_id" INTEGER PRIMARY KEY, "NAME" TEXT, '
        '"START_TIME" INTEGER NOT NULL, "END_TIME" INTEGER NOT NULL, "ACTIVITY_KIND" INTEGER NOT NULL, '
        '"BASE_LONGITUDE" INTEGER, "BASE_LATITUDE" INTEGER, "BASE_ALTITUDE" INTEGER, "GPX_TRACK" TEXT, '
        '"RAW_DETAILS_PATH" TEXT, "DEVICE_ID" INTEGER NOT NULL, "USER_ID" INTEGER NOT NULL, '
        '"SUMMARY_DATA" TEXT, "RAW_SUMMARY_DATA" BLOB, "HEADER_PHOTO" TEXT)'
    )

    ms = lambda t: int(t.timestamp() * 1000)  # noqa: E731
    act, stress, energy, resp = [], [], [], []
    hrv_base = 52.0
    for di, d in enumerate(days):
        bed_prev, wake = sleep[d]
        bed_next, _ = sleep[d + timedelta(days=1)]
        fit = 0.5 + 0.5 * math.sin(di / 4)  # good and worse stretches
        runs = [w for w in workouts.get(d, []) if w["kind"] == "run"]
        lifts = [w for w in workouts.get(d, []) if w["kind"] == "strength"]
        busy = [(w["start"], w["start"] + timedelta(minutes=w["minutes"]), w) for w in runs + lifts]
        steps = dist = kcal = 0
        start = datetime.combine(d, datetime.min.time(), tz)
        body = 30 + 8 * fit
        hr, walk_left = 52.0, 0
        for m in range(1440):
            t = start + timedelta(minutes=m)
            if t > now:
                break
            asleep = t < wake or t >= bed_next
            w = next((x for a, b, x in busy if a <= t < b), None)
            if asleep:
                hr += (50 - 3 * fit - hr) * 0.2 + rnd.uniform(-1.5, 1.5)
                ds = 0
            elif w:
                hr += ((148 if w["kind"] == "run" else 112) - hr) * 0.25 + rnd.uniform(-4, 4)
                ds = 160 if w["kind"] == "run" else rnd.randint(0, 8)
            else:
                if walk_left <= 0 and rnd.random() < (0.009 if 7 <= t.hour <= 20 else 0.001):
                    walk_left = rnd.randint(6, 25)  # walks come in bouts
                walk_left -= 1
                target = 95 if walk_left > 0 else 66
                hr += (target - hr) * 0.3 + rnd.uniform(-2, 2)
                ds = rnd.randint(85, 115) if walk_left > 0 else rnd.randint(0, 4)
            steps += ds
            dist += int(ds * 72)
            kcal += 0.02 * ds + (6 if w else 0.0) * (1 if ds or w else 0)
            act.append((int(t.timestamp()), 1, 1, 20 if ds else 2, steps, 1, round(hr), dist, round(kcal)))
            if m % 3 == 0:
                s = (rnd.randint(8, 20) if asleep else rnd.randint(18, 48) + (25 if w else 0)) - 6 * fit
                stress.append((ms(t), 1, 1, max(1, round(s))))
            if m % 15 == 0:
                body = min(100, body + 2.4) if asleep else max(5, body - (1.6 if w else 0.55))
                energy.append((ms(t), 1, 1, round(body)))
                resp.append((ms(t), 1, 1, round((13.5 if asleep else 16) + rnd.uniform(-1, 1), 1)))
        # the night that ended this morning
        night = sleep_stages(bed_prev, wake, rnd)
        con.executemany("INSERT INTO GARMIN_SLEEP_STAGE_SAMPLE VALUES (?,1,1,?)", [(ms(t), c) for t, c in night])
        score = round(62 + 25 * fit + rnd.randint(-4, 4))
        con.execute("INSERT INTO GARMIN_SLEEP_STATS_SAMPLE VALUES (?,1,1,?)", (ms(wake), score))
        hrv_night = hrv_base + 10 * fit + rnd.uniform(-4, 4)
        t = bed_prev + timedelta(minutes=30)
        while t < wake:
            con.execute("INSERT INTO GARMIN_HRV_VALUE_SAMPLE VALUES (?,1,1,?)", (ms(t), round(hrv_night + rnd.uniform(-9, 9))))
            if t.minute < 5:
                con.execute("INSERT INTO GARMIN_SPO2_SAMPLE VALUES (?,1,1,?,?)", (ms(t), rnd.randint(94, 98), 3))
            t += timedelta(minutes=5)
        con.execute(
            "INSERT INTO GARMIN_HRV_SUMMARY_SAMPLE VALUES (?,1,1,?,?,?,?,?,?,?)",
            (ms(wake), round(hrv_base + 5), round(hrv_night), round(hrv_night + 14), 40, 46, 64, 4 if hrv_night > 46 else 3),
        )
        con.execute("INSERT INTO GARMIN_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,?)", (ms(wake + timedelta(hours=2)), round(53 - 4 * fit)))
        con.execute("INSERT INTO GARMIN_RESTING_METABOLIC_RATE_SAMPLE VALUES (?,1,1,?)", (ms(wake), 1740))
        ready = round(45 + 40 * fit + rnd.randint(-5, 5))
        con.execute("INSERT INTO GENERIC_METRIC_SAMPLE VALUES (?,1,1,8,?,?)", (ms(wake + timedelta(minutes=20)), ready, min(5, 1 + ready // 20)))
        con.execute("INSERT INTO GENERIC_METRIC_SAMPLE VALUES (?,1,1,12,?,NULL)", (ms(wake + timedelta(hours=1)), 47 + di // 14))
        for w in runs + lifts:
            end = w["start"] + timedelta(minutes=w["minutes"])
            con.execute(
                "INSERT INTO GARMIN_INTENSITY_MINUTES_SAMPLE VALUES (?,1,1,?,?)",
                (ms(end), 5 if w["kind"] == "run" else 20, w["minutes"] - 5 if w["kind"] == "run" else 5),
            )
        for w in runs:
            end = w["start"] + timedelta(minutes=w["minutes"])
            summary = {
                "averageHR": {"unit": "bpm", "value": 146}, "maxHR": {"unit": "bpm", "value": 171},
                "distanceMeters": {"unit": "m", "value": w["km"] * 1000},
                "caloriesBurnt": {"unit": "kcal", "value": 62 * w["km"]},
            }  # fmt: skip
            con.execute(
                "INSERT INTO BASE_ACTIVITY_SUMMARY VALUES (NULL, 'Running', ?, ?, 16, NULL, NULL, NULL, NULL, NULL, 1, 1, ?, NULL, NULL)",
                (ms(w["start"]), ms(end), json.dumps(summary)),
            )
    con.executemany("INSERT INTO GARMIN_ACTIVITY_SAMPLE VALUES (?,?,?,?,?,?,?,?,?)", act)
    con.executemany("INSERT INTO GARMIN_STRESS_SAMPLE VALUES (?,?,?,?)", stress)
    con.executemany("INSERT INTO GARMIN_BODY_ENERGY_SAMPLE VALUES (?,?,?,?)", energy)
    con.executemany("INSERT INTO GARMIN_RESPIRATORY_RATE_SAMPLE VALUES (?,?,?,?)", resp)
    con.commit()
    con.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = (
            b"data export from Android application nodomain.freeyourgadget.gadgetbridge version 0.94.0 from "
            + now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ").encode()
        )
        z.write(path, "database/Gadgetbridge")
        z.writestr("preferences/global.json", "{}")
        z.writestr("gadgetbridge.json", "{}")
    return buf.getvalue()


# --- everything you log yourself ------------------------------------------------------------------

FOODS = {  # per 100 g / ml or per portion: kcal, protein, carbs, fat, sugars, fiber, salt
    "oats": ("Rolled oats", "g", 100, (372, 13.5, 58.7, 7.0, 0.7, 10.0, 0.0)),
    "skyr": ("Skyr", "g", 100, (63, 11.0, 4.0, 0.2, 4.0, 0.0, 0.1)),
    "berries": ("Mixed berries (frozen)", "g", 100, (48, 1.0, 8.5, 0.4, 7.0, 4.5, 0.0)),
    "banana": ("Banana", "portion", 1, (105, 1.3, 27.0, 0.4, 14.4, 3.1, 0.0)),
    "bread": ("Wholegrain bread", "g", 100, (220, 8.0, 38.0, 2.5, 3.0, 7.5, 1.1)),
    "cheese": ("Gouda", "g", 100, (356, 25.0, 0.0, 28.0, 0.0, 0.0, 1.8)),
    "chicken": ("Chicken breast", "g", 100, (110, 23.5, 0.0, 1.5, 0.0, 0.0, 0.2)),
    "rice": ("Basmati rice (cooked)", "g", 100, (130, 2.7, 28.0, 0.3, 0.1, 0.4, 0.0)),
    "broccoli": ("Broccoli", "g", 100, (34, 2.8, 4.0, 0.4, 1.7, 2.6, 0.0)),
    "lentils": ("Red lentil curry", "portion", 1, (480, 22.0, 62.0, 14.0, 9.0, 13.0, 1.9)),
    "salmon": ("Salmon fillet", "g", 100, (208, 20.0, 0.0, 13.0, 0.0, 0.0, 0.1)),
    "potatoes": ("Potatoes (boiled)", "g", 100, (77, 2.0, 17.0, 0.1, 0.8, 1.8, 0.0)),
    "pasta": ("Wholewheat pasta (cooked)", "g", 100, (150, 6.0, 28.0, 1.0, 1.0, 4.0, 0.0)),
    "tomato_sauce": ("Tomato sauce", "g", 100, (55, 1.5, 7.5, 2.0, 5.5, 1.5, 0.8)),
    "almonds": ("Almonds", "g", 100, (589, 24.0, 5.7, 52.0, 4.4, 12.5, 0.0)),
    "apple": ("Apple", "portion", 1, (80, 0.4, 19.0, 0.3, 15.0, 3.6, 0.0)),
    "dark_choc": ("Dark chocolate 70 %", "g", 100, (580, 9.0, 34.0, 42.0, 28.0, 11.0, 0.0)),
    "whey": ("Whey protein shake", "portion", 1, (120, 24.0, 2.5, 1.5, 2.0, 0.0, 0.2)),
}
KEYS = ("calories", "proteins", "carbohydrates", "fat", "sugars", "fiber", "salt")

MEALS = [  # (meal index, hour, [(food, amount, quantity)])
    [(0, 7.5, [("oats", 60, 1), ("skyr", 150, 1), ("berries", 100, 1)]),
     (0, 7.5, [("bread", 80, 1), ("cheese", 30, 1), ("apple", 1, 1)])],
    [(1, 12.75, [("chicken", 160, 1), ("rice", 220, 1), ("broccoli", 150, 1)]),
     (1, 12.75, [("lentils", 1, 1)]),
     (1, 12.75, [("pasta", 250, 1), ("tomato_sauce", 150, 1), ("cheese", 20, 1)])],
    [(2, 19.0, [("salmon", 150, 1), ("potatoes", 250, 1), ("broccoli", 150, 1)]),
     (2, 19.0, [("bread", 120, 1), ("cheese", 40, 1), ("skyr", 150, 1)]),
     (2, 19.0, [("chicken", 150, 1), ("pasta", 200, 1), ("tomato_sauce", 120, 1)])],
    [(3, 16.0, [("almonds", 30, 1), ("banana", 1, 1)]),
     (3, 16.0, [("dark_choc", 20, 1), ("apple", 1, 1)]),
     (3, 16.0, [("whey", 1, 1)])],
]  # fmt: skip

EXERCISES = {  # key: (name, garmin category, garmin name, increment, start kg)
    "bench": ("Bench press", "bench_press", "barbell_bench_press", 2.5, 60.0),
    "row": ("Barbell row", "row", "barbell_row", 2.5, 50.0),
    "ohp": ("Overhead press", "shoulder_press", "barbell_shoulder_press", 2.5, 35.0),
    "pulldown": ("Lat pulldown", "pull_up", "lat_pulldown", 2.5, 50.0),
    "squat": ("Back squat", "squat", "barbell_back_squat", 2.5, 70.0),
    "rdl": ("Romanian deadlift", "deadlift", "romanian_deadlift", 2.5, 60.0),
    "lunge": ("Dumbbell lunge", "lunge", "alternating_dumbbell_lunge", 2.0, 12.0),
    "plank": ("Plank", "plank", "plank", 2.5, 0.0),
}
PLAN = [
    ("Upper A", [("bench", 3, 6, 10), ("row", 3, 8, 12), ("ohp", 3, 8, 12), ("pulldown", 3, 10, 12)]),
    ("Lower A", [("squat", 3, 5, 8), ("rdl", 3, 8, 10), ("lunge", 2, 10, 12)]),
]

JOURNAL = [
    "Slept well, legs a bit heavy from yesterday. Easy walk at lunch, felt fresh in the evening.",
    "Busy day at work, skipped the afternoon snack. Squats felt strong today.",
    "Long run in the sun, kept the pace easy. Need to drink more on hot days.",
    "Tired after a late evening. Stretching helped, went to bed on time.",
    "Good focus in the morning. Tried the new lentil curry, will cook it again.",
]


def seed(api: Api, today: date, now: datetime, tz, rnd) -> dict:
    days = [today - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]
    at = lambda d, h: (datetime.combine(d, datetime.min.time(), tz) + timedelta(hours=h)).isoformat()  # noqa: E731
    past = lambda d, h: datetime.combine(d, datetime.min.time(), tz) + timedelta(hours=h) <= now  # noqa: E731

    api.put("/api/v1/profile", {"sex": "male", "birth_year": 1990, "height_cm": 180, "pal": 1.5,
                                "units": "metric", "lang": "en", "goal": "keep", "setup_done": True})  # fmt: skip
    api.put("/api/v1/modules", {k: True for k in
            ("nutrition", "drinks", "training", "habits", "journal", "health", "supplements", "watch")})  # fmt: skip
    first = days[0].isoformat()
    for metric, lo, hi in (("energy", 2200, 2700), ("protein", 130, None), ("carbohydrates", None, 320),
                           ("fat", None, 95), ("fiber", 30, None), ("sugars", None, 60)):  # fmt: skip
        api.put(f"/api/v1/goals/nutrition.{metric}", {"min_value": lo, "max_value": hi, "valid_from": first})

    for key, (name, unit, base, vals) in FOODS.items():
        api.post("/api/v1/foods", {"id": uid("food", key), "name": name, "base_amount": base, "base_unit": unit,
                                   "nutrients": dict(zip(KEYS, vals)), "favorite": key in ("skyr", "oats")})  # fmt: skip

    # workouts: strength Mon/Thu (rotating plan days), runs Tue/Sat
    workouts: dict[date, list[dict]] = {}
    for d in days:
        if d.weekday() in (0, 3):
            workouts.setdefault(d, []).append({"kind": "strength", "start": datetime.combine(d, datetime.min.time(), tz) + timedelta(hours=18), "minutes": 55})
        if d.weekday() in (1, 5):
            km = 8 if d.weekday() == 5 else 5
            workouts.setdefault(d, []).append({"kind": "run", "start": datetime.combine(d, datetime.min.time(), tz) + timedelta(hours=7 if d.weekday() == 1 else 9), "minutes": km * 6, "km": km})

    for i, d in enumerate(days):
        # food: one variant per meal, varied over the days
        for mi, variants in enumerate(MEALS):
            meal, hour, items = variants[(i + mi) % len(variants)]
            if not past(d, hour):
                continue
            for k, (food, amount, qty) in enumerate(items):
                api.post("/api/v1/nutrition/entries", {
                    "id": uid("entry", d, mi, k), "logged_at": at(d, hour), "meal_index": meal,
                    "food_id": uid("food", food), "amount": amount if FOODS[food][1] == "portion" else round(amount * 1.3),
                    "quantity": qty, "entered_via": "webui"})  # fmt: skip
        for k, (drink, ml, hour) in enumerate([("coffee", 250, 7.2), ("water", 500, 10), ("water", 500, 13.5),
                                               ("tea", 300, 15.5), ("water", 500, 18), ("water", 250, 20.5)]):  # fmt: skip
            if past(d, hour) and not (drink == "tea" and i % 3 == 0):
                api.post("/api/v1/drinks", {"id": uid("drink", d, k), "drink": drink, "ml": ml, "ts": at(d, hour), "source": "android"})
        if i % 2 == 0 and past(d, 7.1):
            api.post("/api/v1/body", {"id": uid("weight", d), "ts": at(d, 7.1), "metric": "weight",
                                      "value": round(79.4 - i * 0.045 + rnd.uniform(-0.3, 0.3), 1), "source": "webui"})  # fmt: skip
        if past(d, 21):
            for kind, v in (("mood", rnd.choice([3, 4, 4, 4, 5])), ("energy", rnd.choice([3, 3, 4, 4, 5]))):
                api.post("/api/v1/checkins", {"id": uid("checkin", d, kind), "kind": kind, "value": v, "ts": at(d, 21), "source": "android"})
            if i % 9 == 4:
                api.post("/api/v1/checkins", {"id": uid("checkin", d, "symptom"), "kind": "symptom", "label": "Headache",
                                              "value": 2, "ts": at(d, 15), "source": "android"})  # fmt: skip
            if i % 3 == 0 or d == today - timedelta(days=1):
                api.put(f"/api/v1/journal/{d}", {"text": JOURNAL[i % len(JOURNAL)], "source": "webui"})
    for p in (body for body in [{"metric": "body_fat", "value": 17.8}, {"metric": "body.waist", "value": 84}]):
        api.post("/api/v1/body", {"id": uid("body", p["metric"]), "ts": at(days[3], 7.2), "source": "webui", **p})

    # habits
    habits = [
        {"name": "Read", "icon": "📖", "kind": "count", "unit": "pages", "target": 20},
        {"name": "Stretch", "icon": "🤸", "kind": "check"},
        {"name": "Meditate", "icon": "🧘", "kind": "check", "per_week": 4},
        {"name": "10,000 steps", "icon": "👟", "metric": "steps", "min_value": 10000},
        {"name": "Protein 130 g", "icon": "🥚", "metric": "nutrition.protein", "min_value": 130},
        {"name": "In bed by 23:00", "icon": "🌙", "metric": "sleep.bedtime", "min_value": 1290, "max_value": 1380},
        {"name": "No caffeine after 14:00", "icon": "☕", "metric": "drinks.last_caffeine", "max_value": 840},
    ]
    for n, h in enumerate(habits):
        api.post("/api/v1/habits", {"id": uid("habit", h["name"]), "position": n, **h})
    for d in days:
        if d == today and not past(d, 21):
            continue
        if rnd.random() < 0.85:
            api.put(f"/api/v1/habits/{uid('habit', 'Read')}/log/{d}", {"value": rnd.choice([12, 20, 25, 30])})
        if rnd.random() < 0.75:
            api.put(f"/api/v1/habits/{uid('habit', 'Stretch')}/log/{d}", {"value": 1})
        if d.weekday() in (0, 2, 4, 6):
            api.put(f"/api/v1/habits/{uid('habit', 'Meditate')}/log/{d}", {"value": 1})

    # supplements
    catalog = {i["key"] for i in api.get("/api/v1/supplements/ingredients")}
    supps = [("Vitamin D3", [("vitamin_d", 1000)], 1, "morning"), ("Magnesium", [("magnesium", 150)], 2, "evening"),
             ("Omega-3", [("epa", 250), ("dha", 125)], 1, "with lunch")]  # fmt: skip
    for name, ingr, per_day, timing in supps:
        ingredients = [{"key": k, "amount": a} for k, a in ingr if k in catalog]
        api.post("/api/v1/supplements", {"id": uid("supp", name), "name": name, "per_day": per_day, "timing": timing,
                                         "ingredients": ingredients, "started_on": days[0].isoformat()})  # fmt: skip
        for d in days:
            if d < today and rnd.random() < 0.9:
                api.put(f"/api/v1/supplements/{uid('supp', name)}/log/{d}", {"count": per_day})

    # training: exercises, a two-day plan, workouts with a slow progression
    ex = {}
    for key, (name, cat, gname, inc, _) in EXERCISES.items():
        ex[key] = api.post("/api/v1/training/exercises", {"id": uid("ex", key), "name": name, "garmin_category": cat,
                                                          "garmin_name": gname, "increment_kg": inc})["id"]  # fmt: skip
    plan = api.post("/api/v1/training/plans", {
        "id": uid("plan"), "name": "Upper / Lower", "active": True, "days": [
            {"id": uid("day", name), "name": name, "exercises": [
                {"id": uid("pe", name, k), "exercise_id": ex[k], "sets": s, "reps_min": lo, "reps_max": hi,
                 "rest_s": 120, "target_reps": lo, "target_weight_kg": EXERCISES[k][4]}
                for k, s, lo, hi in entries]} for name, entries in PLAN]})  # fmt: skip
    del plan
    n = 0
    for d in days:
        for w in workouts.get(d, []):
            if w["kind"] != "strength" or w["start"] > now:
                continue
            name, entries = PLAN[n % 2]
            week = n // 2
            sets = []
            ratings = []
            for k, s, lo, hi in entries:
                kg = EXERCISES[k][4] + EXERCISES[k][3] * (week // 2)
                reps = min(hi, lo + week % 2 * 2)
                for j in range(s):
                    sets.append({"exercise_id": ex[k], "plan_exercise_id": uid("pe", name, k),
                                 "repetitions": reps - (1 if j == s - 1 and rnd.random() < 0.4 else 0), "weight_kg": kg})  # fmt: skip
                ratings.append({"exercise_id": ex[k], "plan_exercise_id": uid("pe", name, k),
                                "rating": rnd.choice(["good", "good", "easy", "hard"])})  # fmt: skip
            api.post("/api/v1/training/sessions", {
                "manual_key": uid("session", d), "start_ts": w["start"].isoformat(), "duration_min": w["minutes"],
                "title": name, "plan_day_id": uid("day", name), "sets": sets, "ratings": ratings})  # fmt: skip
            n += 1

    # one lab report, three weeks ago
    api.post("/api/v1/labs/reports", {
        "id": uid("labs"), "collected_on": (today - timedelta(days=21)).isoformat(), "lab": "Demo Lab", "values": [
            {"name": "25-OH-Vitamin D", "value": "32", "unit": "ng/ml", "reference": "30 - 100"},
            {"name": "Ferritin", "value": "64", "unit": "ng/ml", "reference": "30 - 400"},
            {"name": "LDL-Cholesterin", "value": "128", "unit": "mg/dl", "reference": "< 116"},
            {"name": "HDL-Cholesterin", "value": "58", "unit": "mg/dl", "reference": "> 40"},
            {"name": "HbA1c", "value": "5,2", "unit": "%", "reference": "4,0 - 5,6"},
            {"name": "TSH", "value": "1,8", "unit": "mU/l", "reference": "0,27 - 4,2"},
            {"name": "Magnesium", "value": "0,84", "unit": "mmol/l", "reference": "0,75 - 1,02"}]})  # fmt: skip
    return workouts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--timezone", default="Europe/Berlin")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    api, tz, rnd = Api(a.url, a.token), ZoneInfo(a.timezone), random.Random(a.seed)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    today = now.date()
    if api.get("/api/v1/foods?limit=1"):
        sys.exit("The server already has foods: demo data only goes into an empty instance.")
    print("logging food, drinks, habits, training, journal, labs …")
    workouts = seed(api, today, now, tz, rnd)
    print("importing the synthetic watch export …")
    batch = api.upload("/api/v1/ingest/upload", "Gadgetbridge.zip", make_gadgetbridge_export(today, now, tz, workouts, rnd))
    for _ in range(300):
        st = api.get(f"/api/v1/ingest/batches/{batch['batch_id']}")
        if st.get("status") not in ("queued", "processing", "pending", "running"):
            print("import:", st.get("status"))
            break
        time.sleep(2)
    print("done – open the WebUI")


if __name__ == "__main__":
    main()
