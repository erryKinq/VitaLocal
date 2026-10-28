"""Parser `garmin-fit`: ACTIVITY sessions/sets/records, SKIN_TEMP, SLEEP assessments, MONITOR ascent
and altitude, METRICS (readiness factors, sleep need, sleep body battery, load focus), SLP_DISR.

Pure functions only. Only message types with verified semantics are mapped; everything else
stays in the raw store (and in the FIT catalog message counts) for later parser versions.

Verified against the Venu 4 export (2026-09):
- Gadgetbridge's set table lists only `set_type == active` sets, in message order, and names the
  exercise after the first entry of the `category` array. Rest sets are real FIT messages too.
- `category` holds up to three candidates from the watch's exercise recognition; 65534 = unknown.
- `skin_temp_overnight` carries one nightly value plus deviations from the personal baseline.
- ACTIVITY `record` messages carry per-second heart rate during a workout (the monitoring stream
  often has gaps then), stored as `workout_heart_rate` so both streams stay distinguishable.
- SLEEP files: `sleep_assessment` holds Garmin's sub-scores; its night ends at the `event` 74
  stop timestamp, which equals the sleep end used by Gadgetbridge's sleep score. The `event` 74
  start is the sleep start (Gadgetbridge shows it as the night's begin). `sleep_level` messages
  (same as Gadgetbridge's GARMIN_SLEEP_STAGE_SAMPLE) are stamped at the END of their stage: the
  first stage runs from the sleep start to the first message. Checked against Gadgetbridge's
  sleep view for the night to 2026-09-28 (stage totals match to the minute).
- SKIN_TEMP files: the undocumented message 397 carries a skin temperature curve over the 24 hours
  before the file, every 3 minutes (field 1, °C; field 253 = FIT timestamp). It is relative to an
  undocumented reference (about −1 °C at night, −5 to −8 °C during the day for a nightly value of
  36 °C), so it is stored as `skin_temp_relative`: the course is meaningful, the level is not.
- `time_in_zone` with `reference_mesg == session` holds seconds per heart rate zone (below zone 1
  first) and the zone limits; per-set entries are not kept.
- MONITOR files: a `monitoring` message with `ascent`/`descent` is one climbed / descended stretch
  (about 3 m, Garmin counts 3.048 m as a floor), stamped by `timestamp_16` relative to the last
  full timestamp. Message 279 (MONITORING_ALTITUDE) is the altitude every 2 minutes, raw / 5 − 500
  m (126 m at home, the same as ACCLIMATIZATION's altitude). Barometric: in a plane it shows the
  cabin pressure altitude (about 1900 m).
- Undocumented messages are named after Gadgetbridge's fit_profile.json (FitCodeGenerator) and read
  by field number (fitdecode only knows the public profile). Checked against the stored values:
  - 369 TRAINING_READINESS (METRICS): field 4 = sleep score, 21 / 128 = HRV 7-day average and
    22 = acute load equal the stored values. Fields 5, 8, 10, 12, 14, 16 are Garmin's six factors
    in %, in the watch's order (sleep score, recovery time, acute load, HRV status, sleep history,
    stress history): 5 follows the sleep score, 8 falls as the recovery time rises. The field after
    each is its level; level 0 (always with value 0) means the factor is not available yet (no
    readiness in the first days, acute load and sleep history while their history builds up, the
    sleep factor before the night): such factors are skipped.
  - 410 SLEEP_DEMAND (METRICS): field 1 = tonight's sleep need in minutes (equals USER_METRICS
    sleep_need; 480 → 440 since August).
  - 384 DAILY_SLEEP (METRICS): fields 14 / 16 = body battery at sleep start / end (field 9 / 11),
    equal to the body energy curve at those times on three nights.
  - 294 TRAINING_BALANCE_RESULTS (METRICS): fields 0–2 / 65536 = 4-week load focus low aerobic,
    high aerobic, anaerobic (rises after a workout). The target ranges are not mapped (unclear).
  - 471 SLEEP_DISRUPTION_OVERNIGHT_SEVERITY (SLP_DISR): severity code of the night, stamped at the
    sleep end (only 0 seen so far).
  - ACTIVITY: 79 USER_METRICS field 15 = body battery at the start, 140 PHYSIOLOGICAL_METRICS
    field 25 = body battery at the end, field 9 = recovery time in minutes after the workout.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import fitdecode
from fitdecode import profile

PARSER_NAME = "garmin-fit"
PARSER_VERSION = "1.4.1"
# 1.1: workout heart rate, sleep assessment; 1.2: sleep_in_bed
# 1.3: skin temperature curve, heart rate zones, laps, workout respiration/stress/temperature,
#      ascent/descent and floors from MONITOR files
# 1.4: METRICS and SLP_DISR files, MONITOR altitude, body battery and recovery time per workout
# 1.4.1: readiness factors that are not available yet (level 0) are skipped

_UNKNOWN_CATEGORY = 65534
_EXERCISE_CATEGORY = profile.FIELD_TYPES["exercise_category"].enum

# Session fields kept as-is in `fit_attrs` (subset with stable, documented meaning).
_SESSION_ATTRS = (
    "total_training_effect",
    "total_anaerobic_training_effect",
    "training_load_peak",
    "total_cycles",
    "total_ascent",
    "total_descent",
    "avg_temperature",
    "min_temperature",
    "max_temperature",
    "num_laps",
    "avg_respiration_rate",
    "workout_rpe",
    "workout_feel",
    "avg_spo2",
    "avg_stress",
    "enhanced_avg_respiration_rate",
)
_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
FLOOR_M = 3.048  # Garmin: one floor = 10 ft
# record fields kept besides heart rate (at most one value per this many seconds)
_RECORD_EXTRA = (
    ("enhanced_respiration_rate", "workout_respiration_rate", "brpm"),
    ("current_stress", "workout_stress", "score"),
    ("temperature", "workout_temperature", "°C"),
)
_RECORD_EXTRA_EVERY_S = 30


@dataclass
class FitSession:
    index: int
    start_ts: datetime
    sport: str | None
    sub_sport: str | None
    sport_profile_name: str | None
    elapsed_s: float | None
    timer_s: float | None
    distance_m: float | None
    energy_kcal: float | None
    avg_hr: int | None
    max_hr: int | None
    attrs: dict = field(default_factory=dict)

    @property
    def end_ts(self) -> datetime | None:
        return self.start_ts + timedelta(seconds=self.elapsed_s) if self.elapsed_s else None


@dataclass
class FitSet:
    index: int
    set_type: str
    start_ts: datetime | None
    duration_s: float | None
    repetitions: int | None
    weight_kg: float | None
    category_candidates: list[int]
    subtype_candidates: list[int | None]
    wkt_step_index: int | None
    session_index: int = 0

    @property
    def exercise_category(self) -> str | None:
        """Name of the first candidate, which is what Gadgetbridge and Garmin display."""
        if not self.category_candidates or self.category_candidates[0] == _UNKNOWN_CATEGORY:
            return None
        n = self.category_candidates[0]
        return _EXERCISE_CATEGORY.get(n, f"category_{n}")


@dataclass
class FitSample:
    ts: datetime
    metric: str
    value: float
    unit: str | None


@dataclass
class FitParse:
    file_type: str | int | None = None
    sessions: list[FitSession] = field(default_factory=list)
    sets: list[FitSet] = field(default_factory=list)
    samples: list[FitSample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "file_type": self.file_type,
            "sessions": len(self.sessions),
            "sets": len(self.sets),
            "active_sets": sum(s.set_type == "active" for s in self.sets),
            "samples": len(self.samples),
            "warnings": self.warnings,
        }


def _get(frame: fitdecode.FitDataMessage, name: str | int):
    return frame.get_value(name) if frame.has_field(name) else None


def _utc(v) -> datetime | None:
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=UTC)


def _num(v) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def _int(v) -> int | None:
    return int(v) if isinstance(v, int | float) else None


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, tuple | list) else [v]


def _str(v) -> str | None:
    return None if v is None else str(v)  # unknown enum values arrive as plain ints


def _jsonable(v):
    if isinstance(v, int | float | str | bool) or v is None:
        return v
    return str(v)


def parse_fit(data: bytes) -> FitParse:
    """Parse one FIT file. Raises on CRC/format errors so the parse run is marked failed."""
    out = FitParse()
    sleep_assessment = sleep_start = sleep_end = created = None
    zones: dict[int, dict] = {}
    laps: list[dict] = []
    workout_metrics: list = []
    state = {"last_extra": {}, "monitor_ts": None}
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "file_id" and out.file_type is None:
                out.file_type = _get(frame, "type")
            elif frame.name == "session":
                _session(frame, out)
            elif frame.name == "set":
                _set(frame, out)
            elif frame.name == "skin_temp_overnight":
                _skin_temp(frame, out)
            elif frame.name == "record":
                _record(frame, out, state["last_extra"])
            elif frame.name == "unknown_397":
                _skin_temp_point(frame, out)
            elif frame.name in _BY_NUMBER:
                _BY_NUMBER[frame.name](frame, out)
            elif frame.name in ("unknown_79", "unknown_140"):
                workout_metrics.append(frame)
            elif frame.name == "time_in_zone":
                _time_in_zone(frame, zones)
            elif frame.name == "lap":
                _lap(frame, laps)
            elif frame.name == "monitoring":
                _monitoring(frame, out, state)
            elif frame.name == "sleep_assessment":
                sleep_assessment = frame
            elif frame.name == "event" and _get(frame, "event") == _SLEEP_EVENT:
                if _get(frame, "event_type") == "start":
                    sleep_start = _utc(_get(frame, "timestamp"))
                elif _get(frame, "event_type") == "stop":
                    sleep_end = _utc(_get(frame, "timestamp"))
            if frame.name == "file_id":
                created = _utc(_get(frame, "time_created"))
    if sleep_assessment is not None:
        _sleep_assessment(sleep_assessment, sleep_end or created, out)
    if sleep_start is not None and sleep_end is not None and sleep_end > sleep_start:
        # stamped at the sleep start; the value is the time from sleep start to sleep end
        out.samples.append(
            FitSample(sleep_start, "sleep_in_bed", (sleep_end - sleep_start).total_seconds(), "s")
        )
    _assign_sets_to_sessions(out)
    _attach_zones_and_laps(out, zones, laps)
    _attach_workout_metrics(out, workout_metrics)
    return out


def _session(frame, out: FitParse) -> None:
    start = _utc(_get(frame, "start_time"))
    if start is None:
        out.warnings.append("session without start_time skipped")
        return
    idx = _int(_get(frame, "message_index"))
    out.sessions.append(
        FitSession(
            index=idx if idx is not None else len(out.sessions),
            start_ts=start,
            sport=_str(_get(frame, "sport")),
            sub_sport=_str(_get(frame, "sub_sport")),
            sport_profile_name=_get(frame, "sport_profile_name"),
            elapsed_s=_num(_get(frame, "total_elapsed_time")),
            timer_s=_num(_get(frame, "total_timer_time")),
            distance_m=_num(_get(frame, "total_distance")),
            energy_kcal=_num(_get(frame, "total_calories")),
            avg_hr=_int(_get(frame, "avg_heart_rate")),
            max_hr=_int(_get(frame, "max_heart_rate")),
            attrs={k: _jsonable(v) for k in _SESSION_ATTRS if (v := _get(frame, k)) is not None},
        )
    )


def _set(frame, out: FitParse) -> None:
    set_type = _get(frame, "set_type")
    if set_type not in ("active", "rest"):
        out.warnings.append(f"set with unknown set_type {set_type!r} skipped")
        return
    idx = _int(_get(frame, "message_index"))
    active = set_type == "active"
    out.sets.append(
        FitSet(
            index=idx if idx is not None else len(out.sets),
            set_type=set_type,
            start_ts=_utc(_get(frame, "start_time")),
            duration_s=_num(_get(frame, "duration")),
            # Rest sets carry no reps/weight; never turn an absent value into 0.
            repetitions=_int(_get(frame, "repetitions")) if active else None,
            weight_kg=_num(_get(frame, "weight")) if active else None,
            category_candidates=[c for c in _as_list(_get(frame, "category")) if c is not None],
            subtype_candidates=_as_list(_get(frame, "category_subtype")),
            wkt_step_index=_int(_get(frame, "wkt_step_index")),
        )
    )


def _skin_temp(frame, out: FitParse) -> None:
    ts = _utc(_get(frame, "timestamp"))
    if ts is None:
        out.warnings.append("skin_temp_overnight without timestamp skipped")
        return
    for fname, metric in (
        ("nightly_value", "daily.skin_temp_nightly"),
        ("average_deviation", "daily.skin_temp_deviation"),
        ("average_7_day_deviation", "daily.skin_temp_deviation_7d"),
    ):
        v = _num(_get(frame, fname))
        if v is not None:
            out.samples.append(FitSample(ts, metric, v, "°C"))
    if not any(s.ts == ts for s in out.samples):
        # Seen on the first nights after setup (presumably while the baseline is built).
        out.warnings.append(f"skin_temp_overnight at {ts.isoformat()} has no values")


_SLEEP_EVENT = 74  # not in the public FIT profile; start/stop brackets the night in SLEEP files

_SLEEP_ASSESSMENT = (
    ("sleep_quality_score", "daily.sleep_quality_score", "score"),
    ("sleep_duration_score", "daily.sleep_duration_score", "score"),
    ("sleep_recovery_score", "daily.sleep_recovery_score", "score"),
    ("deep_sleep_score", "daily.sleep_deep_score", "score"),
    ("light_sleep_score", "daily.sleep_light_score", "score"),
    ("rem_sleep_score", "daily.sleep_rem_score", "score"),
    ("awake_time_score", "daily.sleep_awake_time_score", "score"),
    ("interruptions_score", "daily.sleep_interruptions_score", "score"),
    ("sleep_restlessness_score", "daily.sleep_restlessness_score", "score"),
    ("awakenings_count", "daily.sleep_awakenings", "count"),
    ("average_stress_during_sleep", "daily.sleep_avg_stress", "score"),
)


def _record(frame, out: FitParse, last: dict) -> None:
    ts = _utc(_get(frame, "timestamp"))
    if ts is None:
        return
    hr = _int(_get(frame, "heart_rate"))
    if hr is not None and 0 < hr < 255:
        out.samples.append(FitSample(ts, "workout_heart_rate", float(hr), "bpm"))
    for fname, metric, unit in _RECORD_EXTRA:
        v = _num(_get(frame, fname))
        if v is None or v < 0 or (metric == "workout_stress" and v > 100):
            continue
        prev = last.get(metric)
        if prev is None or (ts - prev).total_seconds() >= _RECORD_EXTRA_EVERY_S:
            out.samples.append(FitSample(ts, metric, v, unit))
            last[metric] = ts


def _fit_ts(seconds) -> datetime | None:
    return _FIT_EPOCH + timedelta(seconds=seconds) if isinstance(seconds, int) and seconds > 0 else None


def _skin_temp_point(frame, out: FitParse) -> None:
    ts, v = _fit_ts(_get(frame, 253)), _num(_get(frame, 1))  # unknown fields: by number
    if ts is not None and v is not None and -20 < v < 10:
        out.samples.append(FitSample(ts, "skin_temp_relative", v, "°C"))


def _add(out: FitParse, ts: datetime | None, metric: str, v, unit: str | None, lo=0.0, hi=100.0) -> None:
    if ts is not None and isinstance(v, int | float) and lo <= v <= hi:
        out.samples.append(FitSample(ts, metric, float(v), unit))


# 369: (field, factor) in the watch's order; the keys match insights.readiness components
READINESS_FACTORS = (
    (5, "sleep_score"),
    (8, "recovery_time"),
    (10, "acute_load"),
    (12, "hrv_status"),
    (14, "sleep_history"),
    (16, "stress_history"),
)


def _readiness(frame, out: FitParse) -> None:
    ts = _fit_ts(_get(frame, 253))
    for num, key in READINESS_FACTORS:
        if not _get(frame, num + 1):
            continue  # level 0: the factor is not available yet
        _add(out, ts, f"garmin.readiness_factor.{key}", _get(frame, num), "%")


def _sleep_demand(frame, out: FitParse) -> None:
    _add(out, _fit_ts(_get(frame, 253)), "daily.sleep_need", _get(frame, 1), "min", 60, 1440)


def _daily_sleep(frame, out: FitParse) -> None:
    end = _fit_ts(_get(frame, 11))
    _add(out, end, "daily.sleep_body_energy_start", _get(frame, 14), "score")
    _add(out, end, "daily.sleep_body_energy_end", _get(frame, 16), "score")


def _load_focus(frame, out: FitParse) -> None:
    ts = _fit_ts(_get(frame, 253))
    for num, key in ((0, "low_aerobic"), (1, "high_aerobic"), (2, "anaerobic")):
        v = _get(frame, num)
        if isinstance(v, int):
            _add(out, ts, f"load_focus.{key}", round(v / 65536, 1), None, 0, 10000)


def _sleep_disruption(frame, out: FitParse) -> None:
    _add(out, _fit_ts(_get(frame, 253)), "daily.sleep_disruption_severity", _get(frame, 0), "code", 0, 10)


def _altitude(frame, out: FitParse) -> None:
    v = _get(frame, 0)
    if isinstance(v, int):
        _add(out, _fit_ts(_get(frame, 253)), "altitude", round(v / 5 - 500, 1), "m", -500, 9000)


_BY_NUMBER = {
    "unknown_369": _readiness,
    "unknown_410": _sleep_demand,
    "unknown_384": _daily_sleep,
    "unknown_294": _load_focus,
    "unknown_471": _sleep_disruption,
    "unknown_279": _altitude,
}


def _attach_workout_metrics(out: FitParse, frames: list) -> None:
    """Body battery at start (79, stamped at the start) and end plus recovery time (140, stamped
    at the end) onto the session they belong to."""
    for frame in frames:
        ts = _fit_ts(_get(frame, 253))
        if ts is None:
            continue
        for sess in out.sessions:
            end = sess.end_ts or sess.start_ts
            if not (sess.start_ts - timedelta(minutes=2) <= ts <= end + timedelta(minutes=10)):
                continue
            if frame.name == "unknown_79":
                v = _int(_get(frame, 15))
                if v is not None and 0 <= v <= 100:
                    sess.attrs["body_energy_start"] = v
            else:
                v = _int(_get(frame, 25))
                if v is not None and 0 <= v <= 100:
                    sess.attrs["body_energy_end"] = v
                v = _int(_get(frame, 9))
                if v is not None and 0 <= v < 0xFFFF:
                    sess.attrs["recovery_time_min"] = v
            break


def _time_in_zone(frame, zones: dict) -> None:
    if _get(frame, "reference_mesg") != "session":
        return
    seconds = [
        round(x) if isinstance(x, int | float) else None for x in _as_list(_get(frame, "time_in_hr_zone"))
    ]
    bounds = [b for b in _as_list(_get(frame, "hr_zone_high_boundary")) if isinstance(b, int)]
    if not any(seconds) or not bounds:
        return
    zones[_int(_get(frame, "reference_index")) or 0] = {
        # seconds[0] is below zone 1, seconds[i] zone i up to bounds[i]
        "seconds": seconds[: len(bounds) + 1],
        "bounds": bounds,
        "max_hr": _int(_get(frame, "max_heart_rate")),
        "resting_hr": _int(_get(frame, "resting_heart_rate")),
        "calc": _str(_get(frame, "hr_calc_type")),
    }


def _lap(frame, laps: list) -> None:
    start = _utc(_get(frame, "start_time"))
    if start is None:
        return
    speed = _num(_get(frame, "enhanced_avg_speed")) or _num(_get(frame, "avg_speed"))
    laps.append(
        {
            "start": start.isoformat(),
            "elapsed_s": _num(_get(frame, "total_elapsed_time")),
            "distance_m": _num(_get(frame, "total_distance")),
            "avg_hr": _int(_get(frame, "avg_heart_rate")),
            "max_hr": _int(_get(frame, "max_heart_rate")),
            "kcal": _int(_get(frame, "total_calories")),
            "avg_speed_ms": round(speed, 3) if speed else None,
            "ascent_m": _num(_get(frame, "total_ascent")),
        }
    )


def _monitoring(frame, out: FitParse, state: dict) -> None:
    at = _utc(_get(frame, "timestamp"))
    if at is not None:
        state["monitor_ts"] = at
    else:
        base, ts16 = state["monitor_ts"], _int(_get(frame, "timestamp_16"))
        if base is None or ts16 is None:
            return
        fit_s = int((base - _FIT_EPOCH).total_seconds())
        at = base + timedelta(seconds=(ts16 - (fit_s & 0xFFFF)) & 0xFFFF)
    for fname, metric, floors in (
        ("ascent", "ascent", "floors_climbed"),
        ("descent", "descent", "floors_descended"),
    ):
        v = _num(_get(frame, fname))
        if v is None or v <= 0:
            continue
        out.samples.append(FitSample(at, metric, v, "m"))
        out.samples.append(FitSample(at, floors, round(v / FLOOR_M, 3), "floors"))


def _attach_zones_and_laps(out: FitParse, zones: dict, laps: list) -> None:
    for sess in out.sessions:
        if sess.index in zones:
            sess.attrs["hr_zones"] = zones[sess.index]
        own = [
            lap
            for lap in laps
            if datetime.fromisoformat(lap["start"]) >= sess.start_ts
            and (sess.end_ts is None or datetime.fromisoformat(lap["start"]) < sess.end_ts)
        ]
        if len(own) > 1:  # a single lap repeats the session
            sess.attrs["laps"] = own


def _sleep_assessment(frame, ts: datetime | None, out: FitParse) -> None:
    if ts is None:
        out.warnings.append("sleep_assessment without sleep end or file time skipped")
        return
    for fname, metric, unit in _SLEEP_ASSESSMENT:
        v = _num(_get(frame, fname))
        if v is not None:
            out.samples.append(FitSample(ts, metric, v, unit))


def _assign_sets_to_sessions(out: FitParse) -> None:
    if len(out.sessions) <= 1:
        return
    for s in out.sets:
        for sess in out.sessions:
            end = sess.end_ts
            if s.start_ts and sess.start_ts <= s.start_ts and (end is None or s.start_ts < end):
                s.session_index = sess.index
                break
        else:
            out.warnings.append(f"set {s.index} outside all sessions; assigned to first session")
            s.session_index = out.sessions[0].index
