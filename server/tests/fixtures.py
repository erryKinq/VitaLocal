"""Synthetic Gadgetbridge export artifacts. No real health data lives in the repository."""

from __future__ import annotations

import io
import sqlite3
import struct
import zipfile
from pathlib import Path

DEVICE_MAC = "AA:BB:CC:DD:EE:01"
T0_S = 1_790_000_000  # 2026-09-21T14:13:20Z
T0_MS = T0_S * 1000

_SAMPLE_TABLE = (
    'CREATE TABLE "{name}" ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL, '
    '"USER_ID" INTEGER NOT NULL, {cols}, PRIMARY KEY ("TIMESTAMP", "DEVICE_ID"{pk}))'
)


def _table(con, name, cols, pk_extra=""):
    con.execute(
        _SAMPLE_TABLE.format(
            name=name,
            cols=", ".join(f'"{c}" INTEGER' for c in cols),
            pk=f', "{pk_extra}"' if pk_extra else "",
        )
    )


def make_gb_database(
    path: Path,
    *,
    user_version: int = 140,
    activity_fit: str | None = None,
    summary_json: str | None = None,
) -> Path:
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {user_version}")
    con.execute(
        'CREATE TABLE "DEVICE" ("_id" INTEGER PRIMARY KEY, "NAME" TEXT, "MANUFACTURER" TEXT, '
        '"IDENTIFIER" TEXT, "TYPE" INTEGER, "TYPE_NAME" TEXT, "MODEL" TEXT, "ALIAS" TEXT, '
        '"PARENT_FOLDER" TEXT)'
    )
    con.execute(
        "INSERT INTO DEVICE VALUES (1, 'Venu 4', 'Garmin', ?, 0, 'GARMIN_VENU_4', '', NULL, NULL)",
        (DEVICE_MAC,),
    )

    _table(
        con,
        "GARMIN_ACTIVITY_SAMPLE",
        ["RAW_INTENSITY", "STEPS", "RAW_KIND", "HEART_RATE", "DISTANCE_CM", "ACTIVE_CALORIES"],
    )
    # STEPS / DISTANCE_CM / ACTIVE_CALORIES are daily cumulative counters in Gadgetbridge
    con.executemany(
        "INSERT INTO GARMIN_ACTIVITY_SAMPLE VALUES (?,1,1,?,?,?,?,?,?)",
        [
            (T0_S, 3, 100, 1, 70, 5000, 10),  # first reading: counter baseline only
            (T0_S + 60, -1, -1, 1, 0, -1, -1),  # nothing measured: HR 0 and -1 sentinels
            (T0_S + 120, 2, 112, 1, 65, 5900, 12),  # +12 steps, +9 m, +2 kcal
            (T0_S + 180, 2, 112, 1, 66, 5900, 12),  # standing still: 0 steps is a real value
        ],
    )
    _table(con, "GARMIN_STRESS_SAMPLE", ["STRESS"])
    con.executemany("INSERT INTO GARMIN_STRESS_SAMPLE VALUES (?,1,1,?)", [(T0_MS, 25), (T0_MS + 180_000, -1)])
    _table(con, "GARMIN_NAP_SAMPLE", ["END_TIMESTAMP"])
    con.execute("INSERT INTO GARMIN_NAP_SAMPLE VALUES (?,1,1,?)", (T0_MS, T0_MS + 1_200_000))
    con.execute(
        'CREATE TABLE "GENERIC_METRIC_SAMPLE" ("TIMESTAMP" INTEGER NOT NULL, '
        '"DEVICE_ID" INTEGER NOT NULL, "USER_ID" INTEGER NOT NULL, "METRIC_TYPE" INTEGER, '
        '"METRIC_SCORE" REAL, "METRIC_EXTRA" INTEGER, '
        'PRIMARY KEY ("TIMESTAMP", "DEVICE_ID", "METRIC_TYPE"))'
    )
    con.executemany(
        "INSERT INTO GENERIC_METRIC_SAMPLE VALUES (?,1,1,?,?,?)",
        [(T0_MS, 8, 41.0, 2), (T0_MS, 11, 1800.0, None)],
    )
    _table(con, "GENERIC_WEIGHT_SAMPLE", ["WEIGHT_GRAMS"])  # empty on purpose
    _table(con, "SOME_FUTURE_SAMPLE", ["VALUE"])
    con.execute("INSERT INTO SOME_FUTURE_SAMPLE VALUES (?,1,1,5)", (T0_MS,))

    con.execute(
        'CREATE TABLE "BASE_ACTIVITY_SUMMARY" ("_id" INTEGER PRIMARY KEY, "NAME" TEXT, '
        '"START_TIME" INTEGER NOT NULL, "END_TIME" INTEGER NOT NULL, '
        '"ACTIVITY_KIND" INTEGER NOT NULL, "BASE_LONGITUDE" INTEGER, "BASE_LATITUDE" INTEGER, '
        '"BASE_ALTITUDE" INTEGER, "GPX_TRACK" TEXT, "RAW_DETAILS_PATH" TEXT, '
        '"DEVICE_ID" INTEGER NOT NULL, "USER_ID" INTEGER NOT NULL, "SUMMARY_DATA" TEXT, '
        '"RAW_SUMMARY_DATA" BLOB, "HEADER_PHOTO" TEXT)'
    )
    if activity_fit:
        con.execute(
            "INSERT INTO BASE_ACTIVITY_SUMMARY VALUES (1, 'Strength', ?, ?, 32, NULL, NULL, NULL, "
            "NULL, ?, 1, 1, ?, NULL, NULL)",
            (
                T0_MS,
                T0_MS + 1_800_000,
                f"/storage/emulated/0/Android/data/x/files/{DEVICE_MAC}/ACTIVITY/2026/{activity_fit}",
                summary_json or '{"averageHR": {"unit": "bpm", "value": 90}}',
            ),
        )
    con.commit()
    con.close()
    return path


