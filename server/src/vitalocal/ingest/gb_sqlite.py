"""Parser `gadgetbridge-sqlite`: Gadgetbridge export database -> normalized samples.

Pure functions only (no VitaLocal DB access), so the mapping can be tested with synthetic
SQLite fixtures and replayed against preserved raw artifacts when the parser version changes.

Rules:
- Missing measurements are never emitted. Gadgetbridge uses -1 (and 0 for heart rate) as
  "not measured" in minute samples; those values are counted as `missing`, not stored as 0.
- Timestamp units differ per table (GARMIN_ACTIVITY_SAMPLE / BATTERY_LEVEL: seconds, most
  others: milliseconds) and are declared explicitly, then checked for plausibility.
- Semantics that are not verified yet keep a raw `gb.` metric name (e.g. GENERIC_METRIC_SAMPLE
  type ids, event codes, raw intensity/kind).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo

PARSER_NAME = "gadgetbridge-sqlite"
PARSER_VERSION = "1.5.0"
# 1.1: steps/distance/energy are daily counters -> deltas
# 1.2: counter deltas are stamped 1 s before the reading (inside their interval), so the 00:00
#      reading's increase belongs to the day that just ended, not to the new one
# 1.3: GENERIC_METRIC_SAMPLE types verified against Gadgetbridge's MetricSample.Metric get names
# 1.4: also the 24/7 VO2max, race predictions and the daily ascent/descent totals
# 1.5: GENERIC_METRIC 9/10/11 dropped (copies of the training load and resting metabolic rate tables)

# SQLite `PRAGMA user_version` values this mapping was verified against.
KNOWN_SCHEMA_VERSIONS = frozenset({140})

_TS_MIN = datetime(2000, 1, 1, tzinfo=UTC)
_INTERVAL_END = timedelta(seconds=1)
_TS_MAX = datetime(2100, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Col:
    column: str
    metric: str  # may contain "{key}" when the table has a key_column
    unit: str | None = None
    scale: float = 1.0
    missing: frozenset[float] = frozenset()  # raw values meaning "not measured"
    end_timestamp: bool = False  # value is an end timestamp -> emit duration in seconds
    # Daily cumulative counter (resets at local midnight): emit the increase per sample instead.
    cumulative: bool = False


@dataclass(frozen=True)
class TableSpec:
    table: str
    ts_unit: Literal["s", "ms"]
    columns: tuple[Col, ...]
    key_column: str | None = None  # extra primary-key column substituted into metric names
    # verified keyed names -> (metric, unit), e.g. "gb.metric.8" -> Garmin's training readiness
    names: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    # keyed names that only repeat another table's values: not stored
    dropped: frozenset[str] = frozenset()


_NA = frozenset({-1})
_HR_NA = frozenset({-1, 0})

TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "GARMIN_ACTIVITY_SAMPLE",
        "s",
        (
            Col("HEART_RATE", "heart_rate", "bpm", missing=_HR_NA),
            Col("STEPS", "steps", "count", missing=_NA, cumulative=True),
            Col("DISTANCE_CM", "distance", "m", scale=0.01, missing=_NA, cumulative=True),
            Col("ACTIVE_CALORIES", "active_energy", "kcal", missing=_NA, cumulative=True),
            Col("RAW_INTENSITY", "gb.raw_intensity", None, missing=_NA),
            Col("RAW_KIND", "gb.raw_kind", None, missing=_NA),
        ),
    ),
    TableSpec("GARMIN_BODY_ENERGY_SAMPLE", "ms", (Col("ENERGY", "body_energy", "score"),)),
    TableSpec("GARMIN_STRESS_SAMPLE", "ms", (Col("STRESS", "stress", "score", missing=_NA),)),
    TableSpec(
        "GARMIN_SPO2_SAMPLE",
        "ms",
        (
            Col("SPO2", "spo2", "%", missing=frozenset({-1, 0})),
            Col("TYPE_NUM", "gb.spo2_type", None),
        ),
    ),
    TableSpec(
        "GARMIN_RESPIRATORY_RATE_SAMPLE",
        "ms",
        (Col("RESPIRATORY_RATE", "respiratory_rate", "brpm", missing=frozenset({-1, 0})),),
    ),
    TableSpec(
        "GARMIN_HEART_RATE_RESTING_SAMPLE",
        "ms",
        (Col("HEART_RATE", "resting_heart_rate", "bpm", missing=_HR_NA),),
    ),
    TableSpec("GARMIN_HRV_VALUE_SAMPLE", "ms", (Col("VALUE", "hrv_rmssd", "ms", missing=_HR_NA),)),
    TableSpec(
        "GARMIN_HRV_SUMMARY_SAMPLE",
        "ms",
        (
            Col("WEEKLY_AVERAGE", "daily.hrv_weekly_avg", "ms"),
            Col("LAST_NIGHT_AVERAGE", "daily.hrv_last_night_avg", "ms"),
            Col("LAST_NIGHT5_MIN_HIGH", "daily.hrv_last_night_5min_high", "ms"),
            Col("BASELINE_LOW_UPPER", "daily.hrv_baseline_low_upper", "ms"),
            Col("BASELINE_BALANCED_LOWER", "daily.hrv_baseline_balanced_lower", "ms"),
            Col("BASELINE_BALANCED_UPPER", "daily.hrv_baseline_balanced_upper", "ms"),
            Col("STATUS_NUM", "daily.hrv_status_code", None),
        ),
    ),
    # Garmin FIT sleep_level codes (0 unmeasurable, 1 awake, 2 light, 3 deep, 4 REM); verified
    # against the SLEEP FIT of 2026-09-27. Each row is stamped at the END of its stage (see
    # fit_parser: the sleep start comes from the SLEEP FIT as `sleep_in_bed`).
    TableSpec("GARMIN_SLEEP_STAGE_SAMPLE", "ms", (Col("STAGE", "sleep_stage_code", None),)),
    TableSpec("GARMIN_SLEEP_STATS_SAMPLE", "ms", (Col("SLEEP_SCORE", "daily.sleep_score", "score"),)),
    TableSpec(
        "GARMIN_SLEEP_RESTLESS_MOMENTS_SAMPLE", "ms", (Col("COUNT", "daily.sleep_restless_moments", "count"),)
    ),
    TableSpec("GARMIN_NAP_SAMPLE", "ms", (Col("END_TIMESTAMP", "nap_duration", "s", end_timestamp=True),)),
    TableSpec(
        "GARMIN_INTENSITY_MINUTES_SAMPLE",
        "ms",
        (
            Col("MODERATE", "intensity_minutes_moderate", "min"),
            Col("VIGOROUS", "intensity_minutes_vigorous", "min"),
        ),
    ),
    TableSpec(
        "GARMIN_RESTING_METABOLIC_RATE_SAMPLE",
        "ms",
        (Col("RESTING_METABOLIC_RATE", "resting_metabolic_rate", "kcal/day", missing=_HR_NA),),
    ),
    TableSpec(
        "GARMIN_EVENT_SAMPLE",
        "ms",
        (
            Col("DATA", "gb.event.{key}.data", None),
            Col("EVENT_TYPE", "gb.event.{key}.type", None),
        ),
        key_column="EVENT",
    ),
    TableSpec(
        "GENERIC_METRIC_SAMPLE",
        "ms",
        (
            Col("METRIC_SCORE", "gb.metric.{key}", None),
            Col("METRIC_EXTRA", "gb.metric.{key}.extra", None),
        ),
        key_column="METRIC_TYPE",
        # Gadgetbridge MetricSample.Metric ids (Gadgetbridge master, 2026-10)
        names={
            "gb.metric.8": ("garmin.training_readiness", "score"),
            "gb.metric.8.extra": ("garmin.training_readiness_level", None),  # 1 poor … 5 prime
            "gb.metric.6": ("vo2max_daily", "ml/kg/min"),  # estimated from 24/7 monitoring
            "gb.metric.12": ("vo2max", "ml/kg/min"),
            "gb.metric.19": ("race_time_5k", "s"),
            "gb.metric.20": ("race_time_10k", "s"),
            "gb.metric.21": ("race_time_half_marathon", "s"),
            "gb.metric.22": ("race_time_marathon", "s"),
            "gb.metric.23": ("recovery_time", "min"),  # remaining recovery as of the sample
            "gb.metric.24": ("daily.total_ascent", "m"),
            "gb.metric.25": ("daily.total_descent", "m"),
        },
        # training load acute / chronic and resting metabolic rate: the same values as
        # GENERIC_TRAINING_LOAD_*_SAMPLE and GARMIN_RESTING_METABOLIC_RATE_SAMPLE
        dropped=frozenset({"gb.metric.9", "gb.metric.10", "gb.metric.11"}),
    ),
    TableSpec("GENERIC_TRAINING_LOAD_ACUTE_SAMPLE", "ms", (Col("VALUE", "training_load_acute", None),)),
    TableSpec("GENERIC_TRAINING_LOAD_CHRONIC_SAMPLE", "ms", (Col("VALUE", "training_load_chronic", None),)),
    TableSpec(
        "BATTERY_LEVEL",
        "s",
        (Col("LEVEL", "device.battery_level.{key}", "%", missing=_NA),),
        key_column="BATTERY_INDEX",
    ),
)

# Tables that are handled elsewhere or intentionally not normalized (app/sync bookkeeping).
_NOT_SAMPLE_TABLES = frozenset(
    {
        "android_metadata",
        "sqlite_sequence",
        "DEVICE",
        "DEVICE_ATTRIBUTES",
        "USER",
        "USER_ATTRIBUTES",
        "BASE_ACTIVITY_SUMMARY",
        "CALENDAR_SYNC_STATE",
        "HEALTH_CONNECT_SLEEP_SESSION",
        "HEALTH_CONNECT_SYNC_STATE",
    }
)

_RELEVANT_EMPTY_PREFIXES = ("GARMIN_", "GENERIC_")


class Sample(NamedTuple):
    ts: datetime
    gb_device_id: int
    metric: str
    value: float
    unit: str | None


@dataclass
class GbDevice:
    gb_id: int
    source_key: str  # hashed device identifier; the raw MAC never leaves the raw store
    vendor: str | None
    model: str | None
    name: str | None


@dataclass
class GbActivity:
    gb_id: int
    gb_device_id: int
    start_ts: datetime
    end_ts: datetime | None
    name: str | None
    kind_raw: int | None
    summary: dict | None
    raw_details_name: str | None  # basename of the FIT file referenced by Gadgetbridge
    has_gpx: bool


@dataclass
class TableStats:
    rows_read: int = 0
    values: int = 0
    missing: Counter = field(default_factory=Counter)
    implausible_ts: int = 0
    counter: Counter = field(default_factory=Counter)  # cumulative-counter handling events
    error: str | None = None

    def as_dict(self) -> dict:
        d = {
            "rows_read": self.rows_read,
            "values": self.values,
            "missing": dict(self.missing),
            "implausible_ts": self.implausible_ts,
            **({"counter": dict(self.counter)} if self.counter else {}),
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class ParseReport:
    schema_version: int
    schema_known: bool
    tables: dict[str, TableStats] = field(default_factory=dict)
    empty_tables: list[str] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    unmapped_tables: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "schema_version": self.schema_version,
            "schema_known": self.schema_known,
            "tables": {k: v.as_dict() for k, v in self.tables.items()},
            "empty_tables": self.empty_tables,
            "missing_tables": self.missing_tables,
            "unmapped_tables": self.unmapped_tables,
            "warnings": self.warnings,
        }


def device_source_key(identifier: str) -> str:
    """Stable pseudonymous key for a Gadgetbridge device identifier (e.g. a MAC address)."""
    return hashlib.sha256(f"gadgetbridge:{identifier.upper()}".encode()).hexdigest()[:24]


def _to_dt(raw: int, unit: str) -> datetime:
    return datetime.fromtimestamp(raw / 1000 if unit == "ms" else raw, tz=UTC)


to_dt = _to_dt


def _plausible(ts: datetime) -> bool:
    return _TS_MIN <= ts < _TS_MAX


class GadgetbridgeDb:
    def __init__(self, path: Path, counter_timezone: str = "UTC"):
        # Garmin's daily counters reset at the device's local midnight.
        self._counter_tz = ZoneInfo(counter_timezone)
        # Read-only and immutable: the preserved original is opened in place and never touched.
        self._con = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        self._con.row_factory = sqlite3.Row
        self.schema_version: int = self._con.execute("PRAGMA user_version").fetchone()[0]
        self.report = ParseReport(self.schema_version, self.schema_version in KNOWN_SCHEMA_VERSIONS)
        if not self.report.schema_known:
            self.report.warnings.append(
                f"schema user_version {self.schema_version} not verified for {PARSER_NAME} "
                f"{PARSER_VERSION}; mapping applied by column name"
            )
        self._tables = {
            r[0]: self._columns(r[0])
            for r in self._con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> GadgetbridgeDb:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self._con.execute(f'PRAGMA table_info("{table}")')}

    def _count(self, table: str) -> int:
        return self._con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]

    def devices(self) -> list[GbDevice]:
        if "DEVICE" not in self._tables:
            return []
        return [
            GbDevice(
                gb_id=r["_id"],
                source_key=device_source_key(r["IDENTIFIER"] or f"gb-device-{r['_id']}"),
                vendor=r["MANUFACTURER"],
                model=r["TYPE_NAME"],
                name=r["NAME"],
            )
            for r in self._con.execute("SELECT * FROM DEVICE")
        ]

    def activities(self) -> list[GbActivity]:
        if "BASE_ACTIVITY_SUMMARY" not in self._tables:
            self.report.missing_tables.append("BASE_ACTIVITY_SUMMARY")
            return []
        out = []
        for r in self._con.execute("SELECT * FROM BASE_ACTIVITY_SUMMARY ORDER BY START_TIME"):
            try:
                summary = json.loads(r["SUMMARY_DATA"]) if r["SUMMARY_DATA"] else None
            except json.JSONDecodeError:
                summary = None
                self.report.warnings.append(f"activity {r['_id']}: SUMMARY_DATA is not JSON")
            path = r["RAW_DETAILS_PATH"]
            out.append(
                GbActivity(
                    gb_id=r["_id"],
                    gb_device_id=r["DEVICE_ID"],
                    start_ts=_to_dt(r["START_TIME"], "ms"),
                    end_ts=_to_dt(r["END_TIME"], "ms") if r["END_TIME"] else None,
                    name=r["NAME"],
                    kind_raw=r["ACTIVITY_KIND"],
                    summary=summary,
                    raw_details_name=PurePosixPath(path).name if path else None,
                    has_gpx=bool(r["GPX_TRACK"]),
                )
            )
        return out

    def table_bounds(self) -> dict[str, tuple[int, int]]:
        """(min, max) TIMESTAMP in raw units of every mapped table that has rows."""
        out = {}
        for spec in TABLE_SPECS:
            if spec.table in self._tables:
                q = f'SELECT min(TIMESTAMP), max(TIMESTAMP) FROM "{spec.table}"'
                lo, hi = self._con.execute(q).fetchone()
                if lo is not None:
                    out[spec.table] = (lo, hi)
        return out

    def samples(self, windows: dict[str, int] | None = None) -> Iterator[Sample]:
        """Yield the mapped samples; statistics accumulate in `self.report`.

        `windows` (table -> lowest TIMESTAMP in raw units) reads only rows from there on, as the
        full read would emit them: daily counters start from the last valid reading before the
        window. Without `windows` every row is read.
        """
        for spec in TABLE_SPECS:
            cols = self._tables.get(spec.table)
            if cols is None:
                self.report.missing_tables.append(spec.table)
                continue
            if self._count(spec.table) == 0:
                self.report.empty_tables.append(spec.table)
                continue
            yield from self._table_samples(spec, cols, None if windows is None else windows.get(spec.table))

        mapped = {s.table for s in TABLE_SPECS}
        for table in sorted(self._tables):
            if table in mapped or table in _NOT_SAMPLE_TABLES:
                continue
            n = self._count(table)
            if n:
                self.report.unmapped_tables[table] = n
            elif table.startswith(_RELEVANT_EMPTY_PREFIXES):
                # Gadgetbridge ships tables for every vendor; only report ones relevant to Garmin.
                self.report.empty_tables.append(table)

    def _needed(self, spec: TableSpec) -> set[str]:
        needed = {"TIMESTAMP", "DEVICE_ID", *(c.column for c in spec.columns)}
        if spec.key_column:
            needed.add(spec.key_column)
        return needed

    def _table_samples(self, spec: TableSpec, cols: set[str], since: int | None = None) -> Iterator[Sample]:
        stats = self.report.tables[spec.table] = TableStats()
        needed = self._needed(spec)
        if missing_cols := needed - cols:
            stats.error = f"missing columns: {sorted(missing_cols)}"
            return

        lo, hi = self._con.execute(f'SELECT min(TIMESTAMP), max(TIMESTAMP) FROM "{spec.table}"').fetchone()
        if not (_plausible(_to_dt(lo, spec.ts_unit)) and _plausible(_to_dt(hi, spec.ts_unit))):
            stats.error = f"timestamps implausible for declared unit '{spec.ts_unit}'"
            return

        counters: dict = {}
        select = ", ".join(f'"{c}"' for c in sorted(needed))
        query, params = f'SELECT {select} FROM "{spec.table}" ORDER BY TIMESTAMP', ()
        if since is not None:
            self._seed_counters(spec, since, counters)
            query = f'SELECT {select} FROM "{spec.table}" WHERE TIMESTAMP >= ? ORDER BY TIMESTAMP'
            params = (since,)
        for row in self._con.execute(query, params):
            stats.rows_read += 1
            ts = _to_dt(row["TIMESTAMP"], spec.ts_unit)
            if not _plausible(ts):
                stats.implausible_ts += 1
                continue
            key = row[spec.key_column] if spec.key_column else None
            for col in spec.columns:
                metric, unit = col.metric.format(key=key) if key is not None else col.metric, col.unit
                if metric.split(".extra")[0] in spec.dropped:
                    continue
                metric, unit = spec.names.get(metric, (metric, unit))
                raw = row[col.column]
                if raw is None or raw in col.missing:
                    stats.missing[metric] += 1
                    continue
                if col.cumulative:
                    raw = self._counter_delta(counters, (row["DEVICE_ID"], col.column), ts, raw, stats)
                    if raw is None:
                        continue
                value = float(raw) * col.scale
                if col.end_timestamp:
                    value = (_to_dt(raw, spec.ts_unit) - ts).total_seconds()
                stats.values += 1
                sample_ts = ts - _INTERVAL_END if col.cumulative else ts
                yield Sample(sample_ts, row["DEVICE_ID"], metric, value, unit)

    def _seed_counters(self, spec: TableSpec, since: int, counters: dict) -> None:
        """Baseline of each daily counter: the last valid reading before `since`, as the full
        read would have it when it reaches the window (implausible times and sentinels skipped)."""
        cumulative = [c for c in spec.columns if c.cumulative]
        if not cumulative:
            return
        devices = [
            r[0]
            for r in self._con.execute(
                f'SELECT DISTINCT DEVICE_ID FROM "{spec.table}" WHERE TIMESTAMP >= ?', (since,)
            )
        ]
        scale = 1000 if spec.ts_unit == "ms" else 1
        ts_min, ts_max = int(_TS_MIN.timestamp()) * scale, int(_TS_MAX.timestamp()) * scale
        for device in devices:
            for col in cumulative:
                missing = sorted(col.missing)
                excluded = f' AND "{col.column}" NOT IN ({", ".join("?" * len(missing))})' if missing else ""
                row = self._con.execute(
                    f'SELECT TIMESTAMP, "{col.column}" FROM "{spec.table}" '
                    f"WHERE DEVICE_ID = ? AND TIMESTAMP < ? AND TIMESTAMP >= ? AND TIMESTAMP < ? "
                    f'AND "{col.column}" IS NOT NULL{excluded} ORDER BY TIMESTAMP DESC LIMIT 1',
                    (device, since, ts_min, ts_max, *missing),
                ).fetchone()
                if row is not None:
                    counters[(device, col.column)] = (_to_dt(row[0], spec.ts_unit), row[1])

    def window_metrics(self, windows: dict[str, int]) -> list[tuple[int, str, datetime]]:
        """(Gadgetbridge device id, metric, lowest sample time) of everything the windowed read
        covers: a stored value there that the snapshot no longer produces is superseded."""
        out = []
        for spec in TABLE_SPECS:
            since = windows.get(spec.table)
            cols = self._tables.get(spec.table)
            if since is None or cols is None or self._needed(spec) - cols:
                continue
            devices = [
                r[0]
                for r in self._con.execute(
                    f'SELECT DISTINCT DEVICE_ID FROM "{spec.table}" WHERE TIMESTAMP >= ?', (since,)
                )
            ]
            keys = (
                [r[0] for r in self._con.execute(f'SELECT DISTINCT "{spec.key_column}" FROM "{spec.table}"')]
                if spec.key_column
                else [None]
            )
            lo = _to_dt(since, spec.ts_unit)
            for col in spec.columns:
                for key in keys:
                    metric = col.metric.format(key=key) if key is not None else col.metric
                    if metric.split(".extra")[0] in spec.dropped:
                        continue
                    metric = spec.names.get(metric, (metric, None))[0]
                    for device in devices:
                        out.append((device, metric, lo - _INTERVAL_END if col.cumulative else lo))
        return out

    def _counter_day(self, ts: datetime):
        # Samples are stamped at the end of their interval: 00:00 still belongs to the day before.
        return (ts - timedelta(seconds=1)).astimezone(self._counter_tz).date()

    def _counter_delta(self, counters: dict, key: tuple, ts: datetime, raw: int, stats: TableStats):
        """Increase of a daily counter since the previous valid reading (None if unknowable)."""
        prev = counters.get(key)
        counters[key] = (ts, raw)
        if prev is None:
            stats.counter["no_baseline"] += 1  # first reading: the day's earlier count is unknown
            return None
        prev_ts, prev_raw = prev
        if self._counter_day(ts) != self._counter_day(prev_ts):
            stats.counter["daily_reset"] += 1
            return raw
        if raw < prev_raw:
            stats.counter["unexpected_reset"] += 1  # e.g. device restart within a day
            return raw
        return raw - prev_raw
