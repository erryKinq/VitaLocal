"""Catalog of normalized metrics: display labels, units and the daily aggregation rule.

The aggregation rule is part of the metric's meaning (steps are summed, stress is averaged,
resting heart rate is the day's last value) and is used by the API, never guessed by the UI.
Metrics not listed here are still stored and queryable; they just have no curated label yet.
"""

from dataclasses import asdict, dataclass
from typing import Literal

Agg = Literal["sum", "avg", "min", "max", "last"]


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    unit: str | None
    daily: Agg
    group: str
    decimals: int = 0
    # "higher" / "lower" is better, or None when a direction would be a value judgement
    better: Literal["higher", "lower"] | None = None
    note: str | None = None  # caveat shown with the metric (source semantics)
    # Clock metrics: the value is a time of day in minutes after midnight, ordered from this minute
    # on (18:00 for sleep, so 23:40 comes before 00:20). Derived per day, not stored as samples.
    clock_start: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# Daily nutrient totals: from the food diary, or imported Waistline diary CSV totals.
_DIARY_NOTE = "Sum of the day's food diary; foods without this value count as 0"

_DEFS = [
    # activity
    MetricDef("steps", "Steps", "steps", "sum", "activity"),
    MetricDef("distance", "Distance", "km", "sum", "activity", decimals=2),
    MetricDef("active_energy", "Active energy", "kcal", "sum", "activity"),
    MetricDef("intensity_minutes_moderate", "Moderate intensity", "min", "sum", "activity"),
    MetricDef("intensity_minutes_vigorous", "Vigorous intensity", "min", "sum", "activity"),
    MetricDef("resting_metabolic_rate", "Resting metabolic rate", "kcal", "last", "activity"),
    MetricDef("training_load_acute", "Acute training load", None, "last", "activity"),
    MetricDef("training_load_chronic", "Chronic training load", None, "last", "activity"),
    MetricDef(
        "garmin.training_readiness",
        "Training readiness (Garmin)",
        "score",
        "last",
        "activity",
        better="higher",
        note="The watch's own score; it changes after sleep and workouts",
    ),
    MetricDef(
        "recovery_time",
        "Recovery time",
        "min",
        "last",
        "activity",
        note="Remaining as of the watch's reading",
    ),
    MetricDef("vo2max", "VO₂max", "ml/kg/min", "last", "activity", 1, better="higher"),
    *(
        MetricDef(
            f"garmin.readiness_factor.{key}",
            f"Readiness factor: {label} (Garmin)",
            "%",
            "last",
            "activity",
            better="higher",
            note="The watch's own rating of this factor (100 = no limit on readiness)",
        )
        for key, label in (
            ("sleep_score", "sleep"),
            ("recovery_time", "recovery time"),
            ("acute_load", "acute load"),
            ("hrv_status", "HRV status"),
            ("sleep_history", "sleep history"),
            ("stress_history", "stress history"),
        )
    ),
    *(
        MetricDef(
            f"load_focus.{key}",
            f"Load focus: {label}",
            None,
            "last",
            "activity",
            note="Garmin's training load of the last 4 weeks by intensity",
        )
        for key, label in (
            ("low_aerobic", "low aerobic"),
            ("high_aerobic", "high aerobic"),
            ("anaerobic", "anaerobic"),
        )
    ),
    MetricDef(
        "vo2max_daily",
        "VO₂max (24/7)",
        "ml/kg/min",
        "last",
        "activity",
        1,
        better="higher",
        note="Garmin's estimate from all-day monitoring rather than from a recorded run",
    ),
    MetricDef(
        "floors_climbed",
        "Floors climbed",
        "floors",
        "sum",
        "activity",
        better="higher",
        note="3 m of ascent = 1 floor",
    ),
    MetricDef("floors_descended", "Floors descended", "floors", "sum", "activity"),
    MetricDef("ascent", "Ascent", "m", "sum", "activity"),
    MetricDef("descent", "Descent", "m", "sum", "activity"),
    MetricDef("altitude", "Altitude", "m", "avg", "activity", note="Barometric, every 2 minutes"),
    MetricDef("race_time_5k", "Race prediction 5 km", "s", "last", "activity", better="lower"),
    MetricDef("race_time_10k", "Race prediction 10 km", "s", "last", "activity", better="lower"),
    MetricDef(
        "race_time_half_marathon", "Race prediction half marathon", "s", "last", "activity", better="lower"
    ),
    MetricDef("race_time_marathon", "Race prediction marathon", "s", "last", "activity", better="lower"),
    MetricDef("workout_respiration_rate", "Respiration during workouts", "brpm", "avg", "activity", 1),
    MetricDef("workout_stress", "Stress during workouts", "score", "avg", "activity"),
    MetricDef(
        "workout_temperature",
        "Temperature during workouts",
        "°C",
        "avg",
        "activity",
        1,
        note="Watch sensor, not body temperature",
    ),
    # heart & body
    MetricDef("heart_rate", "Heart rate", "bpm", "avg", "heart"),
    MetricDef("resting_heart_rate", "Resting heart rate", "bpm", "last", "heart", better="lower"),
    MetricDef("hrv_rmssd", "HRV (RMSSD)", "ms", "avg", "heart"),
    MetricDef("daily.hrv_last_night_avg", "HRV last night", "ms", "last", "heart"),
    MetricDef("daily.hrv_weekly_avg", "HRV 7-day average", "ms", "last", "heart"),
    MetricDef("daily.hrv_baseline_balanced_lower", "HRV balanced range (lower)", "ms", "last", "heart"),
    MetricDef("daily.hrv_baseline_balanced_upper", "HRV balanced range (upper)", "ms", "last", "heart"),
    MetricDef("stress", "Stress", "score", "avg", "body", better="lower"),
    MetricDef("body_energy", "Body energy", "score", "max", "body", better="higher"),
    MetricDef("spo2", "SpO₂", "%", "avg", "body", decimals=1),
    MetricDef("respiratory_rate", "Respiration", "brpm", "avg", "body", decimals=1),
    # sleep
    MetricDef("daily.sleep_score", "Sleep score", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_restless_moments", "Restless moments", "count", "last", "sleep"),
    MetricDef(
        "daily.sleep_need",
        "Sleep need",
        "min",
        "last",
        "sleep",
        note="Garmin's sleep need for the coming night; it rises after hard days and short nights",
    ),
    MetricDef("daily.sleep_body_energy_start", "Body energy at bedtime", "score", "last", "sleep"),
    MetricDef(
        "daily.sleep_body_energy_end", "Body energy at wake-up", "score", "last", "sleep", better="higher"
    ),
    MetricDef(
        "daily.sleep_disruption_severity",
        "Sleep disruptions",
        "code",
        "last",
        "sleep",
        better="lower",
        note="Garmin's severity of the night's sleep disruptions; 0 = none",
    ),
    MetricDef("daily.skin_temp_nightly", "Skin temperature", "°C", "last", "sleep", decimals=2),
    MetricDef("daily.skin_temp_deviation", "Skin temp. deviation", "°C", "last", "sleep", decimals=2),
    MetricDef(
        "skin_temp_relative",
        "Skin temperature (relative)",
        "°C",
        "avg",
        "sleep",
        1,
        note=(
            "Watch curve every 3 minutes relative to an undocumented reference: "
            "the course counts, not the level"
        ),
    ),
    MetricDef("nap_duration", "Naps", "min", "sum", "sleep"),
    # sleep start to sleep end of the main sleep (SLEEP FIT event), stamped at the sleep start
    MetricDef("sleep_in_bed", "Time in bed", "min", "sum", "sleep"),
    MetricDef("daily.sleep_quality_score", "Sleep quality", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_recovery_score", "Sleep recovery", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_duration_score", "Sleep duration", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_deep_score", "Deep sleep score", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_rem_score", "REM sleep score", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_light_score", "Light sleep score", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_awake_time_score", "Awake time score", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_interruptions_score", "Interruptions", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_restlessness_score", "Restlessness", "score", "last", "sleep", better="higher"),
    MetricDef("daily.sleep_awakenings", "Awakenings", "count", "last", "sleep"),
    MetricDef("daily.sleep_avg_stress", "Stress during sleep", "score", "last", "sleep", 1, better="lower"),
    MetricDef(
        "sleep.bedtime",
        "Bedtime",
        "clock",
        "last",
        "sleep",
        clock_start=18 * 60,
        note="Start of the sleep the watch recorded; counts for the evening it began",
    ),
    MetricDef(
        "sleep.wake_time",
        "Wake time",
        "clock",
        "last",
        "sleep",
        clock_start=18 * 60,
        note="End of the sleep the watch recorded, including a final awake stage",
    ),
    # nutrition: daily totals of the food diary (VitaLocal, earlier Waistline); see nutrients.py
    MetricDef("nutrition.energy", "Energy intake", "kcal", "sum", "nutrition"),
    MetricDef("nutrition.protein", "Protein", "g", "sum", "nutrition"),
    MetricDef("nutrition.carbohydrates", "Carbohydrates", "g", "sum", "nutrition"),
    MetricDef("nutrition.fat", "Fat", "g", "sum", "nutrition"),
    MetricDef(
        "nutrition.saturated_fat",
        "Saturated fat",
        "g",
        "sum",
        "nutrition",
        note=_DIARY_NOTE,
    ),
    MetricDef(
        "nutrition.sugars",
        "Sugars",
        "g",
        "sum",
        "nutrition",
        note=_DIARY_NOTE,
    ),
    MetricDef(
        "nutrition.fiber",
        "Fiber",
        "g",
        "sum",
        "nutrition",
        note=_DIARY_NOTE,
    ),
    MetricDef(
        "nutrition.salt",
        "Salt",
        "g",
        "sum",
        "nutrition",
        1,
        note=_DIARY_NOTE,
    ),
    MetricDef(
        "nutrition.caffeine",
        "Caffeine (food)",
        "mg",
        "sum",
        "nutrition",
        note=_DIARY_NOTE,
    ),
    MetricDef(
        "nutrition.alcohol",
        "Alcohol",
        "g",
        "sum",
        "nutrition",
        1,
        note=_DIARY_NOTE,
    ),
    # drinks logged in VitaLocal (WebUI, Android)
    MetricDef("nutrition.drinks", "Drinks", "ml", "sum", "nutrition"),
    MetricDef(
        "drinks.last_caffeine",
        "Last caffeine",
        "clock",
        "last",
        "nutrition",
        clock_start=0,
        note="Time of the day's last drink marked as caffeinated",
    ),
    MetricDef("weight", "Weight", "kg", "last", "body", 1),
    MetricDef("body_fat", "Body fat", "%", "last", "body", 1),
    MetricDef("body.waist", "Waist", "cm", "last", "body", 1),
    MetricDef("body.hips", "Hips", "cm", "last", "body", 1),
    MetricDef("body.neck", "Neck", "cm", "last", "body", 1),
    # manual check-ins
    MetricDef("checkin.mood", "Mood", "1–5", "avg", "journal", 1, better="higher"),
    MetricDef("checkin.energy", "Felt energy", "1–5", "avg", "journal", 1, better="higher"),
    MetricDef("checkin.rpe", "Workout RPE", "1–10", "avg", "journal", 1),
]

# Derived for habits only: share of the day's active supplements taken completely (100 = all).
SUPPLEMENTS_TAKEN = MetricDef(
    "supplements.taken",
    "All supplements taken",
    "%",
    "last",
    "supplements",
    better="higher",
    note="Share of the active supplements taken completely that day; 100 = all of them",
)
_DEFS.append(SUPPLEMENTS_TAKEN)
# Derived for habits only: minutes of recorded workouts (watch or entered by hand) on a day.
WORKOUT_MINUTES = MetricDef(
    "activities.minutes",
    "Workout minutes",
    "min",
    "sum",
    "activity",
    better="higher",
    note="Duration of the day's recorded activities; a day the watch was worn without one is 0",
)
_DEFS.append(WORKOUT_MINUTES)
# Counted only when something happened: a day the watch was worn without any is 0, not unknown.
ZERO_WHEN_ABSENT = frozenset({"floors_climbed", "floors_descended", "ascent", "descent"})

# Check-in kinds exposed as metrics (value = the check-in's numeric value).
CHECKIN_METRICS = ("mood", "energy", "rpe")
# Metrics entered in the UI, offered even before the first value exists.
# Body measurements entered by hand (`body_entry`).
BODY_METRICS = ("weight", "body_fat", "body.waist", "body.hips", "body.neck")
# Metrics entered in the UI (check-ins, drinks, food diary, body), offered before the first value.
MANUAL_METRICS = (
    tuple(f"checkin.{k}" for k in CHECKIN_METRICS)
    + ("nutrition.drinks",)
    + tuple(m.key for m in _DEFS if m.key.startswith("nutrition.") and m.key != "nutrition.drinks")
    + BODY_METRICS
)

METRICS: dict[str, MetricDef] = {m.key: m for m in _DEFS}
CLOCK_METRICS = frozenset(k for k, m in METRICS.items() if m.clock_start is not None)

# Stored unit -> display unit conversions for the catalog (value * factor).
UNIT_FACTORS: dict[str, float] = {"distance": 0.001, "nap_duration": 1 / 60, "sleep_in_bed": 1 / 60}

# Garmin sleep_level codes as stored in `sleep_stage_code`.
SLEEP_STAGES = {0: "unmeasurable", 1: "awake", 2: "light", 3: "deep", 4: "rem"}


def clock_position(metric: str, minutes: float) -> float:
    """A clock metric's value in the order of its day (minutes since `clock_start`); others as is."""
    start = METRICS[metric].clock_start if metric in METRICS else None
    return minutes if start is None else (minutes - start) % 1440


def hhmm(minutes: float) -> str:
    m = round(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def display_value(metric: str, value: float | None) -> float | None:
    if value is None:
        return None
    return value * UNIT_FACTORS.get(metric, 1.0)