# --- minimal FIT writer (file_id + optional session) --------------------------------------------

_CRC_TABLE = [
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
]


def fit_crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


FIT_EPOCH_OFFSET = 631_065_600


def _fit_ts(unix_s: int) -> int:
    return unix_s - FIT_EPOCH_OFFSET


class FitBuilder:
    """Tiny FIT writer: each message is (global_num, [(field_num, base_type, fmt, value), ...])."""

    _SIZES = {"B": 1, "H": 2, "I": 4, "i": 4, "f": 4}

    def __init__(self):
        self._body = io.BytesIO()
        self._local = 0

    def message(self, global_num: int, fields: list[tuple[int, int, str, object]]) -> FitBuilder:
        local = self._local % 16
        self._local += 1
        defs = b"".join(
            bytes([num, self._SIZES[fmt[-1]] * (len(fmt) if len(fmt) > 1 else 1), base])
            for num, base, fmt, _ in fields
        )
        self._body.write(struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields)) + defs)
        data = b"".join(
            struct.pack("<" + fmt, *(v if isinstance(v, tuple) else (v,))) for _, _, fmt, v in fields
        )
        self._body.write(bytes([local]) + data)
        return self

    def build(self, *, corrupt: bool = False) -> bytes:
        data = self._body.getvalue()
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(data), b".FIT")
        header += struct.pack("<H", fit_crc(header))
        crc = fit_crc(header + data)
        if corrupt:
            crc ^= 0xFFFF
        return header + data + struct.pack("<H", crc)


U8, U16, U32, F32 = 0x02, 0x84, 0x86, 0x88
ENUM = 0x00
NA16 = 0xFFFF


def fit_file_id(b: FitBuilder, file_type: int) -> FitBuilder:
    return b.message(0, [(0, ENUM, "B", file_type), (4, U32, "I", _fit_ts(T0_S))])


