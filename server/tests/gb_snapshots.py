"""A Gadgetbridge database that changes like the real one, for snapshot sequences in tests.

Tables use Gadgetbridge's real layout: `WITHOUT ROWID` with `PRIMARY KEY (...) ON CONFLICT
REPLACE`, so writing a row with an existing key rewrites it in place, as greenDAO's
`insertOrReplace` does. `export()` copies the file like Gadgetbridge's database export and wraps
it in an export ZIP whose comment carries the export time. No real health data.
"""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

DEVICE_MAC = "AA:BB:CC:DD:EE:02"

_DDL = {
    "GARMIN_ACTIVITY_SAMPLE": '"RAW_INTENSITY" INTEGER, "STEPS" INTEGER, "RAW_KIND" INTEGER, '
    '"HEART_RATE" INTEGER, "DISTANCE_CM" INTEGER, "ACTIVE_CALORIES" INTEGER',
    "GARMIN_STRESS_SAMPLE": '"STRESS" INTEGER',
    "GARMIN_BODY_ENERGY_SAMPLE": '"ENERGY" INTEGER',
    "GARMIN_SLEEP_STAGE_SAMPLE": '"STAGE" INTEGER',
    "GARMIN_SLEEP_STATS_SAMPLE": '"SLEEP_SCORE" INTEGER',
    "GARMIN_NAP_SAMPLE": '"END_TIMESTAMP" INTEGER',
}
_KEYED = {
    "GARMIN_EVENT_SAMPLE": ("EVENT", '"EVENT" INTEGER NOT NULL, "EVENT_TYPE" INTEGER, "DATA" INTEGER'),
    "GENERIC_METRIC_SAMPLE": (
        "METRIC_TYPE",
        '"METRIC_TYPE" INTEGER NOT NULL, "METRIC_SCORE" REAL, "METRIC_EXTRA" INTEGER',
    ),
}


def ts_s(dt: datetime) -> int:
    return int(dt.timestamp())


def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp()) * 1000


class GbDatabase:
    def __init__(self, path: Path, user_version: int = 140):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.execute(f"PRAGMA user_version = {user_version}")
        self.con.execute(
            'CREATE TABLE "DEVICE" ("_id" INTEGER PRIMARY KEY, "NAME" TEXT, "MANUFACTURER" TEXT, '
            '"IDENTIFIER" TEXT, "TYPE" INTEGER, "TYPE_NAME" TEXT, "MODEL" TEXT, "ALIAS" TEXT, '
            '"PARENT_FOLDER" TEXT)'
        )
        self.con.execute(
            "INSERT INTO DEVICE VALUES (1, 'Venu 4', 'Garmin', ?, 0, 'GARMIN_VENU_4', '', NULL, NULL)",
            (DEVICE_MAC,),
        )
        for table, cols in _DDL.items():
            self.con.execute(
                f'CREATE TABLE "{table}" ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL, '
                f'"USER_ID" INTEGER NOT NULL, {cols}, '
                f'PRIMARY KEY ("TIMESTAMP", "DEVICE_ID") ON CONFLICT REPLACE) WITHOUT ROWID'
            )
        for table, (key, cols) in _KEYED.items():
            self.con.execute(
                f'CREATE TABLE "{table}" ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL, '
                f'"USER_ID" INTEGER NOT NULL, {cols}, '
                f'PRIMARY KEY ("TIMESTAMP", "DEVICE_ID", "{key}") ON CONFLICT REPLACE) WITHOUT ROWID'
            )
        self.con.execute(
            'CREATE TABLE "BASE_ACTIVITY_SUMMARY" ("_id" INTEGER PRIMARY KEY, "NAME" TEXT, '
            '"START_TIME" INTEGER NOT NULL, "END_TIME" INTEGER NOT NULL, '
            '"ACTIVITY_KIND" INTEGER NOT NULL, "BASE_LONGITUDE" INTEGER, "BASE_LATITUDE" INTEGER, '
            '"BASE_ALTITUDE" INTEGER, "GPX_TRACK" TEXT, "RAW_DETAILS_PATH" TEXT, '
            '"DEVICE_ID" INTEGER NOT NULL, "USER_ID" INTEGER NOT NULL, "SUMMARY_DATA" TEXT, '
            '"RAW_SUMMARY_DATA" BLOB, "HEADER_PHOTO" TEXT)'
        )
        self.con.commit()

    # --- writes (insertOrReplace semantics) ----------------------------------------------------

    def minute(self, at: datetime, *, hr: int, steps: int, dist_cm: int, kcal: int, intensity: int = 2):
        """One minute of the monitoring stream; steps/distance/calories are daily counters."""
        self.con.execute(
            "INSERT INTO GARMIN_ACTIVITY_SAMPLE VALUES (?,1,1,?,?,1,?,?,?)",
            (ts_s(at), intensity, steps, hr, dist_cm, kcal),
        )

    def value(self, table: str, at: datetime, value: int):
        self.con.execute(f'INSERT INTO "{table}" VALUES (?,1,1,?)', (ts_ms(at), value))

    def nap(self, start: datetime, end: datetime):
        self.con.execute("INSERT INTO GARMIN_NAP_SAMPLE VALUES (?,1,1,?)", (ts_ms(start), ts_ms(end)))

    def event(self, at: datetime, event: int, event_type: int, data: int):
        self.con.execute(
            "INSERT INTO GARMIN_EVENT_SAMPLE VALUES (?,1,1,?,?,?)", (ts_ms(at), event, event_type, data)
        )

    def metric(self, at: datetime, metric_type: int, score: float, extra: int | None = None):
        self.con.execute(
            "INSERT INTO GENERIC_METRIC_SAMPLE VALUES (?,1,1,?,?,?)", (ts_ms(at), metric_type, score, extra)
        )

    def activity(self, gb_id: int, start: datetime, end: datetime, name: str, summary: str):
        self.con.execute(
            "INSERT OR REPLACE INTO BASE_ACTIVITY_SUMMARY VALUES "
            "(?, ?, ?, ?, 32, NULL, NULL, NULL, NULL, NULL, 1, 1, ?, NULL, NULL)",
            (gb_id, name, ts_ms(start), ts_ms(end), summary),
        )

    def delete(self, table: str, at: datetime, unit: str = "ms"):
        ts = ts_ms(at) if unit == "ms" else ts_s(at)
        self.con.execute(f'DELETE FROM "{table}" WHERE TIMESTAMP = ?', (ts,))

    # --- export --------------------------------------------------------------------------------

    def export(self, zip_path: Path, exported_at: datetime) -> Path:
        """Database export (a plain file copy) inside an export ZIP with the export time."""
        self.con.commit()
        copy = zip_path.with_suffix(".sqlite")
        shutil.copyfile(self.path, copy)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            stamp = exported_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            z.comment = (
                "data export from Android application nodomain.freeyourgadget.gadgetbridge "
                f"version 0.94.0 from {stamp}"
            ).encode()
            z.write(copy, "database/Gadgetbridge")
        copy.unlink()
        return zip_path