def make_fit(
    file_type: int = 4,
    *,
    sport: int | None = None,
    sub_sport: int | None = None,
    sets: list[dict] | None = None,
    corrupt: bool = False,
    start_s: int = T0_S,
) -> bytes:
    b = fit_file_id(FitBuilder(), file_type)
    for i, st in enumerate(sets or []):
        active = st["type"] == "active"
        cats = tuple(st.get("category", ())) + (NA16,) * (3 - len(st.get("category", ())))
        b.message(
            225,
            [
                (254, U32, "I", _fit_ts(T0_S)),
                (0, U32, "I", int(st["duration"] * 1000)),
                (3, U16, "H", st.get("reps", NA16) if active else NA16),
                (4, U16, "H", int(st.get("weight", 0) * 16) if active and "weight" in st else NA16),
                (5, U8, "B", 1 if active else 0),
                (6, U32, "I", _fit_ts(T0_S + st.get("offset", 0))),
                (7, U16, "HHH", cats),
                (10, U16, "H", i),
            ],
        )
    if sport is not None:
        b.message(
            18,
            [
                (2, U32, "I", _fit_ts(start_s)),
                (5, ENUM, "B", sport),
                (6, ENUM, "B", sub_sport or 0),
                (7, U32, "I", 1_800_000),
                (16, U8, "B", 95),
            ],
        )
    return b.build(corrupt=corrupt)


def make_skin_temp_fit(nightly: float = 36.2, deviation: float = -0.1) -> bytes:
    b = fit_file_id(FitBuilder(), 73)
    b.message(398, [(253, U32, "I", _fit_ts(T0_S)), (1, F32, "f", deviation), (4, F32, "f", nightly)])
    return b.build()


STRENGTH_SETS = [  # mirrors the real pattern: warm-up, unclassified set, rests, bench press
    {"type": "active", "duration": 600.4, "reps": 0, "weight": 0.0, "category": (2, 65534, 65534)},
    {"type": "rest", "duration": 6.2, "offset": 600},
    {"type": "active", "duration": 5.9, "reps": 9, "weight": 23.0, "offset": 607},
    {"type": "rest", "duration": 122.5, "offset": 613},
    {"type": "active", "duration": 53.5, "reps": 9, "weight": 23.0, "category": (0, 24, 30), "offset": 736},
]


def gb_strength_summary(sets=STRENGTH_SETS) -> str:
    import json

    rows = {"sets_header": {"type": "tableRow", "isHeader": True, "columns": []}}
    active = [s for s in sets if s["type"] == "active"]
    for i, st in enumerate(active, 1):
        rows[f"set_{i}"] = {
            "type": "tableRow",
            "isHeader": False,
            "columns": [
                {"value": i},
                {"value": "label"},
                {"unit": "string", "value": str(st["reps"])},
                {"unit": "unit_kg", "value": st["weight"]},
                {"unit": "seconds", "value": int(st["duration"])},
            ],
        }
    return json.dumps(rows)


def make_export_zip(path: Path, tmp: Path) -> Path:
    """Export with one linked strength ACTIVITY FIT, one unlinked FIT and a nested backup ZIP."""
    linked, orphan = "ACTIVITY_2026-09-21_12-53-20.fit", "ACTIVITY_2026-09-20_08-00-00.fit"
    db = make_gb_database(tmp / "Gadgetbridge", activity_fit=linked, summary_json=gb_strength_summary())
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as nz:
        nz.writestr("settings.fit", make_fit(file_type=2))
    base = f"files/{DEVICE_MAC}"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = (
            b"data export from Android application nodomain.freeyourgadget.gadgetbridge "
            b"version 0.94.0 from 2026-09-27T22:12:25Z"
        )
        z.write(db, "database/Gadgetbridge")
        z.writestr("preferences/global.json", "{}")
        z.writestr("gadgetbridge.json", "{}")
        z.writestr(f"{base}/ACTIVITY/2026/{linked}", make_fit(sport=10, sub_sport=20, sets=STRENGTH_SETS))
        z.writestr(f"{base}/SKIN_TEMP/2026/SKIN_TEMP_x.fit", make_skin_temp_fit())
        z.writestr(f"{base}/ACTIVITY/2026/{orphan}", make_fit(sport=10, sub_sport=44, start_s=T0_S - 86_400))
        z.writestr(f"{base}/KPI/KPI_broken.fit", make_fit(file_type=44, corrupt=True))
        z.writestr(f"{base}/BACKUP_PRIMARY/BACKUP_PRIMARY_x.zip", nested.getvalue())
    return path


def waistline_export(credentials: bool = True) -> dict:
    """Minimal Waistline export (schema 9) with foods, a recipe, weights and settings."""
    return {
        "version": 34,
        "foodList": [
            {
                "id": 1,
                "name": "Skyr",
                "brand": "B",
                "portion": "100",
                "unit": "g",
                "nutrition": {"calories": 60, "proteins": 11, "carbohydrates": 4, "fat": 0.2, "fiber": 0},
            },
            {
                "id": 2,
                "name": "Apple",
                "brand": "",
                "portion": "1",
                "unit": "portion",
                "nutrition": {"calories": 80, "carbohydrates": 19, "fat": 0.3, "proteins": 0.4},
            },
        ],
        "recipes": [
            {
                "id": 7,
                "name": "Bolognese",
                "portion": "4",
                "unit": "Portionen",
                "nutrition": {
                    "calories": 2000,
                    "proteins": 160,
                    "carbohydrates": 170,
                    "fat": 80,
                    "fiber": 30,
                },
            },
        ],
        "meals": [],
        "diary": [
            {
                "id": 1,
                "dateTime": "2026-09-21T00:00:00.000Z",
                "stats": {"weight": 64.6},
                "items": [
                    {
                        "id": 1,
                        "type": "food",
                        "portion": "250",
                        "quantity": "1",
                        "category": "0",
                        "dateTime": "2026-09-21T05:00:00.000Z",
                    },
                    {
                        "id": 2,
                        "type": "food",
                        "portion": "1",
                        "quantity": "2",
                        "category": 3,
                        "dateTime": "2026-09-21T13:00:00.000Z",
                    },
                    {
                        "id": 7,
                        "type": "recipe",
                        "portion": "1",
                        "quantity": "1",
                        "category": 2,
                        "dateTime": "2026-09-21T18:00:00.000Z",
                    },
                ],
            },
            {"id": 2, "dateTime": "2026-09-22T00:00:00.000Z", "stats": {"weight": "64,4"}, "items": []},
        ],
        "settings": {
            "schemaVersion": 9,
            "units": {"energy": "kcal", "weight": "kg"},
            "diary": {"meal-names": ["", "Mittagessen", "Abendessen", "Snacks"]},
            "integration": {"off-password": "secret-pw" if credentials else ""},
            # as in the real export: one shared value per nutrient; minimum-goal = lower bound
            "goals": {
                "first-day-of-week": "1",
                "calories": {
                    "goal-list": [
                        {"shared-goal": True, "goal": ["2000", "", "", "", "", "", ""], "minimum-goal": True}
                    ]
                },
                "kilojoules": {
                    "goal-list": [{"shared-goal": True, "goal": ["8400", "", "", "", "", "", ""]}]
                },
                "proteins": {
                    "goal-list": [
                        {
                            "shared-goal": True,
                            "goal": ["128", "", "", "", "", "", ""],
                            "minimum-goal": True,
                            "percent-goal": False,
                        }
                    ]
                },
                "salt": {"goal-list": [{"shared-goal": True, "goal": ["6", "", "", "", "", "", ""]}]},
                "fat": {"goal-list": [{"shared-goal": False, "goal": ["60", "70", "", "", "", "", ""]}]},
                "weight": {"goal-list": [{"goal": ["75", "", "", "", "", "", ""]}]},
            },
        },
    }
